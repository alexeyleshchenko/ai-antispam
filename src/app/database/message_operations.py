import logging

from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

MESSAGE_HISTORY_SIZE = 30
MESSAGE_TTL = 60 * 60 * 24  # 24 hours


async def save_message(admin_id: int, role: str, content: str) -> None:
    """Save a message to the admin's conversation history"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
                INSERT INTO message_history (admin_id, role, content, created_at)
                VALUES ($1, $2, $3, NOW())
            """,
            admin_id,
            role,
            content,
        )

        count = await conn.fetchval(
            """
                SELECT COUNT(*) FROM message_history
                WHERE admin_id = $1
            """,
            admin_id,
        )

        if count > MESSAGE_HISTORY_SIZE:
            await conn.execute(
                """
                    DELETE FROM message_history
                    WHERE id IN (
                        SELECT id FROM message_history
                        WHERE admin_id = $1
                        ORDER BY created_at ASC
                        LIMIT $2
                    )
                """,
                admin_id,
                count - MESSAGE_HISTORY_SIZE,
            )


async def cleanup_old_message_history(days: int = 1) -> int:
    """Remove message_history rows older than specified days. Returns deleted count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM message_history WHERE created_at < NOW() - INTERVAL '1 day' * $1",
            days,
        )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Cleaned up {count} old message_history entries")
    return count


async def get_message_history(admin_id: int) -> list[dict]:
    """Retrieve admin's conversation history"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content
            FROM message_history
            WHERE admin_id = $1
            ORDER BY created_at ASC
        """,
            admin_id,
        )

        return [{"role": row["role"], "content": row["content"]} for row in rows]


async def clear_message_history(admin_id: int) -> None:
    """Clear admin's conversation history"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM message_history WHERE admin_id = $1
        """,
            admin_id,
        )
