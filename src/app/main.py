# autoflake: skip_file

# Initialize logging
import contextlib
import logging

from .logging_setup import setup_logging

setup_logging(environment="production")
logger = logging.getLogger(__name__)

# Start the server
import asyncio
import builtins
import os
import time

import logfire
from aiogram.dispatcher.event.bases import UNHANDLED
from aiohttp import web
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from .background_jobs import scheduled_jobs_loop
from .bot_commands import setup_bot_commands
from .common.bot import bot
from .common.llm_budget import WEBHOOK_RESERVE_SECONDS, validate_llm_config
from .common.mcp_client import close_mcp_http_client
from .common.telegram_errors import is_webhook_retryable
from .common.trace_context import set_root_span, set_webhook_deadline
from .common.utils import get_dotted_path, get_webhook_timeout
from .database.classification_verdicts import ensure_verdict_table
from .database.postgres_connection import close_pool, get_pool

# Import all handlers to register them with the dispatcher
from .handlers import *
from .handlers.dp import dp
from .handlers.message.verdict import RESULT_VERDICT_PENDING, inflight_tasks
from .logging_setup import get_telegram_handler, register_telegram_logging_loop
from .max_webhook import (
    MAX_SECRET_HEADER,
    extract_comment,
    is_valid_envelope,
    summarise,
    verify_secret,
)

routes = web.RouteTableDef()
app = web.Application()

# Telegram allows up to 60s; value comes from config.yaml system.webhook_timeout
WEBHOOK_TIMEOUT = get_webhook_timeout()

# Detached update tasks. The webhook guard must not be able to cancel the work
# that produces a verdict, so the update runs in its own task and the guard
# waits on it through a shield. Drained at shutdown, before the pool closes.
_update_tasks: set[asyncio.Task] = set()


def _on_update_task_done(task: asyncio.Task) -> None:
    """Discard a finished update task and surface an unretrieved exception.

    An exception nobody retrieves is silent; the task is detached, so nothing
    else is left to report it.
    """
    _update_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Detached update task failed: {exc!r}")


# Create histogram metric once at module level
serve_time_histogram = logfire.metric_histogram("serve_time", unit="s")


@routes.get("/health")
async def healthcheck(_: web.Request) -> web.Response:
    """Return plain OK response for health probes."""
    return web.Response(text="ok")


@routes.post("/process-tg-updates")
async def handle_update(request: web.Request) -> web.Response:
    """Handle incoming Telegram update"""
    if not await request.read():
        return web.Response()

    json = await request.json()

    # Validate that this is a proper Telegram update
    if not isinstance(json, dict) or "update_id" not in json:
        logger.warning(f"Received invalid update format: {json}")
        return web.json_response(
            {"error": "Invalid update format", "required_field": "update_id"},
            status=400,
        )

    log_update_received(json)

    start_time = time.time()
    # The request deadline the verdict gate reads: what is left of the
    # webhook budget once the reserve is set aside for the response.
    set_webhook_deadline(WEBHOOK_TIMEOUT - WEBHOOK_RESERVE_SECONDS)

    with logfire.span(extract_chat_or_user(json), update=json) as span:
        set_root_span(span)
        try:
            # Detached, so the guard cannot cancel the work: the task keeps
            # running past the deadline and still persists its verdict.
            update_task = asyncio.create_task(dp.feed_raw_update(bot, json))
            _update_tasks.add(update_task)
            update_task.add_done_callback(_on_update_task_done)

            # shield, not a bare wait_for on the coroutine: the timeout must
            # cancel the WAIT, never the task. A cancelled classification is a
            # decision paid for and thrown away.
            result = await asyncio.wait_for(
                asyncio.shield(update_task), timeout=WEBHOOK_TIMEOUT
            )

            # A pending verdict is not a failure: the classification
            # outlived the guard and its verdict is still being written.
            # Answer 503 so Telegram redelivers - the redelivery serves the
            # stored verdict instead of paying for a second classification.
            if result == RESULT_VERDICT_PENDING:
                return await handle_classification_pending(
                    span, json, time.time() - start_time
                )

            # Add tag based on handler result
            span.tags = (
                [result]
                if result is not None and result != UNHANDLED
                else [extract_update_type_ignored(json)]
            )

            return web.json_response({"message": "Processed successfully"})

        except builtins.TimeoutError:
            elapsed = time.time() - start_time
            return await handle_timeout(span, json, elapsed)

        except ModelAPIError as e:
            elapsed = time.time() - start_time
            remaining = WEBHOOK_TIMEOUT - elapsed
            return await handle_temporary_error(span, e, elapsed, remaining)

        except Exception as e:  # noqa: BLE001
            elapsed = time.time() - start_time
            return await handle_unhandled_exception(span, e, json, elapsed)

        finally:
            if update_time := get_dotted_path(json, "*.edit_date") or get_dotted_path(
                json, "*.date"
            ):
                serve_time = max(0.0, time.time() - update_time)
                span.set_attribute("serve_time", serve_time)
                serve_time_histogram.record(serve_time)


@routes.post("/process-max-updates")
async def handle_max_update(request: web.Request) -> web.Response:
    """Handle an inbound MAX Bot API update.

    INGRESS + OBSERVABILITY ONLY — authenticate, validate, log, acknowledge.
    No moderation runs here; MAX moderation is the MAX port's own workstream.

    Fails CLOSED: with MAX_WEBHOOK_SECRET unset this ingress refuses to run at
    all (503) rather than accepting unauthenticated traffic on a public route
    that will later carry a moderation hook.
    """
    expected = os.environ.get("MAX_WEBHOOK_SECRET")
    if not expected:
        logger.error(
            "MAX webhook rejected: MAX_WEBHOOK_SECRET is not set — refusing "
            "unauthenticated MAX traffic (fail closed)"
        )
        return web.json_response(
            {"error": "MAX webhook secret not configured"}, status=503
        )

    if not verify_secret(request.headers.get(MAX_SECRET_HEADER), expected):
        logger.warning(
            "MAX webhook rejected: missing or invalid %s header", MAX_SECRET_HEADER
        )
        return web.json_response({"error": "Unauthorized"}, status=403)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        logger.warning("MAX webhook rejected: body is not valid JSON")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not is_valid_envelope(payload):
        logger.warning("MAX webhook rejected: invalid update envelope")
        return web.json_response(
            {"error": "Invalid update format", "required_field": "update_type"},
            status=400,
        )

    logger.info("MAX update received: %s", summarise(payload))

    # Dispatch actionable comments to background moderation task so webhook returns HTTP 200 immediately
    if (comment := extract_comment(payload)) and comment.is_actionable:
        from .max_client import MaxClient
        from .max_moderator import process_max_comment

        max_client = getattr(request.app, "max_client", None)
        if max_client is None:
            max_client = MaxClient()

        asyncio.create_task(
            process_max_comment(
                comment=comment,
                max_client=max_client,
            )
        )

    return web.json_response({"ok": True})


def _update_type(json: dict) -> str:
    """Return the Telegram update type (the single non-update_id key)."""
    keys = [k for k in json if k != "update_id"]
    if len(keys) == 1:
        return keys[0]
    return "unknown" if not keys else "multiple"


def log_update_received(json: dict) -> None:
    """Log update_id so duplicate Telegram deliveries can be correlated in the logs.

    callback_query updates (admin button presses — rare) log at INFO with context;
    every other update type logs at DEBUG to avoid flooding the log stream.
    """
    update_id = json.get("update_id")
    utype = _update_type(json)
    if utype == "callback_query":
        cb = json.get("callback_query") or {}
        from_user = cb.get("from") or {}
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        logger.info(
            "Webhook callback_query received: update_id=%s chat=%s from=%s data=%s",
            update_id,
            chat_id,
            from_user.get("username") or from_user.get("id"),
            cb.get("data"),
        )
    else:
        logger.debug("Webhook update received: update_id=%s type=%s", update_id, utype)


def extract_update_type_ignored(json: dict) -> str:
    """Extract update type from JSON and return it with '_ignored' suffix."""
    # Remove 'update_id' from keys to find the update type
    update_keys = [key for key in json if key != "update_id"]

    if len(update_keys) == 1:
        return f"{update_keys[0]}_ignored"
    elif not update_keys:
        return "empty_update_ignored"
    else:
        # Multiple update types (shouldn't happen in valid Telegram updates)
        return "multiple_types_ignored"


def extract_chat_or_user(json: dict) -> str:
    # Extract message title or username from the update
    for path in [
        "*.chat.title",
        "*.from.username",
        "*.from.first_name",
    ]:
        try:
            return f"{get_dotted_path(json, path, True)}"
        except Exception:  # noqa: BLE001, S112
            continue

    return "Unknown chat or user"


app.add_routes(routes)


async def _on_startup_validate_config(app: web.Application) -> None:
    validate_llm_config()
    logger.info("LLM config validated")


async def _on_startup_ensure_verdict_table(app: web.Application) -> None:
    """Create the verdict store if it is missing.

    Never raises: a crash-looping container is worse than a degraded store, and
    the moderation path has a store-failure fallback that keeps running
    un-gated.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await ensure_verdict_table(conn)
        logger.info("Verdict store ready")
    except Exception:
        logger.exception("Verdict store setup failed; moderation continues without it")
        return


async def _on_startup_setup_bot(app: web.Application) -> None:
    """Register logging loop, bot command menus, and webhook."""
    register_telegram_logging_loop(asyncio.get_running_loop())
    await setup_bot_commands(bot)
    try:
        webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL")
        if not webhook_url:
            msg = "TELEGRAM_WEBHOOK_URL is not set or empty"
            logger.error(msg)
            raise ValueError(msg)
        logger.info(f"Setting webhook URL to: {webhook_url}")
        await bot.set_webhook(webhook_url)
        logger.info("Webhook setup completed successfully")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}")
        raise


_scheduled_jobs_task: asyncio.Task | None = None


async def _on_startup_scheduled_jobs(app: web.Application) -> None:
    """Start the unified scheduled jobs loop (low balance + cache cleanups)."""
    global _scheduled_jobs_task
    _scheduled_jobs_task = asyncio.create_task(scheduled_jobs_loop())
    logger.info("Scheduled jobs loop started")


async def _on_startup_seed_protected_channels(app: web.Application) -> None:
    """Seed the in-memory protected-channel set from the DB.

    A channel whose linked discussion group the bot protects must never trigger a
    self-leave on channel_post (leaving the channel cascades into leaving the
    discussion group, ending protection — issue #34).
    """
    from .handlers.message.channel_management import _seed_protected_channels

    await _seed_protected_channels()


async def _on_startup_log_server_started(app: web.Application) -> None:
    logger.warning("Server started")


async def _shutdown(app: web.Application) -> None:
    """Gracefully shutdown all resources."""
    logger.info("Starting graceful shutdown...")

    if _scheduled_jobs_task and not _scheduled_jobs_task.done():
        _scheduled_jobs_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _scheduled_jobs_task

    # Stop TelegramLogHandler before closing the bot session,
    # so that queued messages can still be sent before the connector is closed.
    if telegram_handler := get_telegram_handler():
        try:
            await telegram_handler.stop(timeout=5.0)
        except Exception as e:
            logger.warning(f"Error stopping TelegramLogHandler: {e}", exc_info=True)

    # Drain detached work BEFORE the TaskGroup closes the pool: anything that
    # finishes after close_pool() cannot write its verdict. Two sets, because
    # the verdict task is a sibling of the update task, not its child.
    detached = _update_tasks | inflight_tasks()
    if detached:
        _, still_running = await asyncio.wait(detached, timeout=5)
        for task in still_running:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async with asyncio.TaskGroup() as tg:
        tg.create_task(bot.session.close())
        tg.create_task(close_pool())
        tg.create_task(close_mcp_http_client())


app.on_startup.append(_on_startup_validate_config)
app.on_startup.append(_on_startup_ensure_verdict_table)
app.on_startup.append(_on_startup_setup_bot)
app.on_startup.append(_on_startup_scheduled_jobs)
app.on_startup.append(_on_startup_seed_protected_channels)
app.on_startup.append(_on_startup_log_server_started)
app.on_shutdown.append(_shutdown)


async def handle_timeout(
    span: logfire.LogfireSpan, json: dict, elapsed: float
) -> web.Response:
    """Handle timeout error."""
    logger.warning(f"Webhook processing timed out after {elapsed:.2f} seconds")
    span.tags = ["webhook_timeout"]

    return web.json_response(
        {"error": "Processing timed out", "retry": True},
        status=503,
    )


async def handle_classification_pending(
    span: logfire.LogfireSpan, json: dict, elapsed: float
) -> web.Response:
    """The verdict is still being written; ask Telegram to redeliver.

    Distinct from handle_timeout: nothing was cancelled and nothing was lost.
    The classification is running to completion in a detached task, and the
    redelivery serves its stored verdict. Same 503 + retry shape, so the retry
    semantics Telegram already honours are reused rather than reinvented.
    """
    logger.info(f"Classification pending after {elapsed:.2f}s; requesting redelivery")
    span.tags = ["classification_pending"]

    return web.json_response(
        {"error": "Classification pending", "retry": True},
        status=503,
    )


async def handle_temporary_error(
    span: logfire.LogfireSpan,
    e: ModelAPIError,
    elapsed: float,
    remaining: float,
) -> web.Response:
    """Handle ModelAPIError from pydantic-ai after retries exhausted."""
    # Check if it's an HTTP error with status_code
    status_code = e.status_code if isinstance(e, ModelHTTPError) else None
    is_rate_limit = status_code == 429
    error_type = "rate_limit" if is_rate_limit else "model_api_error"
    span.tags = [error_type]

    logger.warning(
        f"LLM error after retries exhausted: {e.model_name} {e.message}",
        extra={
            "elapsed": elapsed,
            "remaining": remaining,
            "status_code": status_code,
            "model": e.model_name,
            "error_message": e.message,
        },
    )

    if remaining < 5:
        logger.warning(
            "LLM error but no time for retries",
            extra={
                "elapsed": elapsed,
                "remaining": remaining,
                "status_code": status_code,
            },
        )
        return web.json_response(
            {"message": "LLM error but no time for retries", "elapsed": elapsed}
        )

    if is_rate_limit:
        body = {"error": "OpenRouter rate limit exceeded", "retry": True}
    else:
        body = {"error": "LLM provider error", "retry": True}
    return web.json_response(body, status=503)


async def handle_unhandled_exception(
    span: logfire.LogfireSpan, e: Exception, json: dict, elapsed: float
) -> web.Response:
    """Handle exceptions that escape feed_raw_update (often re-raised from @dp.errors())."""
    span.record_exception(e)

    if is_webhook_retryable(e):
        remaining = WEBHOOK_TIMEOUT - elapsed
        span.tags = ["webhook_retryable_error"]
        if remaining >= 5:
            logger.warning(
                "Transient webhook error, requesting Telegram retry: %s",
                e,
                exc_info=e,
            )
            return web.json_response(
                {"error": "Transient processing error", "retry": True},
                status=503,
            )
        logger.warning(
            "Transient webhook error but no time for retries (elapsed=%.2fs): %s",
            elapsed,
            e,
            exc_info=e,
        )
        return web.json_response(
            {"message": "Transient error but no time for retries", "elapsed": elapsed}
        )

    span.tags = ["unhandled_exception"]
    logger.warning(
        "Webhook error acknowledged without retry: %s",
        e,
        exc_info=e,
    )
    return web.json_response({"message": "Error processing request"})


if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
