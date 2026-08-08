"""Spam classification: prompt building, LLM calls, response parsing."""

import logging

import logfire
from pydantic_ai import ModelSettings

from ..agents import (
    _get_openrouter_agents,
    _next_openrouter_agent,
    get_gateway_spam_agent,
    get_openrouter_spam_agent,
)
from ..common.utils import get_llm_route_timeout
from ..database import get_admin
from ..i18n import normalize_lang
from ..types import SpamClassificationContext
from .prompt_builder import build_system_prompt, format_spam_request

classification_confidence_gauge = logfire.metric_gauge("spam_score")
attempts_histogram = logfire.metric_histogram("attempts")

logger = logging.getLogger(__name__)


async def is_spam(
    comment: str,
    admin_ids: list[int] | None = None,
    context: SpamClassificationContext | None = None,
) -> tuple[bool, int, str]:
    """Classify message as spam or legitimate. Returns (is_spam, confidence, reason)."""
    ctx = context or SpamClassificationContext()

    lang = "en"
    if admin_ids:
        admin = await get_admin(admin_ids[0])
        if admin and admin.language_code:
            lang = normalize_lang(admin.language_code)

    system_prompt = await build_system_prompt(
        admin_ids=admin_ids,
        context=ctx,
        lang=lang,
    )
    user_request = format_spam_request(comment, ctx)
    user_message = (
        f"{user_request}\n\n"
        "Analyze this message and respond with JSON spam classification "
        "including is_spam, confidence, and reason."
    )
    llm_timeout = get_llm_route_timeout()
    model_settings = ModelSettings(timeout=llm_timeout)

    # Try gateway first
    try:
        with logfire.span("spam_classifier_gateway_call"):
            agent = get_gateway_spam_agent()
            result = await agent.run(
                user_message,
                instructions=system_prompt,
                model_settings=model_settings,
            )
        is_spam_result = result.output.is_spam
        confidence_result = result.output.confidence
        reason_result = result.output.reason
        classification_confidence_gauge.set(
            confidence_result if is_spam_result else -confidence_result
        )
        attempts_histogram.record(1)
        return is_spam_result, confidence_result, reason_result

    except Exception as e:  # noqa: BLE001
        with logfire.span("spam_classifier_gateway_failure"):
            logger.warning(
                f"Gateway spam classification failed: {e}, trying OpenRouter"
            )

    # OpenRouter pool with rotation
    with logfire.span("spam_classifier_openrouter_loop"):
        agents = _get_openrouter_agents()
        num_models = len(agents)

        for attempt in range(num_models):
            agent = get_openrouter_spam_agent()
            try:
                with logfire.span(f"spam_classifier_openrouter_call_{attempt + 1}"):
                    result = await agent.run(
                        user_message,
                        instructions=system_prompt,
                        model_settings=model_settings,
                    )
                is_spam_result = result.output.is_spam
                confidence_result = result.output.confidence
                reason_result = result.output.reason
                classification_confidence_gauge.set(
                    confidence_result if is_spam_result else -confidence_result
                )
                attempts_histogram.record(attempt + 1)
                return is_spam_result, confidence_result, reason_result
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"OpenRouter agent {attempt + 1}/{num_models} failed: {e}"
                )
                _next_openrouter_agent()
                continue

        raise RuntimeError("All spam classifiers failed")
