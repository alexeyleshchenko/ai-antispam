"""Guards for the private-chat provider path (issue #62).

MEASURED DEFECT, deployed container 2026-09-28 12:01Z: a member messaged the
bot, the send failed with Telegram's "message to be replied not found", and the
handler treated that as a PROVIDER failure - rotating the OpenRouter pool and
then raising "All chat providers failed". No provider can fix a missing reply
target, so that rotation could not succeed by construction.

Because the legs were also UNBOUNDED (each took the 30s route timeout, with no
cap on the total), the update ran past the 55s webhook timeout, which answers
503 and makes Telegram redeliver the update straight back into the identical
failure. Measured over 24h: 2 webhook timeouts, 2 full provider-chain
exhaustions. The member blocked the bot in the timeout second.

Both halves are guarded here:

  1. a ``TelegramBadRequest`` is TERMINAL - exactly one provider attempt, and
     the pool is never advanced;
  2. the legs are bounded by the SAME validated budget the classifier uses,
     which startup validation holds below ``system.webhook_timeout``.

Leg 2 is structural rather than behavioural on purpose: the bound is a property
of the config contract, so asserting it here is the same convention the
classifier's own guard (tests/test_classifier_fallback_budget.py) uses.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.common.llm_budget import (
    get_llm_gateway_timeout,
    get_llm_per_attempt_timeout,
    get_llm_route_timeout,
    get_openrouter_models,
    reset_llm_config_validation,
    validate_llm_config,
)
from app.common.telegram_errors import is_webhook_retryable
from app.common.utils import get_webhook_timeout
from app.handlers import private_handlers

MOD = "app.handlers.private_handlers"


def _terminal_error() -> TelegramBadRequest:
    return TelegramBadRequest(
        method=None,
        message="Bad Request: message to be replied not found",
    )


def _fake_message() -> MagicMock:
    message = MagicMock()
    message.from_user = MagicMock()
    message.from_user.id = 4242
    message.from_user.username = "tester"
    message.text = "hello"
    return message


def _patched(attempt: AsyncMock):
    """Install every side effect the handler needs, and return the rotation spy."""
    rotate = MagicMock(name="_next_openrouter_chat_agent")
    replacements = (
        (f"{MOD}.initialize_new_admin", AsyncMock()),
        (f"{MOD}.update_admin_username_if_needed", AsyncMock()),
        (f"{MOD}.save_message", AsyncMock()),
        (f"{MOD}.get_message_history", AsyncMock(return_value=[])),
        (f"{MOD}.get_spam_examples", AsyncMock(return_value=[])),
        (f"{MOD}._read_prd", MagicMock(return_value="PRD")),
        (f"{MOD}._format_spam_examples", MagicMock(return_value="")),
        (f"{MOD}._build_system_prompt", MagicMock(return_value="system")),
        (f"{MOD}._build_conversation_text", MagicMock(return_value="conversation")),
        (
            f"{MOD}.get_chat_agent",
            MagicMock(return_value=MagicMock(name="gateway-chat")),
        ),
        (
            f"{MOD}._get_openrouter_chat_agents",
            MagicMock(return_value=[MagicMock(name="m1"), MagicMock(name="m2")]),
        ),
        (
            f"{MOD}.get_openrouter_chat_agent",
            MagicMock(return_value=MagicMock(name="m1")),
        ),
        (f"{MOD}._next_openrouter_chat_agent", rotate),
        (f"{MOD}._try_provider_with_retries", attempt),
    )
    stack = ExitStack()
    for target, replacement in replacements:
        stack.enter_context(patch(target, replacement))
    return stack, rotate


class TestTerminalTelegramError:
    """A Telegram API error must not be mistaken for a provider failure."""

    async def test_gateway_terminal_error_makes_exactly_one_attempt(self):
        """The defect verbatim: one attempt, and no pool rotation."""
        attempt = AsyncMock(side_effect=_terminal_error())
        stack, rotate = _patched(attempt)
        with stack, pytest.raises(TelegramBadRequest):
            await private_handlers.handle_private_message(_fake_message())

        assert attempt.await_count == 1, (
            f"a terminal Telegram error made {attempt.await_count} provider "
            "attempts; no provider can fix a missing reply target, so every "
            "attempt after the first is a guaranteed failure"
        )
        assert rotate.call_count == 0, (
            "the OpenRouter pool was advanced for a Telegram API error, which "
            "is not a provider failure"
        )

    async def test_terminal_error_does_not_advance_the_pool(self):
        """Even reached from the pool itself, the error stops rotation.

        With the gateway hop disabled (llm.gateway_enabled: false, 2026-10-01)
        the pool IS the primary path, so a genuine provider failure advances the
        pool once - and the terminal Telegram error must not advance it again.
        """
        attempt = AsyncMock(
            side_effect=[RuntimeError("provider down"), _terminal_error()]
        )
        stack, rotate = _patched(attempt)
        with stack, pytest.raises(TelegramBadRequest):
            await private_handlers.handle_private_message(_fake_message())

        assert attempt.await_count == 2, "one provider failure then the terminal leg"
        assert rotate.call_count == 1, (
            "the pool advanced once for the genuine provider failure and must not "
            "be advanced again by the terminal Telegram error"
        )

    def test_a_terminal_error_is_not_webhook_retryable(self):
        """If it were, the 503 redelivery would re-enter the same failure."""
        assert is_webhook_retryable(_terminal_error()) is False


class TestLegsAreBounded:
    """The legs must fit inside the webhook budget, reserve included."""

    def test_derived_legs_fit_inside_the_webhook_budget(self):
        reset_llm_config_validation()
        validate_llm_config()  # the shipped config.yaml

        webhook_timeout = float(get_webhook_timeout())
        models = get_openrouter_models()
        derived_total = (
            get_llm_gateway_timeout() + len(models) * get_llm_per_attempt_timeout()
        )

        assert derived_total <= webhook_timeout, (
            f"derived leg total {derived_total}s must fit the {webhook_timeout}s "
            "webhook timeout, or the update is redelivered into the same failure"
        )

    def test_pre_fix_arithmetic_exceeded_the_webhook_budget(self):
        """The control: one route timeout per leg, with no total cap."""
        reset_llm_config_validation()
        validate_llm_config()

        webhook_timeout = float(get_webhook_timeout())
        models = get_openrouter_models()

        # Pre-fix every leg took the route timeout and nothing capped the sum.
        pre_fix_total = get_llm_route_timeout() * (1 + len(models))
        assert pre_fix_total > webhook_timeout, (
            f"pre-fix leg total {pre_fix_total}s must be shown to exceed the "
            f"{webhook_timeout}s webhook timeout"
        )


class TestRotationSurvives:
    """The fix must narrow rotation, not remove it."""

    async def test_genuine_provider_failure_still_rotates(self):
        """Both pool agents are tried; each failure advances the pool.

        With the gateway hop disabled (2026-10-01) the pool has exactly two
        agents, so a total provider outage is two attempts, not three.
        """
        attempt = AsyncMock(
            side_effect=[RuntimeError("p1"), RuntimeError("p2")]
        )
        stack, rotate = _patched(attempt)
        with stack, pytest.raises(RuntimeError, match="All chat providers failed"):
            await private_handlers.handle_private_message(_fake_message())

        assert attempt.await_count == 2, "both pool agents, gateway hop disabled"
        assert rotate.call_count == 2, "each pool failure still advances the pool"
