"""Payment handler: a zero restore is not automatically a fault.

Measured live 2026-11-08: admin 1457796755 topped up 100 stars for a group whose
moderation had been ON for nineteen days (100 credit deductions before the
payment, 7 after). The handler warned that "the group_administrators join
matched nothing" and told them to re-add the bot — both of those stated causes
were false: the mapping existed, the bot was in the group, and moderation was
already enabled.

Fleet-wide the same reading applies to 35 of the 37 admins with groups (94.6%),
so the collapsed `restored == 0` fired a false alarm on almost the entire
population it was aimed at. The three states are now kept apart, and these arms
pin each one to its own log level.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.database.admin_operations import ModerationRestore
from src.app.handlers import payment_handlers


def _payment_message(admin_id: int = 1457796755, stars: int = 100) -> MagicMock:
    message = MagicMock()
    message.from_user = MagicMock()
    message.from_user.id = admin_id
    message.successful_payment = MagicMock()
    message.successful_payment.total_amount = stars
    return message


async def _run(restore: ModerationRestore):
    """Drive the handler with one restore outcome; return the mocked logger."""
    with (
        patch.object(
            payment_handlers,
            "record_successful_payment",
            new=AsyncMock(return_value=restore),
        ),
        patch.object(payment_handlers, "get_admin", new=AsyncMock(return_value=None)),
        patch.object(payment_handlers, "resolve_lang", return_value="en"),
        patch.object(payment_handlers, "t", return_value="ok"),
        patch.object(payment_handlers, "bot") as mock_bot,
        patch.object(payment_handlers, "logger") as mock_logger,
    ):
        mock_bot.send_message = AsyncMock()
        result = await payment_handlers.process_successful_payment(
            _payment_message()
        )
    return result, mock_logger, mock_bot


@pytest.mark.asyncio
async def test_already_on_is_info_and_does_not_tell_them_to_readd_the_bot():
    """THE REGRESSION ARM: the measured false alarm.

    Moderation was already enabled, so there was nothing to restore. This must
    log at INFO and must NOT carry the "re-add the bot" instruction, which sends
    a paying customer chasing a problem that does not exist.
    """
    restore = ModerationRestore(restored=0, already_on=1, registered_groups=1)

    result, logger, bot = await _run(restore)

    assert result == "payment_successful_processed"
    logger.warning.assert_not_called()
    logger.info.assert_called_once()
    line = logger.info.call_args.args[0]
    assert "needed no moderation restore" in line
    assert "already enabled" in line
    assert "Re-adding the bot" not in line
    assert "matched nothing" not in line
    # The numbers travel with the line, so the next incident is readable from
    # the log alone instead of being reconstructed from the database.
    assert "already_on=1" in line
    assert "registered_groups=1" in line

    # The customer still gets their confirmation: this is a success path.
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_real_restore_is_info_and_names_the_count():
    """The good path: mappings existed with moderation off, the payment flipped them."""
    restore = ModerationRestore(restored=2, already_on=0, registered_groups=2)

    _, logger, _ = await _run(restore)

    logger.warning.assert_not_called()
    logger.info.assert_called_once()
    line = logger.info.call_args.args[0]
    assert "restored moderation in 2 group(s)" in line
    assert "restored=2" in line


@pytest.mark.asyncio
async def test_no_mapping_is_still_a_warning():
    """Issue #41's real case must stay loud.

    The low-balance leave hard-deletes `group_administrators`, so a top-up made
    before the bot is re-added genuinely restores nothing and the customer has
    to re-add it. Suppressing this would resurrect the bug #41 fixed: a paying
    customer sat unmoderated for ~36 days with no line anywhere saying so.
    """
    restore = ModerationRestore(restored=0, already_on=0, registered_groups=0)

    _, logger, _ = await _run(restore)

    logger.info.assert_not_called()
    logger.warning.assert_called_once()
    line = logger.warning.call_args.args[0]
    assert "NO group_administrators rows" in line
    assert "Re-adding the bot restores moderation" in line
    assert "registered_groups=0" in line


@pytest.mark.asyncio
async def test_the_three_states_reach_three_distinct_branches():
    """A guard against a refactor collapsing the branches back into two.

    If any two states ever share a branch, the mapping from state to log level
    stops being injective and one of the three arms above goes quiet.
    """
    seen = {}
    for label, restore in (
        ("healthy", ModerationRestore(0, 3, 3)),
        ("flipped", ModerationRestore(3, 0, 3)),
        ("orphan", ModerationRestore(0, 0, 0)),
    ):
        _, logger, _ = await _run(restore)
        seen[label] = (
            "warning" if logger.warning.called else None,
            logger.info.call_args.args[0] if logger.info.called else None,
        )

    assert seen["orphan"][0] == "warning"
    assert seen["healthy"][0] is None
    assert seen["flipped"][0] is None
    assert seen["healthy"][1] != seen["flipped"][1]
