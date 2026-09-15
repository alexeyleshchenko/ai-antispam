"""MAX comment moderation orchestration logic.

Coordinates classification, whitelisting, and deletion triggers for MAX comments.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import logfire

from .max_webhook import MaxCommentItem
from .types import ContextResult, ContextStatus, SpamClassificationContext

if TYPE_CHECKING:
    from .max_client import MaxClient

logger = logging.getLogger(__name__)

# Owner whitelist for MAX platform — never moderate these user IDs.
MAX_OWNER_WHITELIST_IDS = frozenset({190126855})


async def process_max_comment(
    comment: MaxCommentItem,
    max_client: MaxClient | None = None,
) -> tuple[bool, int, str]:
    """Moderate an inbound MAX comment item.

    Returns:
        tuple of (is_spam, confidence, reason).
    """
    if not comment.is_actionable:
        logger.info(
            "Skipping non-actionable MAX comment: mid=%s post_mid=%s text_len=%d",
            comment.comment_mid,
            comment.post_mid,
            len(comment.text),
        )
        return False, 0, "not_actionable"

    # Whitelist check
    if comment.sender_id is not None and comment.sender_id in MAX_OWNER_WHITELIST_IDS:
        logger.info(
            "Bypassing moderation for whitelisted MAX user_id=%d", comment.sender_id
        )
        return False, 0, "whitelisted_owner"

    # Build context for MAX comment
    context = SpamClassificationContext(
        name=comment.sender_name,
        # No bio, stories, or linked channel available for third-party MAX commenters
        linked_channel=ContextResult(status=ContextStatus.EMPTY),
        stories=ContextResult(status=ContextStatus.EMPTY),
    )

    from .spam.spam_classifier import is_spam

    with logfire.span("max_comment_moderation", comment_mid=comment.comment_mid):
        is_spam_result, confidence, reason = await is_spam(
            comment=comment.text,
            context=context,
        )

    logger.info(
        "MAX comment classified: mid=%s is_spam=%s confidence=%d reason=%s",
        comment.comment_mid,
        is_spam_result,
        confidence,
        reason,
    )

    if is_spam_result and confidence >= 50 and max_client and comment.post_mid and comment.comment_mid:
        try:
            logger.warning(
                "Triggering deletion of spam MAX comment mid=%s (confidence=%d, reason=%s)",
                comment.comment_mid,
                confidence,
                reason,
            )
            await max_client.delete_comment(
                post_mid=comment.post_mid,
                comment_mid=comment.comment_mid,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to delete spam MAX comment mid=%s: %s",
                comment.comment_mid,
                e,
            )

    return is_spam_result, confidence, reason
