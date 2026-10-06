"""Tests for message processing pipeline."""

from unittest.mock import AsyncMock, patch

import pytest

from src.app.database import DELETE_PRICE
from src.app.handlers.message.pipeline import process_spam_or_approve
from tests.conftest import DEFAULT_SPAM_CONFIG


class TestProcessSpamOrApprove:
    """Test process_spam_or_approve confidence thresholds and skip_auto_delete."""

    @pytest.mark.asyncio
    async def test_not_spam_high_confidence_approves_user(
        self, mock_message, mock_message_context_result
    ):
        """Not-spam with confidence >= 90 should approve user."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.add_member",
                new_callable=AsyncMock,
            ) as mock_add_member,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG
            mock_add_member.return_value = True

            result, inserted = await process_spam_or_approve(
                mock_message,
                False,  # not spam
                90,  # high confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "message_user_approved"
            assert inserted is True
            mock_handle_spam.assert_not_called()
            mock_add_member.assert_called_once()

    @pytest.mark.asyncio
    async def test_spam_low_confidence_skip_auto_delete(
        self, mock_message, mock_message_context_result
    ):
        """Spam with confidence < 90 should call handle_spam with skip_auto_delete=True."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result = await process_spam_or_approve(
                mock_message,
                True,  # spam
                75,  # low confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            mock_handle_spam.assert_called_once()
            call_kwargs = mock_handle_spam.call_args[1]
            assert call_kwargs["skip_auto_delete"] is True

    @pytest.mark.asyncio
    async def test_spam_high_confidence_no_skip_auto_delete(
        self, mock_message, mock_message_context_result
    ):
        """Spam with confidence >= 90 should call handle_spam with skip_auto_delete=False."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result = await process_spam_or_approve(
                mock_message,
                True,  # spam
                90,  # high confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            mock_handle_spam.assert_called_once()
            call_kwargs = mock_handle_spam.call_args[1]
            assert call_kwargs["skip_auto_delete"] is False

    @pytest.mark.asyncio
    async def test_spam_100_confidence_no_skip_auto_delete(
        self, mock_message, mock_message_context_result
    ):
        """Spam with 100% confidence should call handle_spam with skip_auto_delete=False."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result = await process_spam_or_approve(
                mock_message,
                True,  # spam
                100,  # confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            mock_handle_spam.assert_called_once()
            call_kwargs = mock_handle_spam.call_args[1]
            assert call_kwargs["skip_auto_delete"] is False

    @pytest.mark.asyncio
    async def test_spam_uses_config_threshold(
        self, mock_message, mock_message_context_result
    ):
        """Custom threshold: spam with 75% confidence, threshold 80 -> skip_auto_delete=True."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = {"spam": {"high_confidence_threshold": 80}}

            await process_spam_or_approve(
                mock_message,
                True,  # spam
                75,  # confidence below threshold
                [123],
                "reason",
                mock_message_context_result,
            )

            call_kwargs = mock_handle_spam.call_args[1]
            assert call_kwargs["skip_auto_delete"] is True

    @pytest.mark.asyncio
    async def test_low_confidence_not_spam_sends_for_review(
        self, mock_message, mock_message_context_result
    ):
        """Not-spam with confidence < 90 should send for review."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.add_member",
                new_callable=AsyncMock,
            ) as mock_add_member,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = True
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG
            mock_add_member.return_value = True
            mock_handle_spam.return_value = "spam_admins_notified"

            result, inserted = await process_spam_or_approve(
                mock_message,
                False,  # not spam
                10,  # low confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "message_low_confidence_review"
            assert inserted is True
            mock_add_member.assert_called_once()
            mock_handle_spam.assert_called_once()
            call_kwargs = mock_handle_spam.call_args[1]
            assert call_kwargs["skip_auto_delete"] is True
            assert call_kwargs["is_low_confidence_not_spam"] is True
            assert call_kwargs["confidence"] == 10

    @pytest.mark.asyncio
    async def test_spam_insufficient_credits(
        self, mock_message, mock_message_context_result
    ):
        """When try_deduct_credits fails for spam, return message_insufficient_credits."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
        ):
            mock_deduct.return_value = False

            result, _ = await process_spam_or_approve(
                mock_message,
                True,  # spam
                95,  # confidence
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "message_insufficient_credits"
            mock_handle_spam.assert_not_called()


class TestSpamDeleteChargeGuards:
    """#48: never charge for a delete that did not happen."""

    @pytest.mark.asyncio
    async def test_refunds_when_delete_failed(
        self, mock_message, mock_message_context_result
    ):
        """A charged delete that Telegram refused must be refunded."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.refund_credits",
                new_callable=AsyncMock,
            ) as mock_refund,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = 777  # the paying admin's id
            mock_handle_spam.return_value = "spam_delete_failed"
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result, _ = await process_spam_or_approve(
                mock_message,
                True,  # spam
                95,  # high confidence -> auto-delete attempted
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "spam_delete_failed"
            mock_refund.assert_awaited_once_with(777, DELETE_PRICE)

    @pytest.mark.asyncio
    async def test_no_refund_on_successful_delete(
        self, mock_message, mock_message_context_result
    ):
        """Positive control: a real delete keeps the charge."""
        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.refund_credits",
                new_callable=AsyncMock,
            ) as mock_refund,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = 777
            mock_handle_spam.return_value = "spam_auto_deleted"
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result, _ = await process_spam_or_approve(
                mock_message,
                True,
                95,
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "spam_auto_deleted"
            mock_refund.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_expired_message_skips_delete_and_charge(
        self, mock_message, mock_message_context_result
    ):
        """A message older than 48h cannot be deleted: notify, never charge."""
        from datetime import UTC, datetime, timedelta

        mock_message.date = datetime.now(UTC) - timedelta(hours=49)

        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result, _ = await process_spam_or_approve(
                mock_message,
                True,
                95,
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "spam_delete_expired"
            mock_deduct.assert_not_awaited()
            assert mock_handle_spam.call_args[1]["skip_auto_delete"] is True

    @pytest.mark.asyncio
    async def test_recent_message_still_charges_and_deletes(
        self, mock_message, mock_message_context_result
    ):
        """Positive control: a 1h-old message takes the normal charge+delete path."""
        from datetime import UTC, datetime, timedelta

        mock_message.date = datetime.now(UTC) - timedelta(hours=1)

        with (
            patch(
                "src.app.handlers.message.pipeline.try_deduct_credits",
                new_callable=AsyncMock,
            ) as mock_deduct,
            patch(
                "src.app.handlers.message.pipeline.handle_spam",
                new_callable=AsyncMock,
            ) as mock_handle_spam,
            patch(
                "src.app.handlers.message.pipeline.load_config",
            ) as mock_load_config,
        ):
            mock_deduct.return_value = 777
            mock_handle_spam.return_value = "spam_auto_deleted"
            mock_load_config.return_value = DEFAULT_SPAM_CONFIG

            result, _ = await process_spam_or_approve(
                mock_message,
                True,
                95,
                [123],
                "reason",
                mock_message_context_result,
            )

            assert result == "spam_auto_deleted"
            mock_deduct.assert_awaited_once()
            assert mock_handle_spam.call_args[1]["skip_auto_delete"] is False
