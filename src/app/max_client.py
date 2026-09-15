"""MAX platform API client.

Handles asynchronous HTTP communication with platform-api2.max.ru.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp
import logfire

logger = logging.getLogger(__name__)

MAX_API_BASE_URL = os.environ.get("MAX_API_BASE_URL", "https://platform-api2.max.ru")


class MaxApiError(Exception):
    """Raised when MAX API returns an error response."""

    def __init__(self, status: int, message: str, data: Any = None):
        super().__init__(f"MAX API error {status}: {message}")
        self.status = status
        self.message = message
        self.data = data


class MaxClient:
    """Client for MAX Bot API."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = MAX_API_BASE_URL,
        session: aiohttp.ClientSession | None = None,
    ):
        self.token = token or os.environ.get("MAX_BOT_TOKEN", "")
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def _get_headers(self) -> dict[str, str]:
        if not self.token:
            raise ValueError("MAX_BOT_TOKEN is not configured")
        # Auth uses raw token header without Bearer prefix
        return {"Authorization": self.token}

    async def delete_comment(self, post_mid: str, comment_mid: str) -> bool:
        """Delete a comment under a channel post.

        MAX API endpoint: DELETE /messages/{post_mid}/comments?comment_id={comment_mid}
        Returns True if deleted or already gone (403), raises MaxApiError otherwise.
        """
        if not post_mid or not comment_mid:
            raise ValueError("Both post_mid and comment_mid are required")

        url = f"{self.base_url}/messages/{post_mid}/comments"
        params = {"comment_id": comment_mid}
        headers = self._get_headers()

        session = await self._get_session()
        with logfire.span("max_delete_comment", post_mid=post_mid, comment_mid=comment_mid):
            try:
                async with session.delete(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        logger.info("Successfully deleted MAX comment mid=%s under post=%s", comment_mid, post_mid)
                        return True
                    if resp.status == 403:
                        # 403 access.denied in MAX delete context means already deleted / non-existent
                        logger.warning(
                            "MAX comment mid=%s already absent or deleted (HTTP 403)", comment_mid
                        )
                        return True

                    body = await resp.text()
                    logger.error(
                        "MAX API delete comment error HTTP %d: %s", resp.status, body
                    )
                    raise MaxApiError(resp.status, body)
            except aiohttp.ClientError as e:
                logger.error("HTTP client error deleting MAX comment mid=%s: %s", comment_mid, e)
                raise
