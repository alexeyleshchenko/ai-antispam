"""Tests for the validated LLM budget configuration."""

import pytest

from app.common.llm_budget import (
    WEBHOOK_RESERVE_SECONDS,
    get_llm_budget_seconds,
    get_llm_gateway_timeout,
    get_llm_per_attempt_timeout,
    get_llm_route_timeout,
    get_openrouter_models,
    reset_llm_config_validation,
    validate_llm_config,
)
from app.common.utils import get_webhook_timeout, load_config

VALID_LLM = {
    "llm": {
        "budget_seconds": 45,
        "gateway_timeout_seconds": 18,
        "route_timeout_seconds": 30,
        "openrouter_models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
        ],
    },
    "system": {"webhook_timeout": 55},
}


def test_validate_accepts_valid_config():
    reset_llm_config_validation()
    validate_llm_config(VALID_LLM)
    assert get_llm_route_timeout() == 30.0
    assert get_llm_budget_seconds() == 45.0
    assert get_llm_gateway_timeout() == 18.0
    assert get_llm_per_attempt_timeout() == 13.5
    assert get_openrouter_models() == VALID_LLM["llm"]["openrouter_models"]


def test_getters_require_validation():
    reset_llm_config_validation()
    with pytest.raises(RuntimeError, match="not validated"):
        get_llm_route_timeout()


def test_rejects_too_many_models_for_minimum_attempt_budget():
    config = {
        **VALID_LLM,
        "llm": {**VALID_LLM["llm"], "openrouter_models": [f"a/model-{i}" for i in range(15)]},
    }
    reset_llm_config_validation()
    with pytest.raises(ValueError, match="per_attempt_timeout_seconds"):
        validate_llm_config(config)


def test_rejects_budget_that_exceeds_webhook_reserve():
    config = {**VALID_LLM, "system": {"webhook_timeout": 50}}
    reset_llm_config_validation()
    with pytest.raises(ValueError, match="budget_seconds.*webhook_timeout"):
        validate_llm_config(config)


@pytest.mark.parametrize(
    "config,match",
    [
        ({}, "missing or invalid 'llm'"),
        ({"llm": "bad"}, "missing or invalid 'llm'"),
        ({"llm": {}}, "missing llm.budget_seconds"),
        (
            {"llm": {"budget_seconds": 45, "gateway_timeout_seconds": 18, "route_timeout_seconds": 30}},
            "openrouter_models must be a non-empty list",
        ),
        (
            {"llm": {"budget_seconds": 45, "gateway_timeout_seconds": 18, "route_timeout_seconds": 30, "openrouter_models": []}},
            "openrouter_models must be a non-empty list",
        ),
        (
            {"llm": {"budget_seconds": 45, "gateway_timeout_seconds": 18, "route_timeout_seconds": 30, "openrouter_models": "a/b"}},
            "openrouter_models must be a non-empty list",
        ),
        (
            {"llm": {"budget_seconds": "slow", "gateway_timeout_seconds": 18, "route_timeout_seconds": 30, "openrouter_models": ["a/b"]}},
            "budget_seconds must be a number",
        ),
        (
            {"llm": {"budget_seconds": 45, "gateway_timeout_seconds": 45, "route_timeout_seconds": 30, "openrouter_models": ["a/b"]}},
            "gateway_timeout_seconds must be <",
        ),
        (
            {"llm": {"budget_seconds": 45, "gateway_timeout_seconds": 18, "route_timeout_seconds": 30, "openrouter_models": ["no-slash"]}},
            r"openrouter_models\[0\].*containing",
        ),
    ],
)
def test_validate_rejects_invalid_config(config, match):
    reset_llm_config_validation()
    with pytest.raises(ValueError, match=match):
        validate_llm_config(config)


def test_project_config_yaml_is_valid():
    reset_llm_config_validation()
    expected_models = load_config()["llm"]["openrouter_models"]
    validate_llm_config()
    models = get_openrouter_models()
    assert set(models) == set(expected_models)
    assert len(models) == len(expected_models)


def test_pre_fix_arithmetic_exceeded_the_webhook_budget():
    """Regression control for the classifier timeout defect.

    Pre-fix, ONE route timeout was handed to the gateway leg AND to every
    OpenRouter model, so the worst case was ``route_timeout * (1 + len(models))``.
    On the shipped config that is ``30 * (1 + 3) = 120s`` against a 55s webhook,
    so the fallback leg could never finish. The derived legs must now fit with
    the reserve included.
    """
    reset_llm_config_validation()
    validate_llm_config()  # the shipped config.yaml

    webhook_timeout = float(get_webhook_timeout())
    models = get_openrouter_models()

    # Pre-fix: ONE route timeout served the gateway leg AND every model.
    pre_fix_total = get_llm_route_timeout() * (1 + len(models))
    assert pre_fix_total > webhook_timeout, (
        f"pre-fix arithmetic {pre_fix_total}s must be shown to exceed the "
        f"{webhook_timeout}s webhook"
    )

    # Post-fix: the derived legs fit inside the webhook budget, reserve included.
    derived_total = get_llm_gateway_timeout() + len(models) * get_llm_per_attempt_timeout()
    assert derived_total <= webhook_timeout - WEBHOOK_RESERVE_SECONDS, (
        f"derived {derived_total}s must fit {webhook_timeout}s minus the "
        f"{WEBHOOK_RESERVE_SECONDS}s reserve"
    )
