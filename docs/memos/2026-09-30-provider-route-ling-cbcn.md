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
