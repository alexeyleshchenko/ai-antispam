# autoflake: skip_file

# Initialize logging
import contextlib
import logging

from .logging_setup import setup_logging

setup_logging(environment="production")
logger = logging.getLogger(__name__)

# Start the server
import asyncio
import os
import time
from asyncio import TimeoutError
from typing import Optional

import logfire
from aiogram.dispatcher.event.bases import UNHANDLED
from aiohttp import web

# Import all handlers to register them with the dispatcher
from .handlers import *

from .background_jobs import scheduled_jobs_loop
from .bot_commands import setup_bot_commands
from .common.bot import bot
from .common.trace_context import set_root_span
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from .common.mcp_client import close_mcp_http_client
from .common.telegram_errors import is_webhook_retryable
from .common.utils import get_dotted_path, get_webhook_timeout, validate_llm_config
from .database.postgres_connection import close_pool
from .handlers.dp import dp
from .logging_setup import get_telegram_handler, register_telegram_logging_loop

routes = web.RouteTableDef()
app = web.Application()

# Telegram allows up to 60s; value comes from config.yaml system.webhook_timeout
WEBHOOK_TIMEOUT = get_webhook_timeout()

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

    with logfire.span(extract_chat_or_user(json), update=json) as span:
        set_root_span(span)
        try:
            # Wrap the update handling in a timeout
            result = await asyncio.wait_for(
                dp.feed_raw_update(bot, json), timeout=WEBHOOK_TIMEOUT
            )

            # Add tag based on handler result
            span.tags = (
                [result]
                if result is not None and result != UNHANDLED
                else [extract_update_type_ignored(json)]
            )

            return web.json_response({"message": "Processed successfully"})

        except TimeoutError:
            elapsed = time.time() - start_time
            return await handle_timeout(span, json, elapsed)

        except ModelAPIError as e:
            elapsed = time.time() - start_time
            remaining = WEBHOOK_TIMEOUT - elapsed
            return await handle_temporary_error(span, e, elapsed, remaining)

        except Exception as e:
            elapsed = time.time() - start_time
            return await handle_unhandled_exception(span, e, json, elapsed)

        finally:
            if update_time := get_dotted_path(json, "*.edit_date") or get_dotted_path(
                json, "*.date"
            ):
                serve_time = max(0.0, time.time() - update_time)
                span.set_attribute("serve_time", serve_time)
                serve_time_histogram.record(serve_time)


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
        logger.debug(
            "Webhook update received: update_id=%s type=%s", update_id, utype
        )


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
        except Exception:
            continue

    return "Unknown chat or user"


app.add_routes(routes)


async def _on_startup_validate_config(app: web.Application) -> None:
    validate_llm_config()
    logger.info("LLM config validated")


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


_scheduled_jobs_task: Optional[asyncio.Task] = None


async def _on_startup_scheduled_jobs(app: web.Application) -> None:
    """Start the unified scheduled jobs loop (low balance + cache cleanups)."""
    global _scheduled_jobs_task
    _scheduled_jobs_task = asyncio.create_task(scheduled_jobs_loop())
    logger.info("Scheduled jobs loop started")


async def _on_startup_log_server_started(app: web.Application) -> None:
    logging.warning("Server started")


async def _shutdown(app: web.Application) -> None:
    """Gracefully shutdown all resources."""
    logger.info("Starting graceful shutdown...")

    global _scheduled_jobs_task
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

    async with asyncio.TaskGroup() as tg:
        tg.create_task(bot.session.close())
        tg.create_task(close_pool())
        tg.create_task(close_mcp_http_client())


app.on_startup.append(_on_startup_validate_config)
app.on_startup.append(_on_startup_setup_bot)
app.on_startup.append(_on_startup_scheduled_jobs)
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
            "message": e.message,
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
                exc_info=True,
            )
            return web.json_response(
                {"error": "Transient processing error", "retry": True},
                status=503,
            )
        logger.warning(
            "Transient webhook error but no time for retries (elapsed=%.2fs): %s",
            elapsed,
            e,
            exc_info=True,
        )
        return web.json_response(
            {"message": "Transient error but no time for retries", "elapsed": elapsed}
        )

    span.tags = ["unhandled_exception"]
    logger.warning(
        "Webhook error acknowledged without retry: %s",
        e,
        exc_info=True,
    )
    return web.json_response({"message": "Error processing request"})


if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=8080)
