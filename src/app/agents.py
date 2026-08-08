"""pydantic-ai agents for spam classification and admin chat."""

import logging
import os
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import stop_after_attempt

from .common.utils import get_llm_http_client_timeout, get_openrouter_models

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


# OpenRouter agent pool
_openrouter_agents: Any = None
_openrouter_agent_idx: int = 0


def _get_openrouter_agents() -> Any:
    global _openrouter_agents
    if _openrouter_agents is None:
        _openrouter_agents = [
            Agent(
                _create_openrouter_model(model_name),
                output_type=SpamClassification,
                name=_openrouter_agent_name("openrouter-spam", model_name),
            )
            for model_name in get_openrouter_models()
        ]
    return _openrouter_agents


def _next_openrouter_agent() -> Agent[None, SpamClassification]:
    """Rotate to next OpenRouter agent."""
    global _openrouter_agent_idx
    agents = _get_openrouter_agents()
    _openrouter_agent_idx = (_openrouter_agent_idx + 1) % len(agents)
    return agents[_openrouter_agent_idx]


def get_openrouter_spam_agent() -> Agent[None, SpamClassification]:
    """Get current OpenRouter spam agent (round-robin)."""
    agents = _get_openrouter_agents()
    return agents[_openrouter_agent_idx]


# OpenRouter chat agent pool (plain text output)
_openrouter_chat_agents: Any = None
_openrouter_chat_agent_idx: int = 0


def _get_openrouter_chat_agents() -> Any:
    global _openrouter_chat_agents
    if _openrouter_chat_agents is None:
        _openrouter_chat_agents = [
            Agent(
                _create_openrouter_model(model_name),
                output_type=str,
                name=_openrouter_agent_name("openrouter-chat", model_name),
            )
            for model_name in get_openrouter_models()
        ]
    return _openrouter_chat_agents


def _next_openrouter_chat_agent() -> Agent[str]:
    """Rotate to next OpenRouter chat agent."""
    global _openrouter_chat_agent_idx
    agents = _get_openrouter_chat_agents()
    _openrouter_chat_agent_idx = (_openrouter_chat_agent_idx + 1) % len(agents)
    return agents[_openrouter_chat_agent_idx]


def get_openrouter_chat_agent() -> Agent[str]:
    """Get current OpenRouter chat agent (round-robin)."""
    agents = _get_openrouter_chat_agents()
    return agents[_openrouter_chat_agent_idx]


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
