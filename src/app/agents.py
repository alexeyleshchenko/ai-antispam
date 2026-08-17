"""pydantic-ai agents for spam classification and admin chat."""

import logging
import os
from typing import Any

import httpx
import logfire
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import stop_after_attempt

from .common.utils import (
    get_llm_http_client_timeout,
    get_llm_route_timeout,
    get_openrouter_models,
)

logger = logging.getLogger(__name__)


def _openrouter_agent_name(prefix: str, model_name: str) -> str:
    return f"{prefix}-{model_name.replace('/', '-')}"


class SpamClassification(BaseModel):
    """Structured output for spam classification."""

    is_spam: bool
    confidence: int
    reason: str = Field(
        description="Reason for classification. IMPORTANT: Write in the same language as the admin's preference (Russian/English). Do NOT write in Chinese."
    )


class TopicSummary(BaseModel):
    """Structured output for chat-topic derivation.

    Full profile (`description`) is the classifier signal; `short_description`
    is the stats-safe label. Length/shape rules live in
    `TOPIC_SUMMARY_INSTRUCTIONS`.
    """

    description: str = Field(
        description="Profile of what this chat/channel is normally about."
    )
    short_description: str = Field(
        description="Stats-safe label for this chat's topic.",
        max_length=120,
    )


# Gateway configuration
GATEWAY_API_BASE = os.getenv("API_BASE")
GATEWAY_API_KEY = os.getenv("CUSTOM_GATEWAY_API_KEY")
GATEWAY_MODEL = os.getenv("CUSTOM_GATEWAY_MODEL")

# OpenRouter configuration
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")


def _create_retrying_client(
    timeout: float | None = None,
) -> httpx.AsyncClient:
    """Create httpx client with retry logic for gateway."""
    if timeout is None:
        timeout = get_llm_http_client_timeout()

    def should_retry_status(response: httpx.Response) -> None:
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=lambda e: isinstance(e, (httpx.HTTPStatusError, httpx.ConnectError)),
            wait=wait_retry_after(
                fallback_strategy=None,
                max_wait=60,
            ),
            stop=stop_after_attempt(5),
            reraise=True,
        ),
        validate_response=should_retry_status,
    )
    return httpx.AsyncClient(timeout=timeout, transport=transport)


def _create_gateway_model() -> OpenAIChatModel:
    """Create OpenAIChatModel for custom gateway."""
    if not GATEWAY_API_BASE:
        raise ValueError("API_BASE environment variable is required")
    if not GATEWAY_API_KEY:
        raise ValueError("CUSTOM_GATEWAY_API_KEY environment variable is required")
    if not GATEWAY_MODEL:
        raise ValueError("CUSTOM_GATEWAY_MODEL environment variable is required")

    client = _create_retrying_client()
    openai_client = AsyncOpenAI(
        base_url=f"{GATEWAY_API_BASE.rstrip('/')}",
        api_key=GATEWAY_API_KEY,
        http_client=client,
        max_retries=0,  # Disable SDK-level retries; AsyncTenacityTransport handles 502/503/504
    )
    return OpenAIChatModel(
        GATEWAY_MODEL,
        provider=OpenAIProvider(openai_client=openai_client),
    )


def _create_openrouter_model(model_name: str) -> OpenAIChatModel:
    """Create OpenAIChatModel for a specific OpenRouter model."""
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY environment variable is required")

    client = _create_retrying_client()
    openai_client = AsyncOpenAI(
        base_url=f"{OPENROUTER_API_BASE.rstrip('/')}",
        api_key=OPENROUTER_API_KEY,
        http_client=client,
        max_retries=0,  # Disable SDK-level retries; AsyncTenacityTransport handles 502/503/504
    )
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(openai_client=openai_client),
    )


# Gateway agent (single model, high retry count via transport)
_gateway_model: OpenAIChatModel | None = None


def get_gateway_model() -> OpenAIChatModel:
    global _gateway_model
    if _gateway_model is None:
        _gateway_model = _create_gateway_model()
    return _gateway_model


# Gateway spam agent (structured output)
_gateway_spam_agent: Any = None


def get_gateway_spam_agent() -> Any:
    global _gateway_spam_agent
    if _gateway_spam_agent is None:
        _gateway_spam_agent = Agent(
            get_gateway_model(),
            output_type=SpamClassification,
            name="gateway-spam",
        )
    return _gateway_spam_agent


# Gateway topic-summary agent (structured output)
_gateway_topic_agent: Any = None


def get_gateway_topic_agent() -> Any:
    global _gateway_topic_agent
    if _gateway_topic_agent is None:
        _gateway_topic_agent = Agent(
            get_gateway_model(),
            output_type=TopicSummary,
            name="gateway-topic",
        )
    return _gateway_topic_agent


# OpenRouter agent pools (spam / topic / chat), parameterized on prefix + output
# type so the rotation logic lives in one place. Each pool is a lazily-built,
# round-robin list of agents; getters below are thin named wrappers per domain.
_openrouter_pools: dict[str, Any] = {}
_openrouter_pool_idxs: dict[str, int] = {}


def _openrouter_pool(prefix: str, output_type: Any) -> Any:
    """Lazily build and cache the OpenRouter agent pool for a domain prefix."""
    pool = _openrouter_pools.get(prefix)
    if pool is None:
        pool = [
            Agent(
                _create_openrouter_model(model_name),
                output_type=output_type,
                name=_openrouter_agent_name(prefix, model_name),
            )
            for model_name in get_openrouter_models()
        ]
        _openrouter_pools[prefix] = pool
        _openrouter_pool_idxs[prefix] = 0
    return pool


def _next_openrouter(prefix: str, output_type: Any) -> Any:
    """Rotate to the next agent in a domain's pool."""
    agents = _openrouter_pool(prefix, output_type)
    _openrouter_pool_idxs[prefix] = (_openrouter_pool_idxs[prefix] + 1) % len(agents)
    return agents[_openrouter_pool_idxs[prefix]]


def _current_openrouter(prefix: str, output_type: Any) -> Any:
    """Get the current agent in a domain's pool (round-robin)."""
    agents = _openrouter_pool(prefix, output_type)
    return agents[_openrouter_pool_idxs[prefix]]


def _get_openrouter_agents() -> Any:
    """Get the OpenRouter spam agent pool."""
    return _openrouter_pool("openrouter-spam", SpamClassification)


def _next_openrouter_agent() -> Agent[None, SpamClassification]:
    """Rotate to next OpenRouter spam agent."""
    return _next_openrouter("openrouter-spam", SpamClassification)


def get_openrouter_spam_agent() -> Agent[None, SpamClassification]:
    """Get current OpenRouter spam agent (round-robin)."""
    return _current_openrouter("openrouter-spam", SpamClassification)


def _get_openrouter_topic_agents() -> Any:
    """Get the OpenRouter topic agent pool."""
    return _openrouter_pool("openrouter-topic", TopicSummary)


def _next_openrouter_topic_agent() -> Agent[None, TopicSummary]:
    """Rotate to next OpenRouter topic agent."""
    return _next_openrouter("openrouter-topic", TopicSummary)


def get_openrouter_topic_agent() -> Agent[None, TopicSummary]:
    """Get current OpenRouter topic agent (round-robin)."""
    return _current_openrouter("openrouter-topic", TopicSummary)


def _get_openrouter_chat_agents() -> Any:
    """Get the OpenRouter chat agent pool (plain text output)."""
    return _openrouter_pool("openrouter-chat", str)


def _next_openrouter_chat_agent() -> Agent[str]:
    """Rotate to next OpenRouter chat agent."""
    return _next_openrouter("openrouter-chat", str)


def get_openrouter_chat_agent() -> Agent[str]:
    """Get current OpenRouter chat agent (round-robin)."""
    return _current_openrouter("openrouter-chat", str)


# Chat agent (plain text, gateway first, fallback to OpenRouter)
_chat_agent: Agent[str] | None = None


def get_chat_agent() -> Agent[str]:
    """Get chat agent (uses gateway model, plain text output)."""
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = Agent(
            get_gateway_model(),
            output_type=str,
            name="gateway-chat",
        )
    return _chat_agent


# ---------------------------------------------------------------------------
# Chat-topic derivation (TopicSummary)
# Same routing as the spam classifier: gateway first, then OpenRouter rotation.
# Never raises — returns None when every model failed, so the scan service can
# apply its own fallback (title on first scan, keep existing on re-scan).
# ---------------------------------------------------------------------------

TOPIC_SUMMARY_INSTRUCTIONS = (
    "Summarize what this chat or channel is normally about. "
    "Describe the recurring subjects, tone, and typical discussions. "
    "Return JSON with 'description' (2-4 sentences) and "
    "'short_description' (one line, max 120 characters)."
)


async def derive_topic_summary(
    sample_text: str,
    *,
    timeout: float | None = None,
) -> TopicSummary | None:
    """Derive a chat-topic summary from sampled message text.

    Routes OpenRouter free pool FIRST (derivation is a manual /scan — no latency
    SLA, non-critical, fallback = title), then the gateway model as the safety
    net, then None. Returns None if every model fails (no exception escapes).
    The caller decides the fallback (chat title on first scan, keep existing on
    re-scan).
    """
    if timeout is None:
        timeout = get_llm_route_timeout()
    model_settings = ModelSettings(timeout=timeout)

    # OpenRouter free pool with rotation first (free-first routing, efficiency
    # review 2026-08-17): derivation is manual + non-critical, so a flaky free
    # model failing just falls through to the gateway.
    try:
        agents = _get_openrouter_topic_agents()
    except Exception as e:  # noqa: BLE001
        logfire.exception("topic_derivation_openrouter_pool_failure")
        logger.error(f"Failed to build OpenRouter topic agent pool: {e}")
        agents = []
    num_models = len(agents)

    for attempt in range(num_models):
        agent = get_openrouter_topic_agent()
        try:
            with logfire.span(f"topic_derivation_openrouter_call_{attempt + 1}"):
                result = await agent.run(
                    sample_text,
                    instructions=TOPIC_SUMMARY_INSTRUCTIONS,
                    model_settings=model_settings,
                )
            return result.output
        except Exception as e:  # noqa: BLE001
            logfire.exception("topic_derivation_openrouter_failure")
            logger.warning(
                f"OpenRouter topic agent {attempt + 1}/{num_models} failed: {e}"
            )
            _next_openrouter_topic_agent()
            continue

    # Gateway as the safety net — only reached when the whole free pool failed.
    try:
        with logfire.span("topic_derivation_gateway_call"):
            agent = get_gateway_topic_agent()
            result = await agent.run(
                sample_text,
                instructions=TOPIC_SUMMARY_INSTRUCTIONS,
                model_settings=model_settings,
            )
        return result.output
    except Exception as e:  # noqa: BLE001
        logfire.exception("topic_derivation_gateway_failure")
        logger.warning(f"Gateway topic derivation failed: {e}")

    logger.error("All topic derivation agents failed")
    return None


def topic_summary_from_title(title: str) -> TopicSummary:
    """Build a TopicSummary from a chat title (fallback, state never empty).

    Used when a first scan cannot derive a topic — the title is the best
    available description. Short form is truncated to 120 chars.
    """
    short = title.strip()[:120] if title.strip() else "Untitled chat"
    return TopicSummary(
        description=f"The chat is titled '{title.strip() or 'untitled'}'. "
        "Detailed topic profile could not be derived — this is the fallback "
        "description based on the chat title alone.",
        short_description=short,
    )
