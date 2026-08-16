"""Tests for chat-topic derivation: TopicSummary schema, fallback builder, and
the derive_topic_summary routing (gateway -> OpenRouter -> None)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.agents import (
    TOPIC_SUMMARY_INSTRUCTIONS,
    TopicSummary,
    derive_topic_summary,
    topic_summary_from_title,
)


class TestTopicSummarySchema:
    """Structured-output schema validation."""

    def test_valid_summary(self):
        summary = TopicSummary(
            description="A group about PHP jobs and freelance gigs.",
            short_description="PHP jobs",
        )
        assert summary.description.startswith("A group about")
        assert summary.short_description == "PHP jobs"

    def test_short_description_over_120_rejected(self):
        with pytest.raises(Exception):
            TopicSummary(
                description="x",
                short_description="Y" * 121,
            )


class TestTopicSummaryFromTitle:
    """Fallback builder — state never empty."""

    def test_normal_title(self):
        summary = topic_summary_from_title("PHP Jobs")
        assert summary.short_description == "PHP Jobs"
        assert "PHP Jobs" in summary.description

    def test_long_title_truncated_to_120(self):
        summary = topic_summary_from_title("X" * 300)
        assert len(summary.short_description) == 120

    def test_empty_title_fallback(self):
        summary = topic_summary_from_title("")
        assert summary.short_description == "Untitled chat"


class TestDeriveTopicSummary:
    """Routing: gateway first, OpenRouter rotation, None on total failure."""

    @pytest.mark.asyncio
    async def test_gateway_success_returns_summary(self):
        fake_output = TopicSummary(
            description="Gateway-derived topic.", short_description="Gateway topic"
        )
        fake_result = MagicMock()
        fake_result.output = fake_output
        agent = MagicMock()
        agent.run = AsyncMock(return_value=fake_result)

        with patch("src.app.agents.get_gateway_topic_agent", return_value=agent):
            result = await derive_topic_summary("sample text", timeout=1)

        assert result == fake_output
        agent.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gateway_failure_falls_back_to_openrouter(self):
        fake_output = TopicSummary(
            description="OpenRouter-derived topic.",
            short_description="OR topic",
        )
        fake_result = MagicMock()
        fake_result.output = fake_output

        or_agent = MagicMock()
        or_agent.run = AsyncMock(return_value=fake_result)

        with patch(
            "src.app.agents.get_gateway_topic_agent",
            side_effect=RuntimeError("gateway down"),
        ), patch(
            "src.app.agents._get_openrouter_topic_agents",
            return_value=[or_agent],
        ), patch(
            "src.app.agents.get_openrouter_topic_agent",
            return_value=or_agent,
        ):
            result = await derive_topic_summary("sample text", timeout=1)

        assert result == fake_output
        or_agent.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_models_fail_returns_none(self):
        with patch(
            "src.app.agents.get_gateway_topic_agent",
            side_effect=RuntimeError("gateway down"),
        ), patch(
            "src.app.agents._get_openrouter_topic_agents",
            return_value=[],
        ):
            result = await derive_topic_summary("sample text", timeout=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_openrouter_pool_build_failure_returns_none(self):
        """Missing OPENROUTER_API_KEY must not let an exception escape."""
        with patch(
            "src.app.agents.get_gateway_topic_agent",
            side_effect=RuntimeError("gateway down"),
        ), patch(
            "src.app.agents._get_openrouter_topic_agents",
            side_effect=ValueError("OPENROUTER_API_KEY environment variable is required"),
        ):
            result = await derive_topic_summary("sample text", timeout=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_openrouter_exhausted_returns_none(self):
        """Every OpenRouter agent fails -> None (no exception escapes)."""
        failing_agent = MagicMock()
        failing_agent.run = AsyncMock(side_effect=RuntimeError("model exploded"))

        with patch(
            "src.app.agents.get_gateway_topic_agent",
            side_effect=RuntimeError("gateway down"),
        ), patch(
            "src.app.agents._get_openrouter_topic_agents",
            return_value=[failing_agent],
        ), patch(
            "src.app.agents.get_openrouter_topic_agent",
            return_value=failing_agent,
        ), patch(
            "src.app.agents._next_openrouter_topic_agent",
            return_value=failing_agent,
        ):
            result = await derive_topic_summary("sample text", timeout=1)

        assert result is None

    def test_instructions_mention_both_fields(self):
        assert "description" in TOPIC_SUMMARY_INSTRUCTIONS
        assert "short_description" in TOPIC_SUMMARY_INSTRUCTIONS

    def test_instructions_require_label_style_short(self):
        """Short must be a compact 3-5 word label, not a compressed sentence."""
        assert "3-5 word label" in TOPIC_SUMMARY_INSTRUCTIONS

    def test_instructions_require_normal_vs_spam_boundary(self):
        """The description must carry the normal-vs-suspicious boundary — the
        actual spam-classifier signal, not just 'what is discussed'."""
        assert "NORMAL" in TOPIC_SUMMARY_INSTRUCTIONS
        assert "UNUSUAL or SUSPICIOUS" in TOPIC_SUMMARY_INSTRUCTIONS
        assert "boundary" in TOPIC_SUMMARY_INSTRUCTIONS
        assert "spam" in TOPIC_SUMMARY_INSTRUCTIONS