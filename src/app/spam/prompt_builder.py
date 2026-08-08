"""
Spam classification prompt building utilities.

This module handles the construction of LLM prompts for spam classification,
including system instructions, context guidance, response formats, and examples.

The module provides:
- SpamPromptBuilder: A fluent builder for constructing prompts with various guidance sections
- build_system_prompt(): Async function to build complete prompts with examples from database
- format_spam_request(): Formats live classification requests for the user message (prose + BEGIN/END markers).
- format_spam_example_input(): Dict for few-shot DB examples (same logical fields as live request).
- _format_context_section(): Internal helper for consistent context section formatting

Prompt Structure:
1. Base instructions (what spam classification is)
2. Context-specific guidance sections (linked channels, stories, account signals, replies)
3. Response format specification
4. Spam classification examples from database
"""

import json
import logging
from typing import Any

import logfire

from ..database.spam_examples import get_spam_examples
from ..i18n import t
from ..types import ContextResult, SpamClassificationContext
from .account_signals import build_account_signals_body

logger = logging.getLogger(__name__)


@logfire.no_auto_trace  # Called per optional field on every example; avoids hot-path span noise
def _norm_opt(value: Any) -> str | None:
    """Coerce value to stripped string, or None if empty."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


@logfire.no_auto_trace
def format_spam_example_input(example: dict[str, Any]) -> dict[str, Any]:
    """
    Build a dict for a spam_examples DB row (few-shot input only).

    Keys align with live `format_spam_request` sections: message, user profile,
    linked channel fragment, stories, reply thread text, account_signals line(s).
    """
    return {
        "message": example.get("text") or "",
        "user_name": _norm_opt(example.get("name")),
        "user_bio": _norm_opt(example.get("bio")),
        "linked_channel": _norm_opt(example.get("linked_channel_fragment")),
        "stories": _norm_opt(example.get("stories_context")),
        "reply_context": _norm_opt(example.get("reply_context")),
        "account_signals": _norm_opt(example.get("account_signals_context")),
    }


@logfire.no_auto_trace
class SpamPromptBuilder:
    """Builder for spam classification prompts."""

    def __init__(self):
        self.prompt_parts = []

    def build_base_instructions(self, lang: str = "en") -> SpamPromptBuilder:
        """Add the core spam classification instructions."""
        explanation_hint = t(lang, "prompt.explanation_hint")
        self.prompt_parts.append(f"""You are a spam message classifier for Telegram groups.

Your task: Analyze user messages and determine if they are spam or legitimate.
You will receive context information as JSON (User Bio, Linked Channel, User Stories, Account Signals, Reply Context).

IMPORTANT: Do not classify the context information as spam. Only classify the message.

Few-shot examples and live requests use the same JSON structure with keys: `message`, `user_name`, `user_bio`, `linked_channel`, `stories`, `reply_context`, `account_signals`.

{explanation_hint}""")
        return self

    def add_user_info_guidance(self) -> SpamPromptBuilder:
        """Add guidance for analyzing user profile information (name, bio)."""
        self.prompt_parts.append("""
## USER INFORMATION ANALYSIS
Examine the user's name and bio for professional labels or hidden promotions.

HIGH SPAM INDICATORS:
- NAME: Professional titles ("Psychologist", "Coach", "Investor", "Realtor"); income or offer in display name ("100к за 7 дней", "50к/мес в общаге", "Комменты без усилий", "Экспертиза бренда X"); links directly in the user's display name.
- BIO: Links to Telegram bots (including "helpful" bots or "free tools" — often lead-gen funnels), external sites, t.me/ links to channels or bots, "consultation" offers, or phrases like "чекай канал в био".""")
        return self

    def add_message_metadata_guidance(self) -> SpamPromptBuilder:
        """Add guidance for message metadata signals (via_bot, inline keyboard URLs)."""
        self.prompt_parts.append("""
## MESSAGE METADATA SIGNALS

The following metadata fields may appear in the message:

- [VIA_BOT]: If present, the message was sent via an inline bot (e.g., PostBot). Humans rarely post via inline bots — this is a strong spam indicator, especially when combined with promotional content or external links.
- [INLINE_KEYBOARD_URLS]: URLs from inline keyboard buttons attached to the message. Spam messages often hide malicious links behind innocent-looking buttons (e.g., "Подробно" leading to phishing sites like fake government services). Treat these URLs as part of the message content for classification.""")
        return self

    def add_trojan_horse_guidance(self) -> SpamPromptBuilder:
        """Add Trojan Horse pattern guidance (clean message + dirty profile)."""
        self.prompt_parts.append("""
## TROJAN HORSE PATTERN (Critical)
When the message looks innocent or on-topic BUT the profile has strong spam indicators, this is Trojan Horse spam.

- Clean message + dirty profile can be SPAM when profile evidence is truly strong. The goal is to drive profile clicks, not add value.
- Strong profile indicators: bot link in bio, offer/income in name, photo_age=0mo or unknown.
- A "relevant" or "expert-looking" comment from such a profile is HIGH SPAM — the comment is bait.
- Do NOT let "message is relevant to reply" override profile indicators when profile has bot links or promotional name.

SIGNAL HIERARCHY: Profile indicators (name, bio, stories, profile photo age, Telegram Premium in Account Signals) can outweigh message content when they are genuinely strong (e.g. bot in bio, promotional name, gasket channel, story bait). Do NOT treat the absence of linked channel, absence of stories, and photo_age=unknown alone as multiple strong indicators — many legitimate users have empty auxiliary fields.

- Missing stories, no linked channel, and photo_age=unknown are common for ordinary accounts; stack them only with real spam signals in the message or profile (bots, offers, etc.), not as standalone proof of a "coordinated" or fake account.""")
        return self

    def add_linked_channel_guidance(self) -> SpamPromptBuilder:
        """Add guidance for analyzing linked channel context."""
        self.prompt_parts.append("""
## LINKED CHANNEL ANALYSIS
This section contains information about a channel linked to the user's profile.

Key metrics to evaluate:
- subscribers: Number of channel subscribers
- total_posts: Total posts ever published
- age_delta: Channel age in months (format: "11mo")
- recent_posts: Content from recent channel posts (if available)

GASKET (PROXY) CHANNEL: When subscribers < 5, total_posts < 5, age_delta=0mo — likely a "gasket" (one-post proxy channel). Strong spam indicator.

CONTENT ANALYSIS: Examine recent_posts for spam indicators like:
- Pornographic content
- Advertising or promotions
- Scams or fraudulent offers
- Spam patterns

If recent_posts contain suspicious content, this is a STRONG spam indicator,
even if the current message appears innocent. Porn channels often use innocent comments
to drive traffic to their profiles.""")
        return self

    def add_stories_guidance(self) -> SpamPromptBuilder:
        """Add guidance for analyzing user stories context."""
        self.prompt_parts.append("""
## USER STORIES ANALYSIS
This section contains content from the user's active profile stories.

Spammers frequently use stories to hide promotional content, links, or scam offers
while posting "clean" comments to lure people into viewing their profile.

Flag as HIGH SPAM if stories contain:
- Advertising links or promotions
- Calls to join channels or follow profiles
- Money-making offers, crypto, or investment schemes
- Links to other channels or external sites

This is a strong spam indicator even if the message itself appears legitimate.""")
        return self

    def add_account_signals_guidance(self) -> SpamPromptBuilder:
        """Add guidance for profile photo age and Telegram Premium (Account Signals section)."""
        self.prompt_parts.append("""
## ACCOUNT SIGNALS ANALYSIS
This section combines profile photo age (photo_age=…mo or photo_age=unknown) and optionally is_premium=true/false from Telegram.

Profile photo age is useful because spammers often use new accounts with new or missing photos.

Risk assessment (photo age alone — combine with message and other profile signals):
- photo_age=unknown OR no photo: elevated suspicion, but NOT sufficient for high-confidence spam by itself on a short, on-topic, non-bait message.
- photo_age=0mo: high suspicion for spam when combined with other promotional or bot signals.
- photo_age=1mo to 3mo: medium suspicion
- photo_age > 12mo: low suspicion from photo age alone

Telegram Premium (is_premium=true): weak legitimacy signal — paid accounts are somewhat less typical of disposable spam-only socks, but premium does NOT rule out spam. Do not argue "typical fake/new spam account" based only on empty stories, no linked channel, and unknown photo_age when is_premium=true unless strong indicators exist elsewhere (name, bio, bot links, bait in message, etc.).""")
        return self

    def add_reply_context_guidance(self) -> SpamPromptBuilder:
        """Add guidance for analyzing reply context."""
        self.prompt_parts.append("""
## DISCUSSION CONTEXT ANALYSIS
The user message may be a reply to another post. There are two types of reply context:

1. **REPLY**: A direct reply to another message in the same chat. The content of that original message is provided in the "REPLY CONTEXT" section.

2. **REPLY TO EXTERNAL SOURCE**: A reply to a message from a DIFFERENT Telegram channel or chat. This is marked with `[REPLY TO EXTERNAL SOURCE from "Channel Title"]` prefix. The quoted post text follows after the prefix.

The content of the original post is provided in the "REPLY CONTEXT" section.

CRITICAL INSTRUCTION:
1. The "REPLY CONTEXT" (both REPLY and REPLY TO EXTERNAL SOURCE) is NOT the message you are classifying.
2. It often contains the spam message that the user is replying to (e.g. asking a question about a spam offer).
3. DO NOT classify the user's message as spam just because the "REPLY CONTEXT" is spam.
4. ONLY use this context to check if the user's reply is RELEVANT to the conversation.
5. "Relevant to discussion" alone does NOT mean legitimate. If the profile has strong spam indicators (bot in bio, promotional name, new account), treat even relevant comments as high-risk Trojan Horse.
6. For REPLY TO EXTERNAL SOURCE specifically: the quoted text comes from another Telegram channel's post. A user responding to promotional content from an external channel with what looks like an innocent question is a HIGH-RISK pattern — treat this as a potential coordinated spam setup even if the individual message seems benign. Consider the origin channel's reputation: promotional content from an unknown or low-quality channel raises the concern further.
7. When REPLY CONTEXT is clearly a commercial or channel promo and the message to classify is only a polite, on-topic request for details (scheme, steps, how it works) without DM bait, links in the message, or self-promo in name/bio — do NOT classify as high-confidence spam based on speculation about "coordinated campaigns", "fake interest", or stacking weak empty-field signals alone. Require concrete spam cues in the message or strong profile spam indicators.
8. High-confidence spam (e.g., 90+) requires clear evidence: concrete message-level spam cues OR multiple strong profile cues. Do not assign high confidence from weak absence signals alone.

HIGH SPAM INDICATOR: User replies that are completely unrelated to the discussion topic.

Some signs of irrelevant replies:
- Reply ignores the main topic of the original post
- Shifts to personal topics (books, movies, hobbies) with no connection
- Generic phrases like "interesting" or "I agree" without specific reference
- Self-promotion disguised as "helpful advice" on unrelated topics
- Messages in a different language than the discussion
""")
        return self

    def add_knowledge_sharing_guidance(self) -> SpamPromptBuilder:
        """Add guidance for detecting bait based on sharing materials or knowledge."""
        self.prompt_parts.append("""
## KNOWLEDGE SHARING & BAIT DETECTION
A frequent spam tactic is offering "free" materials to lure users into private messages or external channels.

HIGH SPAM INDICATORS:
- BAIT OFFERS: "I have a free book/course/intensive", "I can share my material", "Write to me and I'll send you the link".
- CONCRETE PHRASES (Russian): "пишите, скину бесплатно", "Могу скинуть", "пишите, скину за спасибо", "скину материал за благодарность".
- CONTEXT LURES: "архив с курсом", "курс по трейдингу", "материал" — these lure users to private chat.
- VAGUE "HELP": Offering help or sharing experience in a way that requires leaving the current discussion.
- "KARMA" BAIT: Using phrases like "giving away for free", "believing in karma", or "just want to help" to appear altruistic while posting advertisements.

Genuine human sharing is usually direct or occurs naturally within the conversation flow without being the primary purpose of the message.""")
        return self

    def add_ai_generated_content_guidance(self) -> SpamPromptBuilder:
        """Add guidance for detecting AI-generated content and unusual emoji usage."""
        self.prompt_parts.append("""
## AI-GENERATED CONTENT & EMOJI DETECTION
A major spam indicator is the use of AI to generate comments that appear "clean" but add no value. This is often combined with unusual emoji patterns.

HIGH SPAM INDICATORS:

- Message is a generic rephrasing or summary of the "REPLY CONTEXT" (e.g., "This post discusses...", "Basically, the writer says...").
- Paraphrasing the post without adding insight — just ticking a "relevant reply" checkbox.
- Zero unique contribution, personal opinion, or genuine human insight.
- Spammers use Telegram custom emojis to bypass filters. You see it as an incomprehensible stream of emojis.

These AI signatures are strong indicators of spam REGARDLESS of whether the profile has promotional links
in stories or linked channels. The goal of such posts is to lure users to a bot-controlled profile.""")
        return self

    def add_response_format(self, lang: str = "en") -> SpamPromptBuilder:
        """Add the required response format specification."""
        reason_format = t(lang, "prompt.reason_format")
        self.prompt_parts.append(f"""
## RESPONSE FORMAT
Always respond with valid JSON in this exact format:
{{
    "is_spam": true/false,
    "confidence": 0-100,
    "reason": "{reason_format}"
}}

Few-shot examples are provided as a JSON array under the `examples` key. Each example has `input` (the message context) and `label` (is_spam and confidence).

Few-shot labels omit `reason` (not stored in the database). Your real response to this task must always include all three keys, including `reason`, as required by the API schema.

Confidence calibration policy: use medium confidence for ambiguous cases with mixed/weak signals; reserve very high confidence for clear evidence.

## SPAM CLASSIFICATION EXAMPLES""")
        return self

    async def add_spam_examples(
        self, admin_ids: list[int] | None = None
    ) -> SpamPromptBuilder:
        """Add spam examples from the database as unified JSON."""
        try:
            db_examples = await get_spam_examples(admin_ids)

            examples_list = []
            for example in db_examples:
                is_spam_ex = example["score"] > 0
                confidence_ex = abs(example["score"])
                examples_list.append(
                    {
                        "input": format_spam_example_input(example),
                        "label": {"is_spam": is_spam_ex, "confidence": confidence_ex},
                    }
                )

            examples_json = json.dumps(
                {"examples": examples_list},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.prompt_parts.append(examples_json)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to load spam examples for prompt: {e}")

        return self

    def build(self) -> str:
        """Build the complete prompt."""
        return "\n".join(self.prompt_parts)


async def build_system_prompt(
    admin_ids: list[int] | None = None,
    context: SpamClassificationContext | None = None,
    lang: str = "en",
) -> str:
    """
    Build a complete spam classification system prompt.

    Args:
        admin_ids: Optional list of admin IDs for personalized examples
        context: Optional spam classification context for prompt guidance flags
        lang: Language for explanation (ru/en)

    Returns:
        Complete system prompt string
    """
    builder = SpamPromptBuilder().build_base_instructions(lang=lang)

    builder.add_user_info_guidance()
    builder.add_message_metadata_guidance()
    builder.add_trojan_horse_guidance()

    if context is None:
        context = SpamClassificationContext()

    if context.include_linked_channel_guidance:
        builder.add_linked_channel_guidance()
    if context.include_stories_guidance:
        builder.add_stories_guidance()
    if context.include_account_signals_guidance:
        builder.add_account_signals_guidance()
    if context.include_reply_guidance:
        builder.add_reply_context_guidance()
    if context.include_ai_detection_guidance:
        builder.add_ai_generated_content_guidance()
        # Knowledge sharing is often linked with AI content or generic bait
        builder.add_knowledge_sharing_guidance()

    builder.add_response_format(lang=lang)

    # Add examples (async operation)
    await builder.add_spam_examples(admin_ids)
    return builder.build()


@logfire.no_auto_trace
def _context_result_to_str(
    context_result: ContextResult | None,
    empty_msg: str,
) -> str | None:
    """Convert a ContextResult to a string or null for JSON serialization."""
    if context_result is None:
        return None
    status = context_result.status.name
    if status == "SKIPPED":
        return None
    if status == "EMPTY":
        return empty_msg
    if status == "FAILED":
        return f"verification failed: {context_result.error}"
    if status == "FOUND":
        c = context_result.content
        if c is not None and hasattr(c, "to_prompt_fragment"):
            return c.to_prompt_fragment()
        return str(c) if c else None
    return None


@logfire.no_auto_trace
def format_spam_request(
    text: str,
    context: SpamClassificationContext | None = None,
) -> str:
    """
    Format a spam classification request as JSON for the LLM.

    The returned JSON has keys matching few-shot example inputs:
    message, user_name, user_bio, linked_channel, stories, reply_context, account_signals.

    Args:
        text: Message text to classify
        context: Spam classification context with all optional context data

    Returns:
        str: JSON string with classification request data
    """
    if context is None:
        context = SpamClassificationContext()

    linked_channel_str = _context_result_to_str(
        context.linked_channel, "no channel linked"
    )
    stories_str = _context_result_to_str(context.stories, "no stories posted")

    if context.account_signals_snapshot is not None:
        account_signals_str = context.account_signals_snapshot.strip() or None
    else:
        acc_body = build_account_signals_body(context)
        account_signals_str = acc_body or None

    if context.reply is None:
        reply_context_str = None
    elif context.reply == "[EMPTY]":
        reply_context_str = "no reply context"
    else:
        reply_context_str = context.reply

    request_dict = {
        "message": text,
        "user_name": context.name or None,
        "user_bio": context.bio or None,
        "linked_channel": linked_channel_str,
        "stories": stories_str,
        "reply_context": reply_context_str,
        "account_signals": account_signals_str,
    }

    return json.dumps(request_dict, ensure_ascii=False, separators=(",", ":"))
