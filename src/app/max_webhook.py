"""MAX Bot API webhook ingress primitives.

Scope guard — INGRESS + OBSERVABILITY ONLY.
This module authenticates, validates and *describes* inbound MAX Bot API updates.
It implements NO MAX moderation: no comment deletion, no banning, no spam
classification. MAX moderation is the MAX port's own workstream, not this one
(see docs/memos/2026-07-26-max-antispam-memo.md).

Everything here is pure and importable without the aiohttp application, so the
auth / envelope / summary logic is unit-testable in isolation.
"""

import hmac
import logging
from typing import Any, cast

logger = logging.getLogger(__name__)

# MAX carries the subscription secret in this header (the `secret` field of a
# MAX Bot API subscription object). HTTP header names are case-insensitive, so
# the route must read it case-insensitively; this constant is the canonical name.
MAX_SECRET_HEADER = "X-Max-Bot-Api-Secret"


def verify_secret(provided: str | None, expected: str | None) -> bool:
    """Constant-time check of the ``X-Max-Bot-Api-Secret`` header value.

    FAILS CLOSED. A missing or empty ``expected`` — i.e. the secret is not
    configured on this host — returns False, as does a missing ``provided``.
    The route distinguishes the two: unconfigured -> 503, merely wrong -> 403.
    """
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


def is_valid_envelope(payload: Any) -> bool:
    """Return True when ``payload`` is a MAX update envelope.

    The live MAX envelope (observed in the 2026-07-28 delivery) is::

        {"update_type": "message_created", "timestamp": 1234,
         "message": {"recipient": {...}, "body": {...}}}

    Only ``update_type`` is required here, as a non-empty string. Every other
    field is optional and read defensively by :func:`summarise`.
    """
    if not isinstance(payload, dict):
        return False
    update_type = payload.get("update_type")
    return isinstance(update_type, str) and bool(update_type.strip())


def _as_dict(value: Any) -> dict[str, Any]:
    """Return ``value`` when it is a mapping, otherwise an empty dict."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def summarise(payload: dict[str, Any]) -> str:
    """Return a one-line, BODY-FREE description of a MAX update for the logs.

    NEVER includes the message body or the secret. Field names are fixed so the
    line stays greppable in the daemon log.
    """
    message = _as_dict(payload.get("message"))
    recipient = _as_dict(message.get("recipient"))
    body = _as_dict(message.get("body"))
    sender = _as_dict(message.get("sender"))

    text = body.get("text")
    text_len = len(text) if isinstance(text, str) else 0

    sender_name = (
        sender.get("name") or sender.get("username") or sender.get("first_name")
    )

    return (
        f"update_type={payload.get('update_type')}"
        f" chat_id={recipient.get('chat_id')}"
        f" mid={body.get('mid')}"
        f" sender_id={sender.get('user_id')}"
        f" sender_name={sender_name}"
        f" text_len={text_len}"
    )
