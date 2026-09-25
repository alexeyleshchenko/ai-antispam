# OpenRouter fallback — model selection measured at production parity

**Date:** 2026-09-24
**Question (owner):** "Can we update model lists?" then "show me the reasons reported
by the proposed models."
**Scope:** the classifier's OpenRouter **fallback** tier (`llm.openrouter_models`).
The gateway leg is unchanged and unaffected.

---

## 1. Why the tier matters

The classifier tries the gateway first. When the gateway fails, the OpenRouter pool
is the only thing standing between a failed call and an unmoderated message. Measured
on the deployed service earlier the same day: **30 gateway failures, the fallback
rescued 3 (10.0%), leaving 27 messages (10.2%) unmoderated.**

So this is not a latency optimisation. It is the difference between moderation and
silence.

## 2. Method — production parity, not a reconstruction

Probe run **inside the deployed container** (`/app`), against the live Postgres.

| Faithfulness property | How it was satisfied |
|---|---|
| Prompt | the app's own `build_system_prompt()` + `format_spam_request()` + the app's own suffix |
| Few-shot corpus | `get_spam_examples(admin_ids)` — owner-scoped exactly as `pipeline.py` does it |
| Chat topic | read from `groups.topic_description_short`, passed as `chat_topics` |
| Language | derived exactly as `is_spam()` derives it (admin `language_code`, default `en`) |
| Budget | `get_llm_per_attempt_timeout()` — the app's own accessor, **15.0s** |
| Agent construction | `Agent(_create_openrouter_model(m), output_type=SpamClassification, ...)` — the same call production's `_openrouter_pool` makes |
| Leakage | a row was used only when its text is ABSENT from the few-shot block it would be shown |

**Bias in the candidates' favour, stated:** the probe's wall clock was
`per_attempt + 5 = 20s`, where production enforces a hard `asyncio.timeout(15s)`.
Every model therefore got **5s more than production would give it**.

**Sample:** 4 models × 8 calls = 32 calls. 8 messages (4 spam / 4 ham) — one spam
text appeared in two different groups, so **7 distinct messages**, and the row is
the message (this is the cross-group duplication measured separately at ~1.9–3.5%).

## 3. Result — the deployed pool is the broken part

| Model | Role | Success | p50 | max | Failure modes |
|---|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b:free` | **in config** | **1/8** | 11.2s | 11.2s | 3 TIMEOUT, 4 BAD_OUTPUT |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | **in config** | **1/8** | 8.3s | 8.3s | 2 TIMEOUT, 5 BAD_OUTPUT |
| `nex-agi/nex-n2.5-mini:free` | proposed | **8/8** | 2.1s | 5.5s | — |
| `dots-studio/dots-3-note-preview:free` | proposed | **6/8** | 8.6s | 9.5s | 2 TIMEOUT |

- **Zero wrong verdicts among successes** (all 16 successful calls correct). The
  failure mode is *availability and output validity*, never accuracy.
- The two configured models' 1/8 **independently corroborates the production figure**
  of 3/30 rescued = 10%. Two different instruments, same tier, same answer.
- `BAD_OUTPUT` is pydantic-ai failing to parse the model's response into the schema
  ("Exceeded maximum output retries" / "Invalid response … validation errors for
  ChatCompletion"). It is not a timeout — the model answered, and the answer was
  unusable.

**Context windows are not a constraint:** nex 262 144, dots 512 000, both well above
the ~28 000-char production prompt.

## 4. The reasons — verbatim

### 4.1 `nex-agi/nex-n2.5-mini:free` (8/8)

Language follows the derived `lang`. English rows:

> The vague recruitment-style post asks for men and women aged 16+ to take a paid side
> gig offering a fixed payout, without explaining the work. It is off-topic for a
> product/lifestyle chat and has clear promotional solicitation characteristics.
> *(SPAM, conf 89, 2.1s)*

> The message is an unsolicited solicitation for paid labor and directs interested users
> to private messages. It is off-topic for a LifeMart product updates, promotions,
> giveaways, and lifestyle tips chat, with no contextual need or supporting account
> signals.
> *(SPAM, conf 84, 2.0s)*

> This is a vague recruitment advertisement seeking men and women from age 16 for a
> poorly described side job with an unspecified employer, duties, or contact method.
> It is also off-topic for a product-update and lifestyle chat, making it likely
> promotional spam.
> *(SPAM, conf 82, 5.5s)*

Russian rows:

> Сообщение содержит конкретные вакансии с адресами, количеством смен, графиком,
> возрастом и ставкой. Рекламные или сомнительные признаки отсутствуют; био лишь
> описывает подбор персонала для ПВЗ.
> *(HAM, conf 94, 2.0s)*

> Сообщение выглядит как обычная локальная вакансия на замену смены в пункте
> Wildberries: указаны срок, оплата, примерное количество смен и место работы. В профиле
> и сообщениях отсутствуют признаки рекламы, мошенничества, бот-ссылки или агрессивного
> сбора контактов.
> *(HAM, conf 98, 2.1s)*

> Сообщение содержит конкретные детали разовой вакансии: даты, место, площадки WB и
> Ozon, ставку, объём поставок, график и возрастное ограничение. Признаков мошенничества,
> скрытой рекламы, подозрительных ссылок или манипулятивного запроса нет.
> *(HAM, conf 98, 2.7s)*

**Assessment:** concise, cites the actual chat topic, states what is absent as well as
what is present, and switches language correctly. No artefacts observed in 8/8.

### 4.2 `dots-studio/dots-3-note-preview:free` (6/8)

> The message is completely off-topic for a chat about 'LifeMart product updates,
> promos, giveaways, and lifestyle tips.' It is a recruitment advertisement for male
> and female workers ("М и Ж") from age 16, offering a salary of 4,450 in hand, with
> requirements like being 'free from duties' and 'no bad habits.' **This type of content
> is typical of escort or sexual services recruitment**, which is both off-topic and
> inappropriate. The user profile has no strong spam indicators, but the message content
> itself is clearly promotional and irrelevant to the chat's purpose.
> *(SPAM, conf 95, 9.5s)*

> The message offers 3500 ₽ to anyone who helps move some things, which is **a classic
> advance-fee scam pattern**. It is completely off-topic for a chat about product
> updates, promos, giveaways, and lifestyle tips. The user profile has no bio, no linked
> channel, and no stories, but the message content itself is a clear scam attempt.
> *(SPAM, conf 95, 4.7s)*

> Сообщение **содержит-legitimate запрос** на замену сотрудника в пункте выдачи
> Wildberries с конкретными условиями (3500 рублей, ~250 шк, метро Спартак, график
> 10-22). Это типичное объявление о подработке, а не спам. …
> *(HAM, conf 95, 8.6s)*

> … Это типичное **легитимное** объявление о поиске работы/замены. Профиль пользователя
> не содержит спам-индикаторов: нет ссылок в био, нет профессиональных званий или
> предложений о доходе в имени, отсутствуют signals о подозрительной активности.
> *(HAM, conf 95, 8.0s — note `отсутствуют signals`, an English noun in Russian prose)*

**Three defects, all on the admin-facing surface:**

1. **Language bleed** — `содержит-legitimate запрос`: an English word glued into Russian
   with a broken hyphen. Also `отсутствуют signals`, and in round 1 `… и формат
   объявления speak in favor of classification as spam`.
2. **Russian grammar errors** — `запрос на поиска` (should be `на поиск`).
3. **Over-interpretation** — "typical of escort or sexual services recruitment" and
   "classic advance-fee scam pattern" assert far more than the message supports. A
   moderator reading that could act on a claim the evidence does not carry.

**Plus 2 TIMEOUTs at the 20s wall** — under production's 15s they would fail too, so
its real success rate on this sample is worse than 6/8 suggests.

*(A typo seen in the same run — `легатимасным` — belongs to `ultra-550b`, one of the
configured models, not to dots. Recorded here so it is not misattributed.)*

### 4.3 The configured models' reasons — quality is fine, availability is not

Both produced good reasons on the rare call that succeeded. From `super-120b`:

> Сообщение выглядит как законное объявление о подработке/сменной работе в различных
> точках города. В тексте нет ссылок, рекламных призывов, ботов или внешних ссылок.
> Профиль пользователя не содержит признаков спама (bio пустой, нет linked channel,
> историй, account_signals). Похоже на типичный пост о вакансии, **аналогичный примерам
> в обучающей выборке, которые были помечены как не спам.**
> *(HAM, conf 100, 11.2s)*

That reason even cites the few-shot corpus — evidence the context families are reaching
the model. The model is not the problem; it cannot return a parseable answer inside the
budget.

## 5. Recommendation

| Slot | Model | Basis |
|---|---|---|
| 1 | `nex-agi/nex-n2.5-mini:free` | 8/8, p50 2.1s, max 5.5s — fits the 15s budget with 2.7× margin |
| 2 | `dots-studio/dots-3-note-preview:free` | 6/8 — marginal; better than 1/8, but its reason text needs review before it is trusted |
| — | both `nvidia/nemotron-*` | **drop** — 1/8 each, and they are what the tier currently runs |

Two models behind a 15s gateway give 15.0s per attempt
(`(45 − 15) / 2`), which both survivors' measured latencies meet.

**Not yet measured:** classification quality against a labelled set. This probe measured
*availability* and *reason text*. Accuracy on these 7 rows was 16/16, but n is far too
small to claim accuracy — the 809-row corpus is the instrument for that, and it is the
obvious next step before the list is treated as settled.

## 6. Open observation (not this decision's to make)

Rows whose group has no `group_administrators` mapping derive `lang="en"`, so the
admin-facing `reason` comes back in English. That is production behaviour, not a model
property — but it means a Russian-speaking admin can receive an English reason. Worth a
separate look.

---

## 7. Deployed — 2026-09-24 21:45:37Z

The section-5 recommendation is now the shipped configuration.

| | |
|---|---|
| Commit | `778be29` — both remotes (`alexey` and `origin`) |
| CI | run `36063370960` — build-push **success**, deploy **success** |
| Container | `2ccd6db3730f`, recreated **21:45:37Z**, healthy |
| Boot | `Configuration loaded successfully` → `LLM config validated` → `Verdict store ready` |

Verified **inside the running container**, not from the repo:

```
models      : ['nex-agi/nex-n2.5-mini:free', 'dots-studio/dots-3-note-preview:free']
gateway     : 15.0
per-attempt : 15.0
budget      : 45.0
```

`per-attempt 15.0` is the DERIVED value (`(45 - 15) / 2`), so the shipped app holds
the budget section 3's measurements assumed.

**The guard was shown to bite.** Adding a third model to `config.yaml` makes
`test_derived_per_attempt_budget_leaves_the_fallback_viable` fail with
`assert 10.0 >= 15.0`; `config.yaml` was restored byte-identical afterwards
(md5 `38e48b5488aa7ee894bb5e1e8449ea1e`). That is the check that pins the pool at two
models, and the reason the 15.0s floor sits above the slowest survivor's 9.5s max
rather than at it. Full suite at this commit: 559 passed, 2 skipped, 4 deselected.

### The baseline this change is judged against

From `classification_verdicts` on `apps`, read ~21:50Z, predicate
`created_at < the 21:45:37Z deploy`:

| Window | verdicts | failed | rate |
|---|---|---|---|
| pre-deploy (all rows) | **308** | **32** | **10.39%** |

Every row in the store predates the deploy, so this is a clean baseline: the old pool
left **10.39%** of classified messages unmoderated. The comparable figure is the same
predicate over a post-deploy window of similar traffic.

### What is NOT verified, and cannot be yet

The pool is exercised **only when the gateway fails**, and there were zero gateway
failures in the minutes after the deploy — so no rescue has been observed. Section 3
is therefore a production-parity measurement of the models taken 2h BEFORE the deploy
with the config values now shipped; it is not an observation of a live rescue.

The signal is in the app log: `Gateway spam classification failed: ..., trying
OpenRouter` followed by **no** `OpenRouter agent N/2 failed` line means the pool
rescued that message. The store's `failed` count is the aggregate form of the same
question.

---

## 8. OBSERVED LIVE RESCUE — 2026-09-25 (the section above is now discharged)

The signal section 7 named has fired. Read from the deployed container and the store
on `apps`, this turn.

**Gateway failures 06:06:32Z → 10:52:21Z** (13 in the 14h window; deploy was
21:45:37Z the previous day). Against them:

| Measure | Value |
|---|---|
| `spam_classifier_gateway_failure` | **13** |
| `spam_classifier_openrouter_call_1` | **14** — every call attempt 1 |
| `spam_classifier_openrouter_call_2` | **0** — slot 2 never reached |
| `OpenRouter agent N failed` | **0** |
| `All spam classifiers failed` | **0** |
| `Webhook processing timed out after` | **0** |

**The store, same turn** (`classification_verdicts`, predicate
`created_at > '2026-09-24 21:45:00+00'`):

| Window | verdicts | decided | failed | rate |
|---|---|---|---|---|
| pre-deploy baseline | 308 | 276 | **32** | **10.39%** |
| post-deploy (this read) | **190** | **190** | **0** | **0.00%** |

**One rescue traced end to end**, 10:52:21Z:

```
10:52:21.544  Gateway spam classification failed: , trying OpenRouter
10:52:21.557  spam_classifier_openrouter_call_1
10:52:21.568    openrouter-spam-nex-agi-nex-n2.5-mini:free run
10:52:23.600  _moderate -> try_deduct_credits -> deduct_credits_from_admins
              "Deducted 1* from <admin> in <group>"
```

Gateway failure → attempt 1 → moderation in **2.0 s**. The message was moderated, not
merely classified.

### The honest bound on "0 failed"

Zero failures in 190 is **not** a measured 0.00% rate. By the rule of three the 95%
upper bound on the true failure rate is **3/190 = 1.58%** — so the defensible claim is
that the rate fell from 10.39% to **below ~1.6%**, not to zero. A longer window tightens
it.

### What this still does NOT establish

**Slot 2 is unexercised in production.** All 14 calls went to attempt 1 (`nex`), so
`dots-studio/dots-3-note-preview:free` has never served a production message. Its 6/8
and its language-bleed defects remain a pre-deploy measurement, and the pool's
cross-provider diversity — the property that motivated a two-model list — is therefore
still untested. It will only be exercised when `nex` itself fails.

**Classification quality is still unmeasured.** This section is about availability. No
labelled-set accuracy run has been done on the shipped pair; the 809-row corpus remains
the instrument for that, per section 5.
