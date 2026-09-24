"""Validated LLM budget configuration and accessors."""

from typing import Any

from .utils import load_config

WEBHOOK_RESERVE_SECONDS = 10.0
MIN_PER_ATTEMPT_SECONDS = 3.0

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
    """Validate the LLM budget and store the derived values for getters."""
    global _validated_llm
    if config is None:
        config = load_config()

    llm = config.get("llm")
    if not isinstance(llm, dict):
        raise ValueError("config.yaml: missing or invalid 'llm' section (expected mapping)")  # noqa: TRY004 - config validation: ValueError is the domain error

    budget = _parse_positive_number(llm.get("budget_seconds"), "llm.budget_seconds")
    gateway = _parse_positive_number(
        llm.get("gateway_timeout_seconds"), "llm.gateway_timeout_seconds"
    )
    route = _parse_positive_number(
        llm.get("route_timeout_seconds"), "llm.route_timeout_seconds"
    )
    models = llm.get("openrouter_models")
    if not isinstance(models, list) or not models:
        raise ValueError("config.yaml: llm.openrouter_models must be a non-empty list")

    validated_models: list[str] = []
    for index, model in enumerate(models):
        if not isinstance(model, str):
            raise ValueError(f"config.yaml: llm.openrouter_models[{index}] must be a string")  # noqa: TRY004 - config validation: ValueError is the domain error
        model_id = model.strip()
        if not model_id or "/" not in model_id:
            raise ValueError(
                f"config.yaml: llm.openrouter_models[{index}] must be a non-empty model id containing '/'"
            )
        validated_models.append(model_id)

    if gateway >= budget:
        raise ValueError("config.yaml: llm.gateway_timeout_seconds must be < llm.budget_seconds")
    per_attempt = (budget - gateway) / len(validated_models)
    if per_attempt < MIN_PER_ATTEMPT_SECONDS:
        raise ValueError(
            "config.yaml: derived llm.per_attempt_timeout_seconds must be at least "
            f"{MIN_PER_ATTEMPT_SECONDS:g} (got {per_attempt:g} for {len(validated_models)} models)"
        )

    webhook_timeout = _parse_positive_number(
        config.get("system", {}).get("webhook_timeout", 55), "system.webhook_timeout"
    )
    if budget + WEBHOOK_RESERVE_SECONDS > webhook_timeout:
        raise ValueError(
            "config.yaml: llm.budget_seconds + reserve must fit system.webhook_timeout"
        )
    if route + WEBHOOK_RESERVE_SECONDS > webhook_timeout:
        raise ValueError(
            "config.yaml: llm.route_timeout_seconds + reserve must fit system.webhook_timeout"
        )

    _validated_llm = {
        "budget_seconds": budget,
        "gateway_timeout_seconds": gateway,
        "route_timeout_seconds": route,
        "per_attempt_timeout_seconds": per_attempt,
        "openrouter_models": validated_models,
    }


def _require_validated_llm() -> dict[str, Any]:
    if _validated_llm is None:
        raise RuntimeError("LLM config not validated; server must call validate_llm_config() at startup")
    return _validated_llm


def get_llm_config() -> dict[str, Any]:
    return dict(_require_validated_llm())


def get_llm_budget_seconds() -> float:
    return float(_require_validated_llm()["budget_seconds"])


def get_llm_gateway_timeout() -> float:
    return float(_require_validated_llm()["gateway_timeout_seconds"])


def get_llm_per_attempt_timeout() -> float:
    return float(_require_validated_llm()["per_attempt_timeout_seconds"])


def get_llm_route_timeout() -> float:
    return float(_require_validated_llm()["route_timeout_seconds"])


def get_openrouter_models() -> list[str]:
    return list(_require_validated_llm()["openrouter_models"])
