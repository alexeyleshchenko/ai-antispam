"""Guards for the dead OpenRouter fallback tier (2026-09-24).

The classifier's per-attempt budget is DERIVED, not configured:

    per_attempt = (budget_seconds - gateway_timeout_seconds) / len(models)

On the shipped config that was ``(45 - 18) / 3 = 9.0s``, while the models'
measured latencies are 3-27s - and one configured model needed 45-56s, so it
could never fit at all and spent a third of the budget on a guaranteed failure.

Measured on the deployed service over 12:16-18:16Z: 265 classifications, 30
gateway timeouts, and the fallback rescued only 3 of them (10%), leaving 27
messages (10.2%) unmoderated. Every OpenRouter attempt in the window failed.

REGRESSION 2026-09-25: a configured model can also VANISH from the catalogue
rather than merely be slow. ``nex-agi/nex-n2.5-mini:free`` - the fastest of the
two shipped on 09-24 - began returning 404 "No endpoints found" at 14:20Z, so
every fallback attempt burned its first leg on a guaranteed failure. The pool
survived only because the second model was a different provider. This is why
``DEAD_MODELS`` is pinned below: a model that has left the catalogue must not be
quietly re-added by a later config edit.
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
# holds the pool to. Measured 2026-09-25 at production parity (real prompt, real
# owner-scoped corpus, 19,135-char system prompt) over 12 free candidates: the
# survivors are ling-3.0-flash-fin (5/5, p50 6.23s, max 7.16s) and
# dots-3-note-preview (5/5, p50 10.96s, max 14.46s). Two models behind the 15s
# gateway derive exactly 15.0s each. The floor is set at that DERIVED value
# rather than at the slowest survivor because it also pins the pool at TWO
# models: a third would derive (45 - 15) / 3 = 10.0s, below the floor, and the
# guard refuses it.
MIN_VIABLE_PER_ATTEMPT_SECONDS = 15.0

# Latency measured for the model that was dropped, against the total budget.
DROPPED_MODEL = "nvidia/nemotron-3.5-lightning:free"
DROPPED_MODEL_MEASURED_SECONDS = (45.2, 55.6, 56.1)

# Models that have LEFT the OpenRouter catalogue. Measured 2026-09-25: both
# answer 404 "No endpoints found" for every request shape, so a pool entry
# pointing at either is a guaranteed wasted attempt.
DEAD_MODELS = (
    "nex-agi/nex-n2.5-mini:free",  # vanished 2026-09-25 14:20Z
    "z-ai/glm-5.2:free",  # 404 on probe
)

# Models BANNED by the owner for reason-language defects - distinct from
# DEAD_MODELS, which is about availability. A banned model is REACHABLE and
# would serve; it must not, because the reason it writes is unreadable to the
# admins who act on it.
#
# ling-3.0-flash-fin was banned 2026-09-25. Measured in production: of 494
# verdict reasons in 24h, 4 carried CJK - and after separating the two causes,
# 2 were the model's own prose (one 141-char run, entirely Chinese) and 2 were
# the user's own CJK display name being quoted, which is legitimate. It was
# serving every fallback rescue at the time.
BANNED_MODELS = (
    "inclusionai/ling-3.0-flash-fin:free",  # owner ban 2026-09-25: Chinese reasons
)



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


def test_no_dead_model_occupies_a_fallback_attempt():
    """A model that has left the catalogue must not be re-added by a config edit.

    Regression 2026-09-25: nex-agi/nex-n2.5-mini:free returned 404 "No endpoints
    found" from 14:20Z, so attempt 1 was a guaranteed failure on every fallback
    until it was replaced. The pool only survived because attempt 2 was a
    different provider - the cross-provider shape is the load-bearing part.
    """
    reset_llm_config_validation()
    validate_llm_config()

    configured = get_openrouter_models()
    for dead in DEAD_MODELS:
        assert dead not in configured, (
            f"{dead} has left the OpenRouter catalogue (404 'No endpoints found') "
            "and cannot serve a fallback attempt"
        )


def test_no_banned_model_occupies_a_fallback_attempt():
    """A model the owner banned for reason-language must not be re-added.

    Owner ban 2026-09-25: ling-3.0-flash-fin wrote its reason in Chinese in
    production (one 141-char run, entirely CJK), while serving every fallback
    rescue. The reason is the admin-facing field - it is what the group owner
    reads to decide whether the bot was right - so a model that cannot write it
    in the admins' language is unusable however fast it is.
    """
    reset_llm_config_validation()
    validate_llm_config()

    configured = get_openrouter_models()
    for banned in BANNED_MODELS:
        assert banned not in configured, (
            f"{banned} is banned for reason-language defects and must not occupy "
            "a fallback attempt"
        )


def test_pool_spans_more_than_one_provider():
    """One provider's outage must not empty the pool.

    The 2026-09-25 regression is the instance: a single dead model cost every
    fallback its first leg, and only provider diversity kept the tier alive.
    """
    reset_llm_config_validation()
    validate_llm_config()

    providers = {m.split("/", 1)[0] for m in get_openrouter_models()}
    assert len(providers) > 1, (
        f"pool spans one provider ({providers}); one outage would empty it"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
