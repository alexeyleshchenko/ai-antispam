#!/usr/bin/env python3
"""
Script to find Logfire traces for a specific user by username.

This script queries Logfire to find all traces where the span name
contains the specified username, which indicates interactions from that user.

USAGE:
    # Find traces for user 'sveta_vin' in the last 7 days
    python3 scripts/find_user_traces.py sveta_vin

    # Find traces for a user in the last 30 days
    python3 scripts/find_user_traces.py sveta_vin --days 30

REQUIREMENTS:
    - LOGFIRE_READ_TOKEN environment variable
    - Access to the project's Logfire instance
    - Python dependencies loaded via the project's environment

OUTPUT:
    - List of traces with timestamps, span names, tags, and relevant metadata
    - Summary of user activity
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any

# Add project root to Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def format_datetime(dt_str: str) -> str:
    """Format ISO datetime string to readable format."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return dt_str


def print_header(title: str):
    """Print a formatted header."""
    print(f"\n{title}")
    print("=" * len(title))


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{title}")
    print("-" * len(title))


async def query_user_traces(
    username: str,
    days_back: int = 7,
) -> Dict[str, Any]:
    """
    Query Logfire for traces from a specific user.

    Args:
        username: Username to search for (span name will contain this)
        days_back: Number of days to look back

    Returns:
        Dict containing trace data
    """
    # Calculate time range
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days_back)

    print(
        f"Querying Logfire for traces containing '{username}' from {start_time.date()} to {end_time.date()}..."
    )

    # Build SQL query to find spans with the username in the span name
    sql = f"""
    SELECT
        span_name,
        start_timestamp,
        end_timestamp,
        tags,
        attributes->'update'->'message'->'chat'->>'title' as chat_title,
        attributes->'update'->'message'->'chat'->>'id' as chat_id,
        attributes->'update'->'message'->>'text' as message_text,
        attributes->'update'->'message'->'from'->>'id' as user_id,
        attributes->'update'->'message'->'from'->>'first_name' as first_name,
        attributes->'update'->'message'->'from'->>'username' as message_username
    FROM records
    WHERE
        start_timestamp >= '{start_time.isoformat()}'
        AND start_timestamp <= '{end_time.isoformat()}'
        AND span_name LIKE '%{username}%'
    ORDER BY start_timestamp DESC
    """

    try:
        # Use the existing LogfireQueryClient from the project
        from src.app.common.logfire_lookup import _get_client

        client = _get_client()

        # Run the query
        result = await asyncio.to_thread(
            client.query_json_rows,
            sql=sql,
            min_timestamp=start_time,
            max_timestamp=end_time,
        )

        if not result or "rows" not in result:
            return {
                "error": "No data returned from Logfire query",
                "username": username,
                "start_time": start_time,
                "end_time": end_time,
                "days_back": days_back,
            }

        rows = result["rows"]
        total_traces = len(rows)

        return {
            "username": username,
            "start_time": start_time,
            "end_time": end_time,
            "days_back": days_back,
            "traces": rows,
            "total_traces": total_traces,
        }

    except Exception as e:
        print(f"Error querying Logfire: {e}")
        return {
            "error": str(e),
            "username": username,
            "start_time": start_time,
            "end_time": end_time,
            "days_back": days_back,
        }


def display_trace_analysis(data: Dict[str, Any]):
    """Display the trace analysis in a formatted way."""

    if "error" in data:
        print(f"❌ Error: {data['error']}")
        return

    print_header(f"Logfire Traces for User: {data['username']}")
    print(f"Time Period: {data['start_time'].date()} to {data['end_time'].date()}")
    print(f"Days Back: {data['days_back']}")
    print(f"Total Traces Found: {data['total_traces']}")

    if data["total_traces"] == 0:
        print("No traces found for this user in the specified time period.")
        return

    traces = data["traces"]

    # Group traces by date
    traces_by_date = {}
    tag_counts = {}
    chat_counts = {}

    for trace in traces:
        # Group by date
        date = trace["start_timestamp"][:10]  # YYYY-MM-DD
        if date not in traces_by_date:
            traces_by_date[date] = []
        traces_by_date[date].append(trace)

        # Count tags
        tags = trace.get("tags", [])
        for tag in tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

        # Count chats
        chat_title = (
            trace.get("chat_title") or f"Chat ID: {trace.get('chat_id', 'Unknown')}"
        )
        chat_counts[chat_title] = chat_counts.get(chat_title, 0) + 1

    print_section("Activity Summary")

    print(f"Active in {len(chat_counts)} chat(s):")
    for chat, count in sorted(chat_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {chat}: {count} traces")

    print(f"\nTag breakdown ({len(tag_counts)} unique tags):")
    for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / data["total_traces"]) * 100
        print(f"  • {tag}: {count} ({percentage:.1f}%)")

    print_section("Recent Traces (Last 10)")

    for i, trace in enumerate(traces[:10], 1):
        timestamp = format_datetime(trace["start_timestamp"])
        span_name = trace["span_name"]
        tags = trace.get("tags", [])
        chat_title = (
            trace.get("chat_title") or f"Chat ID: {trace.get('chat_id', 'Unknown')}"
        )
        message_text = trace.get("message_text", "")

        print(f"\n{i}. {timestamp}")
        print(f"   Span: {span_name}")
        print(f"   Chat: {chat_title}")
        if tags:
            print(f"   Tags: {', '.join(tags)}")
        if message_text:
            # Truncate long messages
            truncated_text = (
                message_text[:100] + "..." if len(message_text) > 100 else message_text
            )
            print(f"   Message: {truncated_text!r}")

    if data["total_traces"] > 10:
        print(f"\n... and {data['total_traces'] - 10} more traces")

    print_section("Daily Activity")

    for date in sorted(traces_by_date.keys(), reverse=True):
        day_traces = traces_by_date[date]
        print(f"{date}: {len(day_traces)} traces")

        # Show tag breakdown for the day
        day_tags = {}
        for trace in day_traces:
            for tag in trace.get("tags", []):
                day_tags[tag] = day_tags.get(tag, 0) + 1

        if day_tags:
            top_tags = sorted(day_tags.items(), key=lambda x: x[1], reverse=True)[:3]
            tag_str = ", ".join(f"{tag} ({count})" for tag, count in top_tags)
            print(f"  Top tags: {tag_str}")


async def main():
    parser = argparse.ArgumentParser(
        description="Find Logfire traces for a specific user"
    )
    parser.add_argument(
        "username",
        help="Username to search for (will find spans containing this username)",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Number of days to look back (default: 7)"
    )

    args = parser.parse_args()

    try:
        data = await query_user_traces(
            username=args.username,
            days_back=args.days,
        )
        display_trace_analysis(data)

    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
