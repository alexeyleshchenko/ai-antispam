# Fallback pool: reason language and verdict accuracy

**Date:** 2026-09-25
**Owner directive:** "ban ling" (after being shown that `ling-3.0-flash-fin`
wrote its classification reason in Chinese).
**Supersedes for model choice:** `2026-09-24-llm-fallback-model-selection.md`
(that memo's latency-only selection is what put a Chinese-writing model in the
pool; its budget findings still stand).

---

## 1. Why the pool was replaced

`inclusionai/ling-3.0-flash-fin:free` was shipped on 2026-09-25 as the fast leg
of the fallback pool, selected on **latency alone**. It served every fallback
rescue. Then a production verdict was found whose `reason` was **entirely
Chinese** — a 141-character CJK run.

`reason` is the **admin-facing field**: it is what the group owner reads to
decide whether the bot was right. A model that cannot write it in the admins'
language is unusable however fast it is. Banned by the owner.

### The measurement, before acting on the ban

Over 494 verdict reasons in 24 h, **4 rows carried CJK**. Separating the two
causes matters, and the first pass got this wrong:

| Cause | Rows | Verdict |
|---|---|---|
| the model's own prose in Chinese | **2** (one 141 chars, one predating ling) | defect |
| the **user's own** CJK display name, quoted in the reason | 2 | **legitimate** |

The 5-CJK-char row at 10:44:16 is the second class: the reason quotes the
sender's name `キスして。`. A naive "any CJK in reason" check would have
condemned a correct reason. Any future detector must subtract CJK runs that
also appear in the user's own text/name.

---

## 2. Candidate measurement — production parity, three axes

Harness: the deployed container, the app's own `build_system_prompt` +
`format_spam_request`, owner-scoped corpus, **populated** context
(name/bio/reply/account_signals/chat_topics), the derived 15.0 s per-attempt
budget, and real **confirmed** rows drawn in equal spam/ham numbers so accuracy
is measurable. Scored on availability, **verdict accuracy vs the admin's own
label**, and the reason's language.

| model | returned | correct | reasons ru | CJK | p50 | max |
|---|---|---|---|---|---|---|
| **dots-3-note-preview** | 7/10 | **7/7** | 7/7 | 0 | 10.2 s | 15.0 s |
| **liquid/lfm-2.5-2.6b** | **9/10** | 4/9 | 7/9 | 0 | 7.5 s | 12.2 s |
| cohere/north-mini-code | 5/10 | 3/5 | 5/5 | **1** | 15.0 s | 15.1 s |
| poolside/laguna-s-2.1 | 2/8 | 2/2 | 2/2 | 0 | — | 12.2 s |
| nvidia/nemotron-3-super-120b | 1/8 | 1/1 | 1/1 | 0 | — | 15.0 s |
| qwen3.8-27b | 0/8 | — | — | — | 15.0 s | 15.0 s |
| gemma-4-26b / 4-31b | 0/8 | — | — | — | 0.2 s | 0.3 s |
| inkling / inkling-small | 0/8 | — | — | — | 0.1 s | 0.2 s |
| nemotron-3.5-content-safety | 0/6 | — | — | — | 0.1 s | 0.8 s |
| poolside/laguna-xs-2.1 | 0/4 | — | — | — | 15.0 s | 15.0 s |

**Accuracy is the finding that changed the pick.** `liquid` is the most
*available* candidate (9/10) and was nearly chosen on that basis — but it
answered only **4 of 9** correctly and wrote several reasons in **English**.
Availability without accuracy is not a rescue. `dots` is the opposite shape:
slower and less available, but **7/7 correct, 7/7 Russian**.

### Excluded on reason language

- **ling-3.0-flash-fin** — banned (owner directive).
- **cohere/north-mini-code** — wrote Chinese in 1 of 5 measured reasons. Same
  defect, same standard. Rejected even though its reasons were otherwise good.

---

## 3. What shipped

```yaml
openrouter_models:
  - dots-studio/dots-3-note-preview:free   # accuracy lead
  - liquid/lfm-2.5-2.6b:free               # availability net
```

Derived budget unchanged and verified: `budget 45.0`, `gateway 15.0`,
`per-attempt 15.0` = `(45 − 15) / 2`, total legs 45.0 ≤ 55 − 10 reserve.
Two providers (AtlasCloud, Liquid) so one outage cannot empty the pool.

**Guard:** `BANNED_MODELS` in `tests/test_classifier_fallback_budget.py` pins
ling, alongside the existing `DEAD_MODELS`. Distinct concepts — `DEAD_MODELS`
is *unreachable*, `BANNED_MODELS` is *reachable but must not serve*. Shown to
bite: re-adding ling fails 2 tests.

---

## 4. Honest limits

- **Accuracy is measured on 10–15 rows per model**, drawn from the flagged band
  — the messages the incumbent found hard. It is not a corpus-scale benchmark.
- **`liquid`'s 4/9 is a warning, not a verdict** — the pool now depends on
  `dots` being first and `liquid` catching only its timeouts.
- **Slot 2's accuracy matters less than slot 1's**, because it only runs when
  slot 1 times out — but a wrong verdict from slot 2 still reaches an admin.
- **The gateway leg's own reasons were checked** in the same pass and are clean
  Russian; this defect is specific to the fallback tier.
- **No detector is added to production code.** The CJK scan was diagnostic.
  A runtime language check on `reason` is a reasonable follow-up and is NOT
  implemented here — stated so its absence is not read as coverage.
