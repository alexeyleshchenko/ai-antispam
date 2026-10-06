#!/usr/bin/env python3
"""
Script to query Logfire and get counts of records for different types of tags.

This script analyzes Logfire logs to provide insights into different types of
events and their frequencies, grouped by tag categories.

USAGE:
    # Get tag counts for the last 7 days (excludes disabled moderation chats)
    python3 scripts/logfire_tag_counts.py

    # Get tag counts for the last 30 days
    python3 scripts/logfire_tag_counts.py --days 30

    # Filter for specific tag patterns (e.g., message-related tags)
    python3 scripts/logfire_tag_counts.py --filter "message_%"

    # Use mock data for testing (doesn't require Logfire access)
    python3 scripts/logfire_tag_counts.py --mock

    # Include chats with disabled moderation in the stats
    python3 scripts/logfire_tag_counts.py --include-disabled

REQUIREMENTS:
    - LOGFIRE_READ_TOKEN environment variable (for real Logfire queries)
    - Access to the project's Logfire instance
    - Python dependencies loaded via the project's environment

OUTPUT:
    - Summary by tag category (message, command, callback, etc.)
    - Detailed breakdown for each category
    - Top 10 most frequent tags with percentages
    - Total record counts and time period information
    - Information about excluded chats with disabled moderation (when applicable)
    - Moderation ratios showing spam percentages, moderation requirements, and user disagreement rates

TAG CATEGORIES:
    - message_: Message processing events
    - command_: Bot command executions
    - callback_: Inline keyboard callback handling
    - service_: Service message processing
    - bot_: Bot status updates
    - private_: Private chat message handling
    - spam_: Spam detection and handling
    - channel_: Channel-specific events
    - help_: Help system interactions
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from collections import defaultdict

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Note: This script is designed to work with MCP Logfire tools
# When running in an environment with MCP support, it will use mcp_logfire_arbitrary_query
# For standalone execution, you'll need to adapt this to use the LogfireQueryClient directly


def format_count(count: int) -> str:
    """Format count with thousands separators."""
    return f"{count:,}"


def print_header(title: str):
    """Print a formatted header."""
    print(f"\n{title}")
    print("=" * len(title))


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{title}")
    print("-" * len(title))


def calculate_moderation_ratios(tag_counts: Dict[str, int]) -> Dict[str, float]:
    """Calculate moderation-related ratios from tag counts."""
    user_approved = tag_counts.get("message_user_approved", 0)
    spam_auto_deleted = tag_counts.get("spam_auto_deleted", 0)
    spam_admins_notified = tag_counts.get("spam_admins_notified", 0)
    spam_deleted = (
        spam_auto_deleted + spam_admins_notified
    )  # Combined for backward compatibility
    known_member_skipped = tag_counts.get("message_trusted_member_skipped", 0)
    private_forward_prompt_sent = tag_counts.get("private_forward_prompt_sent", 0)

    ratios = {}

    # Ratio 1: (user approved + spam_deleted) / (user approved + spam deleted + known member skipped)
    # = percentage of user responses that needs moderation
    total_processed = user_approved + spam_deleted + known_member_skipped
    if total_processed > 0:
        ratios["moderation_percentage"] = (
            user_approved + spam_deleted
        ) / total_processed
    else:
        ratios["moderation_percentage"] = 0.0

    # Ratio 2: spam_deleted / (user approved + spam deleted)
    # = share of spam in messages from new members
    new_member_messages = user_approved + spam_deleted
    if new_member_messages > 0:
        ratios["spam_in_new_members"] = spam_deleted / new_member_messages
    else:
        ratios["spam_in_new_members"] = 0.0

    # Ratio 3: spam deleted / (user approved + spam deleted + known member skipped)
    # = share of spam in all user messages
    if total_processed > 0:
        ratios["spam_in_all_messages"] = spam_deleted / total_processed
    else:
        ratios["spam_in_all_messages"] = 0.0

    # Ratio 4: private_forward_prompt_sent / (user approved + spam deleted)
    # = how often users don't agree with the automatic spam classifier
    if new_member_messages > 0:
        ratios["user_disagreement_rate"] = (
            private_forward_prompt_sent / new_member_messages
        )
    else:
        ratios["user_disagreement_rate"] = 0.0

    return ratios


def categorize_tags(tags: List[str]) -> Dict[str, List[str]]:
    """Categorize tags by their prefixes."""
    categories = defaultdict(list)

    for tag in tags:
        if "_" not in tag:
            categories["other"].append(tag)
            continue

        prefix = tag.split("_")[0]
        categories[prefix].append(tag)

    return dict(categories)


def get_known_tags() -> List[str]:
    """Get list of known tags from the codebase."""
    return [
        # Message processing tags
        "message_user_approved",
        "message_spam_deleted",  # Legacy tag, now split into:
        "spam_auto_deleted",  # Auto-deleted spam messages
        "spam_admins_notified",  # Spam messages that were flagged for admin review
        "message_trusted_member_skipped",
        "message_insufficient_credits",
        "message_spam_check_failed",
        "message_from_group_admin_skipped",
        "message_from_channel_bot_skipped",
        "message_from_admin_skipped",
        "message_no_user_info",
        # Service message tags
        "service_message_deleted",
        "service_message_error",
        "service_message_no_rights_cleanup",
        "service_message_no_rights",
        "service_message_delete_failed",
        # Bot status tags
        "bot_started_private",
        "bot_blocked_private",
        "bot_status_private_other",
        "bot_permissions_updated",
        # Command tags
        "command_no_user_info",
        "command_no_text",
        "command_start_new_user_sent",
        "command_start_existing_user",
        "command_help_sent",
        "command_stats_sent",
        "command_stats_error",
        "command_mode_error",
        # Callback tags
        "callback_message_inaccessible",
        "callback_invalid_data",
        "callback_invalid_data_format",
        "callback_invalid_message_type",
        "callback_no_message_text",
        "callback_marked_as_not_spam",
        "callback_error_marking_not_spam",
        "callback_invalid_message",
        "callback_error_deleting_original",
        "callback_spam_message_deleted",
        "callback_error_deleting_spam",
        # Private message tags
        "private_no_user_info",
        "private_no_message_text",
        "private_message_replied",
        "private_forward_no_user_info",
        "private_forward_prompt_sent",
        # Spam example tags
        "spam_example_invalid_callback",
        "spam_example_invalid_message_type",
        "spam_example_processed",
        "spam_example_extraction_error",
        "spam_example_error",
        # Channel post tags
        "channel_post_left_channel",
        "channel_post_error",
        # Spam handling tags
        "spam_no_user_info",
        "spam_auto_deleted",
        "spam_delete_failed",
        "spam_delete_expired",
        # Help tags
        "help_back_shown",
    ]


async def generate_mock_data(
    days_back: int,
    tag_filter: Optional[str],
    sample_size: int,
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    """Generate mock data for testing purposes."""
    import random

    print(f"Generating mock tag count data ({sample_size} samples)...")

    # Get known tags and filter if needed
    all_tags = get_known_tags()
    if tag_filter:
        # Simple pattern matching for mock data
        filter_pattern = tag_filter.replace("%", "").replace("_", "")
        all_tags = [tag for tag in all_tags if filter_pattern in tag]

    if not all_tags:
        return {
            "error": f"No tags match filter pattern: {tag_filter}",
            "start_time": start_time,
            "end_time": end_time,
            "days_back": days_back,
            "tag_filter": tag_filter,
            "excluded_chats": [],
        }

    # Generate mock data with realistic distribution
    mock_data = []
    total_records = 0

    for tag in all_tags:
        # Generate counts with some tags being more common than others
        base_count = random.randint(1, sample_size // len(all_tags) + 10)

        # Make some tags more frequent (like message processing tags)
        if tag.startswith("message_"):
            base_count *= random.randint(2, 5)
        elif tag.startswith("command_"):
            base_count *= random.randint(1, 3)
        elif tag.startswith("callback_"):
            base_count *= random.randint(1, 2)

        count = max(1, base_count)
        mock_data.append({"tag": tag, "count": count})
        total_records += count

    # Sort by count descending
    mock_data.sort(key=lambda x: x["count"], reverse=True)

    return {
        "start_time": start_time,
        "end_time": end_time,
        "days_back": days_back,
        "tag_filter": tag_filter,
        "excluded_chats": [],
        "results": mock_data,
        "total_records": total_records,
        "mock_data": True,
    }


async def get_disabled_moderation_chat_ids() -> List[int]:
    """Query database for chat IDs where moderation is disabled."""
    try:
        from src.app.database.postgres_connection import get_pool, close_pool

        pool = await get_pool()
        try:
            rows = await pool.fetch(
                "SELECT group_id FROM groups WHERE moderation_enabled = false"
            )
            return [row["group_id"] for row in rows]
        finally:
            await close_pool()
    except Exception as e:
        print(f"Warning: Failed to query disabled moderation chats: {e}")
        print("Continuing without excluding disabled moderation chats...")
        return []


async def query_tag_counts(
    days_back: int = 7,
    tag_filter: Optional[str] = None,
    use_mock: bool = False,
    sample_size: int = 100,
    exclude_disabled_moderation: bool = True,
) -> Dict[str, Any]:
    """
    Query Logfire for tag counts over the specified time period.

    Args:
        days_back: Number of days to look back
        tag_filter: Optional filter for specific tag patterns (e.g., "message_%")

    Returns:
        Dict containing tag count data
    """
    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days_back)

    if use_mock:
        return await generate_mock_data(
            days_back, tag_filter, sample_size, start_time, end_time
        )

    # Get disabled moderation chat IDs if requested
    excluded_chat_ids = []
    if exclude_disabled_moderation:
        print("Loading chats with disabled moderation...")
        excluded_chat_ids = await get_disabled_moderation_chat_ids()
        if excluded_chat_ids:
            print(f"Excluding {len(excluded_chat_ids)} chats with disabled moderation")
        else:
            print("No chats found with disabled moderation")

    print(
        f"Querying Logfire for tag counts from {start_time.date()} to {end_time.date()}..."
    )

    # Build SQL query
    base_query = f"""
    SELECT
        unnest(tags) as tag,
        count(*) as count
    FROM records
    WHERE
        start_timestamp >= '{start_time.isoformat()}'
        AND start_timestamp <= '{end_time.isoformat()}'
        AND tags IS NOT NULL
        AND array_length(tags, 1) > 0
    """

    # Exclude chats with disabled moderation if any were found
    if excluded_chat_ids:
        excluded_ids_str = ", ".join(f"'{chat_id}'" for chat_id in excluded_chat_ids)
        base_query += f" AND (attributes->'update'->'message'->'chat'->>'id')::bigint NOT IN ({excluded_ids_str})"

    if tag_filter:
        # For tag filters, we need to check if any tag in the array matches the pattern
        base_query += (
            f" AND EXISTS (SELECT 1 FROM unnest(tags) as t WHERE t LIKE '{tag_filter}')"
        )

    base_query += " GROUP BY tag ORDER BY count DESC"

    try:
        # Use the existing LogfireQueryClient from the project
        from src.app.common.logfire_lookup import _get_client

        client = _get_client()

        # Run the query
        result = await asyncio.to_thread(
            client.query_json_rows,
            sql=base_query,
            min_timestamp=start_time,
            max_timestamp=end_time,
        )

        if not result or "rows" not in result:
            return {
                "error": "No data returned from Logfire query",
                "start_time": start_time,
                "end_time": end_time,
                "days_back": days_back,
                "tag_filter": tag_filter,
                "excluded_chats": excluded_chat_ids,
            }

        rows = result["rows"]
        total_records = sum(int(row.get("count", 0)) for row in rows)

        return {
            "start_time": start_time,
            "end_time": end_time,
            "days_back": days_back,
            "tag_filter": tag_filter,
            "excluded_chats": excluded_chat_ids,
            "results": rows,
            "total_records": total_records,
        }

    except Exception as e:
        print(f"Error querying Logfire: {e}")
        return {
            "error": str(e),
            "start_time": start_time,
            "end_time": end_time,
            "days_back": days_back,
            "tag_filter": tag_filter,
            "excluded_chats": excluded_chat_ids,
        }


async def display_tag_analysis(data: Dict[str, Any]):
    """Display the tag count analysis in a formatted way."""

    if "error" in data:
        print(f"❌ Error: {data['error']}")
        return

    print_header("Logfire Tag Count Analysis")
    print(f"Time Period: {data['start_time'].date()} to {data['end_time'].date()}")
    print(f"Days Back: {data['days_back']}")
    if data.get("tag_filter"):
        print(f"Tag Filter: {data['tag_filter']}")
    if data.get("excluded_chats"):
        print(
            f"Excluded Chats: {len(data['excluded_chats'])} chats with disabled moderation"
        )
    if data.get("mock_data"):
        print("Data Source: Mock Data (for testing)")
    else:
        print("Data Source: Logfire")
    print(f"Total Tagged Records: {format_count(data['total_records'])}")

    # Get all tags and their counts
    tag_counts = {row["tag"]: row["count"] for row in data["results"]}

    # Categorize tags
    categories = categorize_tags(list(tag_counts.keys()))

    print_section("Summary by Category")

    category_totals = {}
    for category, tags_in_category in categories.items():
        category_total = sum(tag_counts[tag] for tag in tags_in_category)
        category_totals[category] = category_total

        print(f"• {category.title()}: {format_count(category_total)} records")

    # Sort categories by total count
    sorted_categories = sorted(
        category_totals.items(), key=lambda x: x[1], reverse=True
    )

    print_section("Detailed Breakdown")

    for category, total in sorted_categories:
        print(f"\n{category.title()} Tags ({format_count(total)} total):")
        category_tags = categories[category]
        sorted_tags = sorted(category_tags, key=lambda x: tag_counts[x], reverse=True)

        for tag in sorted_tags:
            count = tag_counts[tag]
            percentage = (count / total) * 100 if total > 0 else 0
            print(f"  - {tag}: {format_count(count)} ({percentage:.1f}%)")

    print_section("Top 10 Most Frequent Tags")
    all_tags_sorted = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    for i, (tag, count) in enumerate(all_tags_sorted[:10], 1):
        total_percentage = (count / data["total_records"]) * 100
        print(f"{i:2d}. {tag:<35} {format_count(count):>8} ({total_percentage:.1f}%)")

    # Calculate and display moderation ratios
    print_section("Moderation Ratios")
    ratios = calculate_moderation_ratios(tag_counts)

    print("1. Messages requiring moderation:")
    print(f"   {(ratios['moderation_percentage'] * 100):.1f}%")

    print("2. Spam share in new member messages:")
    print(f"   {(ratios['spam_in_new_members'] * 100):.1f}%")

    print("3. Spam share in all user messages:")
    print(f"   {(ratios['spam_in_all_messages'] * 100):.1f}%")

    print("4. User disagreement with auto-classifier:")
    print(f"   {(ratios['user_disagreement_rate'] * 100):.1f}%")


async def main():
    parser = argparse.ArgumentParser(description="Query Logfire for tag counts")
    parser.add_argument(
        "--days", type=int, default=7, help="Number of days to look back (default: 7)"
    )
    parser.add_argument(
        "--filter",
        type=str,
        help="Filter tags by pattern (e.g., 'message_%%', 'command_%%')",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock data for testing (doesn't require Logfire connection)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Sample size for mock data generation (default: 100)",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include chats with disabled moderation in the stats (default: exclude them)",
    )

    args = parser.parse_args()

    try:
        data = await query_tag_counts(
            days_back=args.days,
            tag_filter=args.filter,
            use_mock=args.mock,
            sample_size=args.sample_size,
            exclude_disabled_moderation=not args.include_disabled,
        )
        await display_tag_analysis(data)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
