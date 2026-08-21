import html
import logging
import os
import re
from functools import cache
from typing import Any

import yaml
from aiogram import types
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiohttp import ClientError, ClientOSError
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Low-level / transport errors retried by retry_on_network_error
_RETRYABLE_TRANSPORT_ERRORS = (
    ClientOSError,
    ClientError,
    OSError,
    ConnectionError,
    TimeoutError,
    TelegramNetworkError,
    TelegramServerError,
    TelegramRetryAfter,
)

# Telegram API errors that must not be retried (permanent or domain-handled)
_NON_RETRYABLE_TELEGRAM_API = (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
)


def _is_retryable_network_error(exc: BaseException) -> bool:
    if isinstance(exc, _NON_RETRYABLE_TELEGRAM_API):
        return False
    return isinstance(exc, _RETRYABLE_TRANSPORT_ERRORS)


_EXPONENTIAL_WAIT = wait_exponential(multiplier=0.5, min=0.5, max=10)


def _retry_wait(retry_state: RetryCallState) -> float:
    """Single wait per retry: honor Telegram flood control or exponential backoff."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, TelegramRetryAfter):
        return float(min(exc.retry_after, 30))
    return _EXPONENTIAL_WAIT(retry_state)


async def _retry_before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, TelegramRetryAfter):
        delay = _retry_wait(retry_state)
        logger.info(
            "Telegram flood control on attempt %s/4, sleeping %ss",
            retry_state.attempt_number,
            delay,
        )
    elif retry_state.attempt_number <= 3:
        logger.info(
            "Retryable error on attempt %s/4: %s. Retrying...",
            retry_state.attempt_number,
            exc,
        )
    else:
        logger.warning(
            "All retries failed with error: %s",
            exc,
            exc_info=exc,
        )


retry_on_network_error = retry(
    stop=stop_after_attempt(4),
    wait=_retry_wait,
    retry=retry_if_exception(_is_retryable_network_error),
    reraise=True,
    before_sleep=_retry_before_sleep,
)


def _compute_retry_delay(exc: BaseException, attempt: int) -> float:
    """Delay for session-middleware sleep.

    - TelegramRetryAfter → honour flood control (cap 15s for webhook budget)
    - Other retryable   → exponential: min(0.5 × 2^(n-1), 10)
    """
    if isinstance(exc, TelegramRetryAfter):
        return min(exc.retry_after, 15)
    return min(0.5 * (2 ** (attempt - 1)), 10)


async def send_admin_dm(admin_id: int, text: str, log_context: str = "message") -> bool:
    """Send HTML message to admin with retry. Returns True if sent, False on failure."""
    from .bot import bot

    try:

        @retry_on_network_error
        async def _send():
            return await bot.send_message(
                admin_id,
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await _send()
        return True
    except Exception as e:
        logger.warning(
            f"Failed to send {log_context} to admin {admin_id}: {e}",
            exc_info=True,
        )
        return False


@cache
def load_config() -> dict[str, Any]:
    """Load config from config.yaml."""
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logger.debug("Configuration loaded successfully")
    return config


def spam_notify_spammers_via_mcp_enabled() -> bool:
    """Return whether MTProto spammer promotional DMs are enabled."""
    env_value = os.getenv("SPAM_NOTIFY_SPAMMERS_VIA_MCP")
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        logger.warning(
            "Invalid SPAM_NOTIFY_SPAMMERS_VIA_MCP value '%s', falling back to config",
            env_value,
        )

    return bool(load_config().get("spam", {}).get("notify_spammers_via_mcp", False))


def remove_lines_to_fit_len(text: str, max_len: int) -> str:
    """Trim text to max_len by collapsing middle lines."""
    splitted = text.split("\n")

    while len(text) > max_len - len("...\n") and len(splitted) > 2:
        half = len(splitted) // 2
        text = "\n".join(splitted[:half] + ["..."] + splitted[half + 1 :])
        splitted = splitted[:half] + splitted[half + 1 :]

    if len(text) > max_len:
        text = text[: max_len - len("...")] + "..."

    return text


def determine_effective_user_id(message: types.Message) -> int | None:
    """
    Determine the effective user ID for moderation.

    For channel messages (sender_chat), use channel ID unless it's the group itself (anonymous admin).
    For regular users, use their user ID.

    Args:
        message: The Telegram message to analyze

    Returns:
        The effective user ID for moderation, or None if not available
    """
    if message.sender_chat and message.sender_chat.id != message.chat.id:
        return message.sender_chat.id
    elif message.from_user:
        return message.from_user.id
    return None


def format_chat_or_channel_display(
    title: str | None,
    username: str | None,
    default_title: str = "Группа",
) -> str:
    """
    Format a chat or channel for user-facing messages: "Title (@username)" or "Title".
    Title part is sanitized for HTML.
    """
    display_title = html.escape(title or default_title, quote=True)
    return f"{display_title} (@{username})" if username else display_title


def format_chat_log(
    chat_id: int,
    title: str | None = None,
    username: str | None = None,
) -> str:
    """Format a chat ID for log messages: "-100123 ('My Group' @mygroup)" or "-100123"."""
    if title or username:
        parts = []
        if title:
            parts.append(f"'{title}'")
        if username:
            parts.append(f"@{username}")
        return f"{chat_id} ({' '.join(parts)})"
    return str(chat_id)


def format_user_log(
    user_id: int,
    name: str | None = None,
    username: str | None = None,
) -> str:
    """Format a user ID for log messages: "123 ('John' @john)" or "123"."""
    if name or username:
        parts = []
        if name:
            parts.append(f"'{name}'")
        if username:
            parts.append(f"@{username}")
        return f"{user_id} ({' '.join(parts)})"
    return str(user_id)


def sanitize_llm_html(text: str) -> str:
    """
    Sanitizes LLM-generated HTML content, allowing only safe Telegram HTML tags.

    Supported Telegram HTML tags (based on official Bot API specification):
    - <b> or <strong> for bold text
    - <i> or <em> for italic text
    - <u> or <ins> for underline
    - <s>, <strike>, or <del> for strikethrough
    - <span class="tg-spoiler"> for spoiler text
    - <tg-spoiler> for spoiler text
    - <a href="URL"> for clickable links
    - <code> for inline code
    - <pre> for code blocks
    - <blockquote> for block quotations
    - <tg-emoji> for custom emoji

    All other HTML tags are stripped while preserving their content.
    """
    if not text:
        return text

    # Regex-based approach for allowed Telegram HTML tags
    def replace_tag(match):
        tag_content = match.group(0)
        tag_name_match = re.match(r"</?([a-zA-Z-]+)", tag_content)
        if not tag_name_match:
            return ""

        tag_name = tag_name_match[1].lower()

        # Define allowed tags (exactly as per Telegram Bot API)
        allowed_tags = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "tg-spoiler",
            "a",
            "code",
            "pre",
            "span",  # Only allowed with class="tg-spoiler"
            "blockquote",
            "tg-emoji",
        }

        if tag_name in allowed_tags:
            # Special validation for span tags - only allow tg-spoiler
            if tag_name == "span":
                # Allow closing span tags and opening tags with tg-spoiler class
                if tag_content.startswith("</") or 'class="tg-spoiler"' in tag_content:
                    return tag_content
                else:
                    return ""  # Remove non-spoiler opening spans
            return tag_content  # Keep other allowed tags

        return ""  # Remove disallowed tags

    text = re.sub(r"</?[a-zA-Z-]+[^>]*>", replace_tag, text)

    return text


def clean_alert_text(text: str | None) -> str | None:
    """Strip alert/notification wrapper from text if present."""
    if not text:
        return text
    # Проверяем наличие служебных маркеров
    if "⚠️ ТРЕВОГА!" in text or "⚠️ ВТОРЖЕНИЕ!" in text or "Содержание угрозы:" in text:
        try:
            # Находим содержание угрозы
            start_idx = text.find("Содержание угрозы:")
            if start_idx != -1:
                start_idx += len("Содержание угрозы:")

                # Ищем конец содержания - останавливаемся на первом же маркере метаданных
                end_markers = [
                    "Причина:",
                    "Reason:",
                    "Вредоносное сообщение уничтожено",
                    "Ссылка на сообщение",
                    "ℹ️ Подробнее",
                    "💡 Совет:",
                ]

                end_idx = len(text)
                for marker in end_markers:
                    m_idx = text.find(marker, start_idx)
                    if m_idx != -1 and m_idx < end_idx:
                        end_idx = m_idx

                cleaned = text[start_idx:end_idx].strip()

                # Если текст обернут в blockquote (что часто бывает в уведомлениях), убираем его
                if cleaned.startswith("<blockquote"):
                    # Находим закрывающий тег первой цитаты
                    close_tag = cleaned.find("</blockquote>")
                    if close_tag != -1:
                        # Извлекаем текст внутри тегов
                        content_start = cleaned.find(">") + 1
                        cleaned = cleaned[content_start:close_tag].strip()

                # Если после очистки остались служебные строки, убираем их
                lines = [
                    line
                    for line in cleaned.splitlines()
                    if line.strip()
                    and all(
                        marker not in line
                        for marker in [
                            "⚠️ ТРЕВОГА!",
                            "⚠️ ВТОРЖЕНИЕ!",
                            "Группа:",
                            "Нарушитель:",
                            "Вредоносное сообщение уничтожено",
                            "ℹ️ Подробнее",
                            "Ссылка на сообщение",
                            "Причина:",
                            "Reason:",
                        ]
                    )
                ]
                return "\n".join(lines).strip()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error cleaning alert text: {e}")
    return text


def get_system_config() -> dict[str, Any]:
    """Get system configuration from config.yaml."""
    return load_config().get("system", {})


_system_config_cache: dict[str, Any] | None = None


def _get_cached_system_config() -> dict[str, Any]:
    """Return lazily loaded system config. Shared by all get_* URL/timeout helpers."""
    global _system_config_cache
    if _system_config_cache is None:
        _system_config_cache = get_system_config()
    return _system_config_cache


def get_project_channel_url() -> str:
    """Get project channel URL."""
    return _get_cached_system_config().get(
        "project_channel", "https://t.me/ai_antispam"
    )


def get_spam_guide_url() -> str:
    """Get spam guide URL."""
    return _get_cached_system_config().get(
        "spam_guide_url", "https://t.me/ai_antispam/7"
    )


def get_setup_guide_url() -> str:
    """Get setup guide URL."""
    return _get_cached_system_config().get(
        "setup_guide_url", "https://t.me/ai_antispam/14"
    )


def get_affiliate_url() -> str:
    """Get affiliate program URL."""
    return _get_cached_system_config().get(
        "affiliate_url", "https://telegram.org/tour/affiliate-programs/"
    )


def get_add_to_group_url() -> str:
    """Deep link to add bot to group with admin permissions (delete_messages, restrict_members)."""
    cfg = _get_cached_system_config()
    username = cfg.get("bot_username", "ai_spam_blocker_bot")
    return f"https://t.me/{username}?startgroup&admin=delete_messages+restrict_members"


def get_webhook_timeout() -> int:
    """Get webhook timeout in seconds."""
    return _get_cached_system_config().get("webhook_timeout", 55)


_validated_llm: dict[str, Any] | None = None


def reset_llm_config_validation() -> None:
    """Clear validated LLM config (for tests)."""
    global _validated_llm
    _validated_llm = None


def _parse_positive_number(value: Any, field: str) -> float:
    if value is None:
        raise ValueError(f"config.yaml: missing {field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"config.yaml: {field} must be a number") from e
    if parsed <= 0:
        raise ValueError(f"config.yaml: {field} must be positive")
    return parsed


def validate_llm_config(config: dict[str, Any] | None = None) -> None:
    """Validate llm section; store result for getters. Raises ValueError on failure."""
    global _validated_llm
    if config is None:
        config = load_config()

    llm = config.get("llm")
    if not isinstance(llm, dict):
        raise ValueError(  # noqa: TRY004 — established public contract (tested)
            "config.yaml: missing or invalid 'llm' section (expected mapping)"
        )

    route_timeout = _parse_positive_number(
        llm.get("route_timeout_seconds"), "llm.route_timeout_seconds"
    )
    http_timeout = _parse_positive_number(
        llm.get("http_client_timeout_seconds"), "llm.http_client_timeout_seconds"
    )
    if http_timeout < route_timeout:
        raise ValueError(
            "config.yaml: llm.http_client_timeout_seconds must be >= "
            "llm.route_timeout_seconds"
        )

    models = llm.get("openrouter_models")
    if not isinstance(models, list) or not models:
        raise ValueError("config.yaml: llm.openrouter_models must be a non-empty list")

    validated_models: list[str] = []
    for index, model in enumerate(models):
        if not isinstance(model, str):
            raise ValueError(  # noqa: TRY004 — established public contract (tested)
                f"config.yaml: llm.openrouter_models[{index}] must be a string"
            )
        model_id = model.strip()
        if not model_id or "/" not in model_id:
            raise ValueError(
                f"config.yaml: llm.openrouter_models[{index}] must be a non-empty "
                "model id containing '/'"
            )
        validated_models.append(model_id)

    _validated_llm = {
        "route_timeout_seconds": route_timeout,
        "http_client_timeout_seconds": http_timeout,
        "openrouter_models": validated_models,
    }


def _require_validated_llm() -> dict[str, Any]:
    if _validated_llm is None:
        raise RuntimeError(
            "LLM config not validated; server must call validate_llm_config() at startup"
        )
    return _validated_llm


def get_llm_config() -> dict[str, Any]:
    """Validated LLM config (gateway route ai-antispam mirror)."""
    return dict(_require_validated_llm())


def get_llm_route_timeout() -> float:
    """Per agent.run ModelSettings timeout (gateway and direct OpenRouter)."""
    return float(_require_validated_llm()["route_timeout_seconds"])


def get_llm_http_client_timeout() -> float:
    """httpx AsyncClient total timeout for LLM HTTP calls (seconds)."""
    return float(_require_validated_llm()["http_client_timeout_seconds"])


def get_openrouter_models() -> list[str]:
    """OpenRouter fallback model IDs, in gateway step order."""
    return list(_require_validated_llm()["openrouter_models"])


def get_dotted_path(json: dict, path: str, raise_on_missing: bool = False):
    """Get value from nested dict by dotted path. Use * to search all keys."""
    current_path = path
    current_json = json
    while True:
        if "." not in current_path:
            if current_path in current_json:
                return current_json[current_path]
            elif raise_on_missing:
                raise KeyError(f"Key {path} not found in {json}")
            else:
                return None
        next_step, rest = current_path.split(".", 1)
        if next_step == "*":
            for value in current_json.values():
                if isinstance(value, dict):
                    result = get_dotted_path(value, rest, False)
                    if result is not None:
                        return result
            if raise_on_missing:
                raise KeyError(f"Key {path} not found in {json}")
            else:
                return None
        if next_step in current_json:
            current_json = current_json[next_step]
            current_path = rest
        elif raise_on_missing:
            raise KeyError(f"Key {next_step} not found in {json}")
        else:
            return None
