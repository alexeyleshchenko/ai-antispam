"""Guards for the dead OpenRouter fallback tier (2026-09-24).

The classifier's per-attempt budget is DERIVED, not configured:

    per_attempt = (budget_seconds - gateway_timeout_seconds) / len(models)

On the shipped config that was ``(45 - 18) / 3 = 9.0s``, while the models'
measured latencies are 3-27s - and one configured model needed 45-56s, so it
could never fit at all and spent a third of the budget on a guaranteed failure.

Measured on the deployed service over 12:16-18:16Z: 265 classifications, 30
gateway timeouts, and the fallback rescued only 3 of them (10%), leaving 27
messages (10.2%) unmoderated. Every OpenRouter attempt in the window failed.
"""

import pytest

from app.common.llm_budget import (
    get_llm_budget_seconds,
    get_llm_gateway_timeout,
    get_llm_per_attempt_timeout,
    get_openrouter_models,
    reset_llm_config_validation,
    validate_llm_config,
)
from app.common.utils import get_webhook_timeout

# The floor a fallback leg needs to complete a real model call, and the shape it
# holds the pool to. The shipped pool's slowest survivor measures max 9.5s
# (dots-3-note-preview; nex-n2.5-mini max 5.5s), and two models behind the 15s
# gateway derive exactly 15.0s each. The floor is set at that DERIVED value
# rather than at 9.5s because it also pins the pool at TWO models: a third would
# derive (45 - 15) / 3 = 10.0s, below the floor, and the guard refuses it.
MIN_VIABLE_PER_ATTEMPT_SECONDS = 15.0

# Latency measured for the model that was dropped, against the total budget.
DROPPED_MODEL = "nvidia/nemotron-3.5-lightning:free"
DROPPED_MODEL_MEASURED_SECONDS = (45.2, 55.6, 56.1)


def test_derived_per_attempt_budget_leaves_the_fallback_viable():
    """Each fallback attempt must be able to outlast a real model call."""
    reset_llm_config_validation()
    validate_llm_config()

    per_attempt = get_llm_per_attempt_timeout()
    assert per_attempt >= MIN_VIABLE_PER_ATTEMPT_SECONDS, (
        f"per-attempt budget {per_attempt}s cannot complete a 3-27s model call; "
        "the fallback tier is dead at this value"
    )


def test_derived_legs_still_fit_the_webhook_budget():
    """Raising the per-attempt budget must not break the webhook guard."""
    reset_llm_config_validation()
    validate_llm_config()

    models = get_openrouter_models()
    total = get_llm_gateway_timeout() + len(models) * get_llm_per_attempt_timeout()
    assert total <= get_webhook_timeout() - 10.0, (
        f"derived {total}s must fit the {get_webhook_timeout()}s webhook minus reserve"
    )


def test_no_configured_model_is_slower_than_the_whole_budget():
    """A model slower than the entire budget is pure waste - it cannot ever win.

    This is the check that would have caught the original configuration: the
    dropped model needs more time than the classifier is allowed in total, so
    every attempt spent on it was guaranteed to fail.
    """
    reset_llm_config_validation()
    validate_llm_config()

    assert DROPPED_MODEL not in get_openrouter_models(), (
        f"{DROPPED_MODEL} measured {DROPPED_MODEL_MEASURED_SECONDS}s against a "
        f"{get_llm_budget_seconds()}s total budget - it can never succeed and "
        "should not occupy a fallback attempt"
    )
    assert get_llm_per_attempt_timeout() <= get_llm_budget_seconds()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
