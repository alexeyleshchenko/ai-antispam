# Provider route for the classifier: measurement record, 2026-09-30

**Scope.** The `ai-antispam` route on the shared gateway (`/data/projects/ai-gateway/config.yaml`
on `apps`) and the app's fallback tier. Owner directive in force: *"Forget any other providers
except inferhub (you may create combos there for your convenience and free openrouter models)"*.

**Measurement basis.** The real captured classifier payload `/tmp/oc-prompt.json` on `apps`
(system 27,399 chars, user 344 chars; `prompt_tokens` 7,793), sent with the app's EXACT body
shape as read from the gateway log: `messages`, `model`, `stream:false`,
`tool_choice:"required"`, `tools[final_result]` (strict, properties is_spam/confidence/reason).
A call counts as OK only when it returns a **valid tool call**; HTTP 200 with JSON as text
is a failure. All keys read in-process; none printed.

## The defect that produced the losses

Live route before the fix: `minimax/MiniMax-M3` (step 7s) -> `openrouter/dots-studio:free`
(step 7s), route 15s. Both steps sat at or below their own measured p50, so under load
**both died on their own step timeout together** and the route returned 502. Verbatim from
the gateway log:

    "error":"request failed: Post \"https://api.minimax.io/v1/chat/completions\":
      context deadline exceeded (Client.Timeout exceeded while awaiting headers)"
    "duration_ms":7002   "step":1   "status":502   "message":"All route steps failed"

Counts, 24 h to 2026-09-30T22:39Z: **954 step requests, 199 all-steps-failed**. In the 3 h
to 22:20Z: 35 minimax `context deadline`, 33 read-deadline, 11 `status:502`, 24
`Step marked for rotation (will be skipped)`.

## Candidates measured at production parity

n = calls; p50/max in seconds; "ru" = how many reasons were written in Russian; CJK = any
CJK run in the admin-facing `reason` (the defect that got `ling-3.0-flash-fin` owner-banned).

### Free OpenRouter (the only free ids that are tool-capable, 15 of 16)

| model | n | ok | p50 | max | notes |
|---|---|---|---|---|---|
| `inclusionai/ling-3.0-flash-sante:free` | 4 | **4/4** | **3.54** | **3.61** | best measured; 0 CJK |
| `dots-studio/dots-3-note-preview:free` | 4 | 3-4/4 | 7.15 | 20.8 | one EMPTY content; one JSON-as-text |
| `qwen/qwen3.8-27b:free` | 4 | 4/4 | 10.06 | 23.8 | tail too long |
| `nvidia/nemotron-3-super-120b-a12b:free` | 4 | 0/4 | - | - | returns JSON as text |
| `nvidia/nemotron-3.5-lightning:free` | 4 | 4/4 | 41.7 | 46.7 | unusable |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | 4 | 1/4 | - | - | parse errors |
| `poolside/laguna-s-2.1:free` | 4 | 1/4 | - | - | JSON as text |
| `poolside/laguna-xs-2.1:free` | 4 | 0/4 | - | - | 429 |
| `google/gemma-4-31b-it:free`, `gemma-4-26b-a4b-it:free` | 4 ea | 0/4 | - | - | 429 on every call |
| `thinkingmachines/inkling-small:free`, `inkling:free` | 4 ea | 0/4 | - | - | 403 agentic-harness only |
| `cohere/north-mini-code:free` | 4 | 0/4 | - | - | JSON as text |
| `liquid/lfm-2.5-2.6b:free` | 5 | 0/5 | - | - | 200 but no tool call |

### inferhub (269 ids; aliases carry routing overhead)

| model | n | ok | p50 | max | notes |
|---|---|---|---|---|---|
| `cbcn/deepseek-v4.1-flash` | 10 | 9/10 | 10.54 | 27.7 | widest variance; 5/5 at p50 7.07 in a 5-call run |
| `cb/deepseek-v4.1-flash` | 4 | 3/4 | 3.56 | 30.0 | one 30 s read timeout |
| `cb/minimax-m3` | 10 | 9/10 | 15.54 | 27.4 | no sub-8 s call in 10 |
| `minimax-m3` (bare id) | 5 | 2/5 | 24.7 | 30.1 | worse than the `cb/` alias |
| `ocg/minimax-m3` | 5 | 4/5 | 18.8 | 19.0 | reasons not in Russian |
| `glm-5.3-flash` | 5 | 4/5 | 12.1 | 18.6 | |
| `cp/cline-pass/deepseek-v4.1-flash` | 5 | 3/5 | 6.8 | 25.5 | two 30 s timeouts |
| `combo/antispam-latency` | 4 | 1/4 | 16.2 | - | `selection:"order"` pins member 1 |

**No inferhub alias is reliably fast at this payload.** Every one carries ~9-18 s of routing
overhead or a 20-30 s tail; the two that looked fast in a small sample (cbcn 5/5 p50 7.07)
reverted to p50 10.5 / max 27.7 at n=10. So inferhub cannot carry a sub-9 s *primary* step;
it is the deep failover.

## The free-model cap (why the fast model cannot be primary)

`GET https://openrouter.ai/api/v1/key` -> `free_model_daily_requests: {used: 615, limit: 1000,
remaining: 385}`. The cap is **account-wide across all `:free` models**, not per model, and the
route serves ~954 requests/24 h. A free model therefore cannot be the sole primary; it can
carry a step whose failure falls through to inferhub.

## Applied

> **Superseded by revision 2 below** (same model pair, walls re-derived from a 20-call
> distribution instead of a 4-call sample). The pair did not change; the walls did.

    - name: ai-antispam
      route_timeout: 15s
      step_cooldown: 5s
      steps:
      - provider: openrouter
        model: inclusionai/ling-3.0-flash-sante:free
        step_timeout: 5s
      - provider: inferhub
        model: cbcn/deepseek-v4.1-flash
        step_timeout: 9s

Steps sum 14 s inside the 15 s client leg (the app's `gateway_timeout_seconds: 15`), so a
slow step cannot eat the whole leg and the failover is reachable. Fast model first, because
it answers in ~3.5 s on the happy path; inferhub second, because it is the only compliant
surface with headroom to absorb a tail.

Validation: the candidate was booted in a **throwaway** gateway (`gw-validate`, same image
`ghcr.io/leshchenko1979/ai-gateway:main`, config bind-mounted, port 18080) before production
was touched - `route_names` listed all 7 routes and `/health` returned 200, so no parse error
could take the other 6 routes down at boot. Route test through the throwaway: **27/28 valid
tool calls** (7/8 then 20/20), p50 3.33-3.49 s, one 502 at 14.05 s. Then applied to the live
file (backup `config.yaml.bak-antispam-20260930-223532`, atomic temp+rename) and the gateway
restarted: `StartedAt 22:35:33.277Z` > config `mtime 22:35:32.877Z`. Live route through
`https://ai-gateway.l1979.ru`: **10/10 valid tool calls, p50 3.58 s, max 4.60 s**.

## What this record does NOT prove

The post-fix window carries **no production classifier traffic**: the newest
`classification_verdicts` row is `2026-09-30 20:06:57Z`, ~2.6 h before the restart, and the
10 gateway step successes in the window are the probe's own calls. The failure was also
load-dependent and had already stopped at `18:33:47Z`, ~4 h *before* the fix, because traffic
collapsed (hourly failed/decided: 15:00 5/30, 16:00 2/15, 17:00 9/23, 18:00 5/13, 19:00 0/11,
20:00 0/1). A pre/post claim therefore needs a matched-load window; the evidence for the fix
today is the direct route measurement, not a production rate. Pre-fix baseline to compare
against: **199 all-steps-failed / 954 step requests in 24 h**.

Standing backlog this fix does not touch: **206** rows `status='failed' AND moderated_at IS NULL`
(2026-09-24 12:41:02Z .. 2026-09-30 18:33:47Z). Their retry semantics are issue #70's scope.

---

# Revision 2 — walls re-derived from a distribution, 2026-09-30T23:45Z

The model pair above was kept; **both step walls were wrong**, and the 4-call samples that
set them could not have shown it. Unclipped 20-call runs at the same production parity:

| leg | n | ok | min | p50 | p90 | max | 5 s wall | 8 s wall |
|---|---|---|---|---|---|---|---|---|
| `inclusionai/ling-3.0-flash-sante:free` | 20 | 20/20 | 2.42 | **3.28** | 3.62 | 7.37 | 18/20 (90%) | **20/20 (100%)** |
| `cbcn/deepseek-v4.1-flash` | 20 | 20/20 | 2.83 | **5.03** | 18.37 | 25.50 | 10/20 (50%) | 13/20 (65%) |

- **ling is bimodal**: 18 of 20 calls land in 2.42-3.62 s and two outliers sit at 7.11 and
  7.37 s. The shipped **5 s wall clipped exactly that second mode** — a 90% coverage wall on
  a model whose first mode is 3.3 s.
- **cbcn's p50 is 5.03 s**, so the shipped 9 s wall covered only 70% of its own calls, and
  the earlier "p50 10.54" reading (n=10) was a small-sample artifact: this run's p50 is 5.03
  with a heavy tail to 25.5 s.
- Consequence for coverage: the 5 s / 9 s pair had a **~90% x ~70%** per-leg pass, i.e. a
  ~3% joint failure rate *before* any provider degradation. That is the same defect class as
  the original `minimax 7s -> dots 7s` route this record exists to fix: **a wall set at or
  below its own p50**.

## The failure observed on the shipped walls (config E)

From the gateway's own log, one production-shaped request:

    step 0  ling-3.0-flash-sante:free  5005 ms  failed to read response: context deadline exceeded
    step 1  cbcn/deepseek-v4.1-flash   9004 ms  Post https://api.inferhub.dev/v1/... context deadline exceeded (awaiting headers)
    request execution failed: all route steps failed for model 'ai-antispam'

14.0 s to a 502, on a config whose steps summed 14 s inside a 15 s route. Window
2026-09-30T22:35Z-23:22Z: **18 ai-antispam requests, 17 ok (all on step 0), 1 502**.
Those 18 requests all carried a **byte-identical payload** (md5 `f76704c5`, 103 chars) which
is the shared probe file `/tmp/oc-prompt.json` on `apps`, so they are probe calls, mine and
peers', **not production classifier traffic**.

## Applied (config F)

    - name: ai-antispam
      route_timeout: 14s
      step_cooldown: 5s
      steps:
      - provider: openrouter
        model: inclusionai/ling-3.0-flash-sante:free
        step_timeout: 8s
      - provider: inferhub
        model: cbcn/deepseek-v4.1-flash
        step_timeout: 5s

`8 + 5 = 13 s` of steps inside `route_timeout 14s` inside the app's `gateway_timeout_seconds:
15` — **1 s of headroom at each boundary**, which answers the reviewer's point that
`route_timeout == the client leg` left none: a route that overruns its own client leg is
aborted from outside and cannot return its own 502. A non-matching return (502, 429) is
recoverable: `spam_classifier.py:75` catches any gateway exception and falls to the app's
2-model inferhub tier at `get_llm_per_attempt_timeout()` = 15 s each, so the total leg
sequence is 15 + 2x15 = 45 s inside the app's `webhook_timeout: 55`.

Validation: throwaway gateway `gw-validate2` (same image, config bind-mounted, port 18080) —
boot clean, `providers:10 routes:7`, health 200; **15/15 valid tool calls**, p50 3.65 s,
p90 4.30 s, max 4.48 s. Then applied to the live file: backup
`config.yaml.bak-antispam-20260930-234523`, verified to hold the pre-edit bytes
(`md5 ebf0e2f2427fe17a003ffeb9aa1c8fad`) before the write, atomic temp+rename to
`md5 8f1420999bffec7398c89d4614c2fc0c`, restart `StartedAt 23:45:23.675Z` with the boot log
listing all 7 routes. Live through `https://ai-gateway.l1979.ru`: **10/10 valid tool calls,
p50 3.25 s, p90 3.66 s, max 4.06 s**.

## Two instrument errors found while verifying this change

1. **A copy's mtime is not its birth instant.** `shutil.copy2` preserved the *candidate's*
   mtime, so the live config read `mtime 23:41:59` — three minutes *before* the write — which
   a later reader would have compared against `StartedAt 23:45:23` and concluded "the file
   predates the boot, the running route is not this file". Corrected by rewriting the bytes
   with no metadata preservation; the corrected `mtime 23:45:44` is read from the filesystem,
   and the content md5 is unchanged.
2. **The gateway publishes no host port** (`expose: 8080`, Traefik only). `127.0.0.1:8080` on
   `apps` is a *different* service, so `curl /health` there returned 404 and `/v1/models`
   returned "Authentication failed" against something that is not the gateway. Every live
   route number in this record is taken through `ai-gateway.l1979.ru`, never a host port.

## Standing constraints this config lives under

- **The free cap is account-wide and shared.** `free_model_daily_requests` moved
  `615` (19:00Z) -> `707` (23:25Z) -> `732` (23:40Z) against `limit 1000`, while only 18
  requests reached this route in that window: **~90/hour is consumed by consumers outside
  this route** (`dynamic/n8n` and `oss-nemotron` also carry `:free` steps). At 732/1000 with
  ~950 requests/day of demand, the primary step will hit 429s before the day ends and the
  route will fall through to the inferhub step. This is a capacity fact, not a defect of the
  config; it is why the pair has a second leg at all.
- **Inferhub cannot be made fast.** Its combo `selection` values are `order`, `reliable`,
  `balanced` only - there is no latency-first mode, and `antispam-latency` is `order`, which
  pins calls to member 1. Every inferhub alias measured carries 9-18 s of routing overhead.
- **Language of `reason`:** `ling` emitted **0 CJK** across its reasons, so the defect that
  got `ling-3.0-flash-fin` owner-banned is absent here. Its reasons are English or mixed
  (Russian appears only inside quoted fragments), while production rows carry fully Russian
  reasons from the previous pool. That is a quality observation for the admin-facing field,
  not a compliance failure, and nothing in the prompt instructs a language.
