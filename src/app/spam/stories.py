import logging

import logfire

from ..common.mtproto_client import MtprotoHttpError, get_mtproto_client
from ..types import ContextResult, ContextStatus, StorySummary

logger = logging.getLogger(__name__)


async def collect_user_stories(
    user_id: int, username: str | None = None, chat_id: int | None = None
) -> ContextResult[str]:
    """
    Collects stories for a user and formats them into a summary string.
    Returns ContextResult with appropriate status.
    """
    client = get_mtproto_client()

    with logfire.span(
        "Collecting user stories", user_id=user_id, username=username, chat_id=chat_id
    ):
        try:
            # Set up identifier: prefer username, but use user_id if no username
            # (subscription check is handled at higher level)
            peer_identifier = username or user_id

            try:
                peer_response = await client.call(
                    "stories.getPeerStories",
                    params={"peer": peer_identifier},
                    resolve=True,
                )
                stories_data = peer_response.get("stories", {}).get("stories", [])
            except MtprotoHttpError as e:
                logger.debug(
                    f"Failed to fetch pinned stories for identifier {peer_identifier}: {e}"
                )
                return ContextResult(status=ContextStatus.FAILED, error=str(e))

            if not stories_data:
                return ContextResult(status=ContextStatus.EMPTY)

            summaries = []
            for story in stories_data:
                # Skip deleted stories
                if story.get("_") == "storyItemDeleted":
                    continue

                caption = story.get("caption")
                entities = story.get("entities")
                media = story.get("media")

                # Check if story has any content worth including
                media_areas = story.get("media_areas")
                has_content = bool(
                    caption
                    or entities
                    or StorySummary.contains_links(media, media_areas)
                )

                # If there's no text content and no media links, skip
                if not has_content:
                    continue

                summary = StorySummary(
                    id=story.get("id"),
                    date=story.get("date"),
                    caption=caption,
                    entities=entities,
                    media=media,
                    media_areas=story.get("media_areas"),
                )
                summaries.append(summary.to_string())

            if not summaries:
                return ContextResult(status=ContextStatus.EMPTY)

            return ContextResult(
                status=ContextStatus.FOUND, content="\n".join(summaries)
            )

        except MtprotoHttpError as e:
            logger.info(
                "Failed to fetch user stories",
                extra={"user_id": user_id, "error": str(e)},
            )
            return ContextResult(status=ContextStatus.FAILED, error=str(e))
        except Exception as e:
            logger.exception(
                "Unexpected error fetching user stories",
                extra={"user_id": user_id, "error": str(e)},
            )
            return ContextResult(status=ContextStatus.FAILED, error=str(e))
