"""Spam classification: prompt building, LLM calls, response parsing."""

import asyncio
import logging

import logfire
from pydantic_ai import ModelSettings

from ..agents import (
    _get_openrouter_agents,
    _next_openrouter_agent,
    get_gateway_spam_agent,
    get_openrouter_spam_agent,
)
from ..common.llm_budget import (
    get_llm_budget_seconds,
    get_llm_gateway_enabled,
    get_llm_gateway_timeout,
)
from ..common.utils import describe_exception
from ..database import get_admin
from ..i18n import normalize_lang
from ..types import SpamClassificationContext
from .prompt_builder import build_system_prompt, format_spam_request

classification_confidence_gauge = logfire.metric_gauge("spam_score")
attempts_histogram = logfire.metric_histogram("attempts")

logger = logging.getLogger(__name__)


def _openrouter_attempt_wall() -> float:
    """The wall for one pool attempt: the WHOLE classifier budget.

    Owner order 2026-10-02: *"Your strict timeouts break the process. Remove
    them. Late classification is better than timeouts with no classification at
    all."*

    The wall this replaces was DERIVED - ``(budget - gateway) / len(models)`` -
    and never measured against a model. On the shipped config it was 16.0s while
    ``ag/gemini-3.7-flash-high``, one of the two models behind it, has a measured
    p50 of 15.9s: the wall sat on that model's median, so half its calls were
    cancelled mid-flight and the fallback paid for the same work again from
    zero.

    No fixed slice can be right, because the provider regime itself moves - the
    same model on the same production-parity prompt measured a ~3x swing across
    two consecutive days (``ag/gemini-3.7-flash-high`` p50 11,272 ms on
    2026-10-01, 15,795 ms on 2026-10-02). Each attempt now gets the whole
    budget.

    The request's own deadline is deliberately NOT the bound here. The verdict
    gate detaches this work into its own task precisely so it may finish after
    the webhook has answered 503, and a redelivery then serves the persisted
    verdict - sizing an attempt by what is left of the webhook window would
    starve exactly that late path, which is the case this order exists to fix.
    """
    return get_llm_budget_seconds()


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
    # Gateway first, but only when the hop is enabled. 2026-10-01: the
    # classification path no longer traverses the ai-gateway (owner directive —
    # collapse the timeout ladders). With gateway_enabled: false the pool below
    # IS the path; this block stays for a one-line rollback and for any consumer
    # still routing through the gateway.
    if get_llm_gateway_enabled():
        gateway_settings = ModelSettings(timeout=get_llm_gateway_timeout())
        try:
            with logfire.span("spam_classifier_gateway_call"):
                agent = get_gateway_spam_agent()
                async with asyncio.timeout(get_llm_gateway_timeout()):
                    result = await agent.run(
                        user_message,
                        instructions=system_prompt,
                        model_settings=gateway_settings,
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
                    "Gateway spam classification failed: %s, trying OpenRouter",
                    describe_exception(e),
                )

    # OpenRouter pool with rotation
    with logfire.span("spam_classifier_openrouter_loop"):
        agents = _get_openrouter_agents()
        num_models = len(agents)

        for attempt in range(num_models):
            wall = _openrouter_attempt_wall()
            agent = get_openrouter_spam_agent()
            attempt_settings = ModelSettings(timeout=wall)
            try:
                with logfire.span(f"spam_classifier_openrouter_call_{attempt + 1}"):
                    async with asyncio.timeout(wall):
                        result = await agent.run(
                            user_message,
                            instructions=system_prompt,
                            model_settings=attempt_settings,
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
                    "OpenRouter agent %d/%d failed: %s",
                    attempt + 1,
                    num_models,
                    describe_exception(e),
                )
                _next_openrouter_agent()
                continue

        raise RuntimeError("All spam classifiers failed")
