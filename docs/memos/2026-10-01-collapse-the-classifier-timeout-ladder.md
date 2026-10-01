# Collapse the classifier timeout ladder — measurement record, 2026-10-01

**Scope.** The classifier's LLM timeout budget: `config.yaml` (`llm:`), `src/app/common/llm_budget.py`,
`src/app/spam/spam_classifier.py`, `src/app/handlers/private_handlers.py` — and the `ai-antispam` route
on the shared gateway (`/data/projects/ai-gateway/config.yaml` on `apps`).

**Owner directives in force.**

- 2026-10-01T00:08Z — *"I think the timeout ladders are wrong and it's hurting us. We need to lift some
  self-inflicted requirements to simplify all of this and make it work. Ling we already tried — it
  doesn't reliably produce reason in Russian."*
- 2026-09-30 — *"Forget any other providers except inferhub (you may create combos there for your
  convenience and free openrouter models)."*

## The defect: five walls derived from each other

The classifier's "budget" was not a budget. It was five walls, nested across three components, each one
load-bearing for the next:

| # | Wall | Owner | Value before |
|---|---|---|---|
| 1 | per-step timeout | gateway route `ai-antispam`, step 0 | 8 s |
| 2 | route timeout | gateway route `ai-antispam` | 14 s |
| 3 | client leg | app `llm.gateway_timeout_seconds` | 15 s |
| 4 | per-attempt budget | app, derived `(budget − gateway) / n` | 15 s |
| 5 | webhook guard | platform `system.webhook_timeout` | 55 s |

The derivation was a closed loop:

```
per_attempt = (budget_seconds - gateway_timeout_seconds) / len(models)      # llm_budget.py
per_attempt >= MIN_VIABLE_PER_ATTEMPT_SECONDS = 15.0                        # test floor
budget_seconds + WEBHOOK_RESERVE_SECONDS(10) <= webhook_timeout(55)         # llm_budget.py
```

Substituting: `gateway + 15n <= 45`. With the pool pinned at `n = 2`, **the gateway leg was locked at
≤ 15 s** — and the gateway route's own steps then had to sum under that leg, or the client cancelled
mid-route and no later step was reachable.

So: the webhook guard fixed the budget → the budget and the 15 s floor fixed the gateway leg → the
gateway leg fixed the route's step walls → **the step walls are what broke, twice in three days**, each
time taking the classifier leg down completely. Every wall was load-bearing for the next; **none was
derived from a measured latency**. The 15 s floor itself was measured on 2026-09-25 against *OpenRouter
free* models, a tier that had since been abandoned.

The second self-inflicted requirement was architectural: **the classifier had to traverse the gateway.**
That was justified when the gateway offered cross-provider failover. Under the inferhub-only directive
the route pointed at the same provider the app already fell back to, so the hop bought a cancellation
boundary and cost three walls.

## The change (Option A — one hop, one layer)

The gateway hop is **removed from the classification path**. The app calls the inferhub pool directly.

`config.yaml` — the arithmetic lifted, and the block comment rewritten to state the measured basis
instead of the falsified 15 s-floor rationale:

```yaml
llm:
  budget_seconds: 32           # total classifier LLM budget; reserve 10s for the webhook
  gateway_enabled: false       # the gateway hop is out of the classification path
  gateway_timeout_seconds: 0   # 0 = no gateway leg; the pool owns the whole budget
  route_timeout_seconds: 30    # non-classification LLM legs (topic derivation, admin DM)
  openrouter_models:
    - ag/gemini-3.7-flash-high
    - cb/deepseek-v4.1-flash
```

Derived per-attempt becomes `(32 − 0) / 2 = 16.0 s`. Worst case 32 s, inside the 55 s guard with 23 s
of slack. There is no step wall and no route timeout in the path, so **a config edit to the gateway can
no longer take the classifier leg down** — which is the failure that cost three outages in three days.

`src/app/common/llm_budget.py` — the gateway leg may now be absent:

- `_parse_non_negative_number` accepts `gateway_timeout_seconds: 0` (the positive-only parser rejected it).
- `gateway >= budget` is only a defect when the hop is enabled; `gateway_enabled and gateway <= 0` is
  refused instead.
- `gateway_enabled` is recorded in the validated dict, and `get_llm_gateway_enabled()` exposes it.
  **An absent key defaults to `true`**, so an un-migrated config behaves exactly as before this key existed.
- `per_attempt = (budget − gateway) / n` is unchanged, so it stays correct at `gateway = 0`.

`src/app/spam/spam_classifier.py` and `src/app/handlers/private_handlers.py` — **both** gateway call
sites wrapped in `if get_llm_gateway_enabled():`. The pool loop below already handled the rest; it is now
the primary path rather than the fallback. The block is kept, not deleted, for a one-line rollback and
for any consumer still routing through the gateway.

`tests/test_classifier_fallback_budget.py` — the floor replaced with a measured one:
`MIN_VIABLE_PER_ATTEMPT_SECONDS` 15.0 → **12.0** (low enough not to fight the 16 s derivation, high
enough that a starved pool is still refused), plus `test_classifier_does_not_route_through_the_gateway`,
which asserts `gateway_enabled is False` and `gateway_timeout_seconds == 0`, and **fails against the
pre-collapse config**. `inclusionai/ling-3.0-flash-sante:free` added to `BANNED_MODELS`.

Commits: `30ca958` *feat(llm): allow the gateway leg to be disabled* (llm_budget.py only),
`a3b3cd5` *feat(classifier): call the inferhub pool directly, dropping the gateway hop* (6 files).

## Deploy verification

Deployed `2026-10-01T09:42:34Z` on `ghcr.io/leshchenko1979/ai-antispam:main`. All three touched
source files are byte-identical local ↔ container:

| File | md5 |
|---|---|
| `spam_classifier.py` | `ec898dbd6cfeb709b6c38569d2379181` |
| `llm_budget.py` | `ec9a3b9d7ca9ebdaf7d5b12a8044b2bb` |
| `private_handlers.py` | `54b2d04bcfa17017ecd6a223b6e08372` |

Live validated config inside the running container: `budget_seconds 32`, `gateway_enabled False`,
`gateway_timeout_seconds 0`, `route_timeout_seconds 30`, derived `per_attempt_timeout_seconds 16.0`,
`openrouter_models [ag/gemini-3.7-flash-high, cb/deepseek-v4.1-flash]`.

**The classification path does not traverse the gateway.** Two independent reads, both post-restart:

- App spans: `spam_classifier_gateway_call` **0**, `spam_classifier_openrouter_loop` **6**.
- Traefik access log, by origin: the app container's origin (`172.18.0.1`, the `apps` docker bridge)
  made its **last** gateway call at **09:38:48Z** — *before* the restart — and **zero** after it.

## The traffic still on the route is NOT this consumer

The `ai-antispam` route keeps receiving traffic after the restart, but the caller is the **outreach
lane**, not the bot. `outreach-watch-stream.service` (on the `agents` host, `78.17.187.4`) runs a
300 s scoring loop; its `considered=` count equals the gateway burst size in the same window, and
Traefik shows those POSTs originating from `78.17.187.4`. Two discriminators separate it from the app's
traffic, both read from the gateway's own request log:

| | App's classification request | Outreach scoring request |
|---|---|---|
| system prompt opening | *"…for Telegram **groups**."* | *"…for Telegram **discussion groups**."* |
| user content | full context JSON (bio, channel, stories, account signals, chat_topic) | `{"message": "<one text>"}` |
| `prompt_tokens` | 7,793 | ~5,100 |

So **criterion 3 is met for this consumer**, and the route is genuinely still in use by another lane —
which is why Task 8 says leave it in place rather than delete it.

**Open, and owed to Task 8:** that route's step 0 is still `inclusionai/ling-3.0-flash-sante:free`
(the owner-banned model, `step_timeout: 8s`), and the gateway loaded it at its own
`2026-09-30T23:45:23Z` start. The outreach lane is therefore being served by a banned model today.
That is a live ban violation for the *other* consumer, and it is the reason the route must not simply
be deleted out from under them.

## Production-parity measurement

Prompt: the real captured classifier payload `/tmp/oc-prompt.json` on `apps` — system 27,399 chars +
user 344 chars (27,743 total), the same file the 2026-09-29 basis was taken on. Body shape read
**verbatim from the app's own request** in the gateway log (pre-restart): `messages`, `model`,
`stream: false`, `tool_choice: "required"`, `tools[final_result]` (strict; `is_spam`/`confidence`/`reason`),
and **no `max_tokens`**. Endpoint `FALLBACK_API_BASE` (`https://api.inferhub.dev/v1`), credential
`FALLBACK_API_KEY`. 10 calls per model, 20 total. A call counts as OK only when it returns a **valid
`final_result` tool call**.

| Arm | ok | min | p50 | p90 | max | over the 16 s wall |
|---|---|---|---|---|---|---|
| `ag/gemini-3.7-flash-high` | 10/10 | 3,764 ms | 11,272 ms | 31,684 ms | 31,684 ms | 2/10 |
| `cb/deepseek-v4.1-flash` | 10/10 | 2,910 ms | 22,555 ms | 38,562 ms | 38,562 ms | 5/10 |
| **pool (n=20)** | **20/20** | **2,910 ms** | **12,415 ms** | **31,684 ms** | **38,562 ms** | **7/20 (35 %)** |

Against the plan's baseline (**p50 11.4 s / p90 108.4 s**, production, 24 h): p50 is **level**
(12.4 s vs 11.4 s) and p90 is **far better** (31.7 s vs 108.4 s) — but the baseline's p90 is *retries*,
as the plan itself states, so the p90 comparison is not like-for-like and this change must not be
credited with fixing it.

## A finding this measurement forces: the 16 s wall is tight for the measured tail

The 2026-09-29 basis recorded `cb/deepseek-v4.1-flash` at **max 12.46 s** and set the 16 s per-attempt
wall against it. Today the same model on the same prompt reads **p50 22.6 s / max 38.6 s**, and
**7 of 20 pool calls (35 %) exceed 16 s**. Two explanations are live and this memo does **not** claim to
have separated them:

1. **Regime.** The provider is materially slower in this window than on 2026-09-29. Two independent
   runs an hour apart differed by ~2× (`ag/gemini` p50 15.1 s vs 11.3 s), which is itself evidence of a
   noisy regime — and a synthetic 0.5 s-gap probe loop is not organic traffic, so neither figure is a
   production rate.
2. **Method.** The earlier harnesses in `/tmp` set `max_tokens: 400` (see `/tmp/oc-cand.py`); the app
   sends **no** `max_tokens`, and this measurement matches the app. A cap the app does not impose would
   have made the earlier basis optimistic. Not verified — recorded as a hypothesis, not a fact.

**How production absorbs it (observed, n=3):** the wall is not fatal, because the pool rotates. Two of
the three post-deploy classifications landed at **24.77 s** and **27.81 s** — i.e. model 1 hit the 16 s
wall and model 2 finished in 8.8 s / 11.8 s, inside the 32 s budget. The third succeeded at **7.04 s**.
Post-deploy production therefore reads **p50 24.77 s, p90 27.20 s, max 27.81 s (n=3)** against
**p50 20.88 s, p90 130.27 s, max 351.46 s (n=324)** for the preceding 24 h.

The risk is stated plainly: if **both** models run past 16 s on the same message, the 32 s budget is
exhausted and the classification fails with *"All spam classifiers failed"*. At a 35 % over-16 s rate in
this regime that is not negligible. **Recommendation: re-measure over a longer organic window before
tightening anything further, and treat the per-attempt wall as the next thing to tune if failures
appear.** This is a follow-up item, not a defect of the collapse.

## Language: what this measurement may and may not say

`/tmp/oc-prompt.json` is an **en-locale** prompt — it carries *"Write in English"* twice, from
`locales/en.yaml:378`. The harness therefore **must not** be read as a language measurement: its
non-Cyrillic reasons are an artifact of the snapshot, not of the pool.

Production runs the **ru** locale, and its reasons are Russian: over the 24 h to 2026-10-01T10:00Z,
**253 of 327** verdicts are predominantly Cyrillic, only **4** predominantly Latin. `locales/ru.yaml:377`
carries `"Пиши по-русски."`, which is the citation the ling ban rests on — and that citation is correct
for the locale production actually uses.

## Suite and guard

- The new guard `test_classifier_does_not_route_through_the_gateway` **fails against the pre-collapse
  revision** (`assert True is False`, 1 failed / 6 passed) and passes after — the pre-fix output is the
  evidence the guard tests something.
- Full suite after the change: **995 passed, 4 skipped**.
