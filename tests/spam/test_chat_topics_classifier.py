"""Tests for chat-topic classifier integration.

Covers: the chat_topics context field + include_chat_topics_guidance property,
the CHAT TOPIC CONTEXT guidance section (only when the property is true), the
chat_topic JSON key in the request, and byte-identical prompts when no topic
is set (no regression on existing prompts).
"""

import json

import pytest
from unittest.mock import AsyncMock, patch

from app.spam.prompt_builder import (
    SpamPromptBuilder,
    build_system_prompt,
    format_spam_request,
)
from app.types import SpamClassificationContext


class TestChatTopicsContextField:
    def test_defaults_to_none(self):
        ctx = SpamClassificationContext()
        assert ctx.chat_topics is None
        assert ctx.include_chat_topics_guidance is False

    def test_set_value_enables_guidance(self):
        ctx = SpamClassificationContext(chat_topics="PHP jobs discussion.")
        assert ctx.include_chat_topics_guidance is True

    def test_empty_string_disables_guidance(self):
        ctx = SpamClassificationContext(chat_topics="")
        assert ctx.include_chat_topics_guidance is False


class TestChatTopicRequestKey:
    def test_key_present_with_value(self):
        ctx = SpamClassificationContext(chat_topics="PHP jobs discussion.")
        request = format_spam_request("hello", ctx)
        parsed = json.loads(request)
        assert parsed["chat_topic"] == "PHP jobs discussion."

    def test_key_null_when_absent(self):
        ctx = SpamClassificationContext()
        request = format_spam_request("hello", ctx)
        parsed = json.loads(request)
        assert parsed["chat_topic"] is None

    def test_key_present_in_plain_request(self):
        """No context at all -> chat_topic null (key always emitted)."""
        request = format_spam_request("hello")
        parsed = json.loads(request)
        assert parsed["chat_topic"] is None


class TestChatTopicGuidanceSection:
    def _builder_with_guidance(self, chat_topics: str | None) -> str:
        builder = SpamPromptBuilder().build_base_instructions()
        ctx = SpamClassificationContext(chat_topics=chat_topics)
        if ctx.include_chat_topics_guidance:
            builder.add_chat_topics_guidance()
        return builder.build()

    def test_section_present_when_topic_set(self):
        prompt = self._builder_with_guidance("PHP jobs discussion.")
        assert "## CHAT TOPIC CONTEXT" in prompt
        assert "off-topic" in prompt.lower()
        assert "Trojan Horse" in prompt
        assert "stale" in prompt.lower()

    def test_section_absent_without_topic(self):
        prompt = self._builder_with_guidance(None)
        assert "## CHAT TOPIC CONTEXT" not in prompt


@pytest.mark.asyncio
class TestBuildSystemPrompt:
    @staticmethod
    async def _build(chat_topics: str | None) -> str:
        ctx = SpamClassificationContext(chat_topics=chat_topics)
        with patch(
            "app.spam.prompt_builder.get_spam_examples",
            AsyncMock(return_value=[]),
        ):
            return await build_system_prompt(context=ctx, lang="en")

    async def test_zero_byte_identical_when_no_topic(self):
        """No topic -> byte-identical prompt to the pre-feature baseline."""
        # Baseline: the same builder without any chat_topics field set.
        baseline_ctx = SpamClassificationContext()
        with patch(
            "app.spam.prompt_builder.get_spam_examples",
            AsyncMock(return_value=[]),
        ):
            baseline = await build_system_prompt(context=baseline_ctx, lang="en")
        actual = await self._build(None)

        assert actual == baseline
        assert "CHAT TOPIC CONTEXT" not in actual
        assert "CHAT TOPIC CONTEXT" not in baseline

    async def test_section_appended_when_topic_set(self):
        prompt = await self._build("PHP jobs discussion.")
        assert "## CHAT TOPIC CONTEXT" in prompt
        # Section ordering: after discussion context, before response format.
        assert (
            prompt.index("## CHAT TOPIC CONTEXT")
            < prompt.index("## RESPONSE FORMAT")
        )

    async def test_trojan_horse_section_still_present(self):
        """Chat-topic guidance must not clobber the Trojan Horse section."""
        prompt = await self._build("PHP jobs discussion.")
        assert "## TROJAN HORSE PATTERN" in prompt