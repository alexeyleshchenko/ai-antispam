# Review — channel post draft (guest bots)

**Subject:** `docs/channel-post-guest-bots.md` @ `5219b62`
**Date:** 2026-10-26
**Method:** three independent sub-agent reviewers, each given the RU post body only (no code, no repo, no owner notes). Personas: (1) non-technical group/channel admin reading once in a feed, (2) logic/structure auditor, (3) adversarial skeptic. Findings below are de-duplicated across the three; every factual claim a reviewer made about Telegram or about our own behaviour was re-verified before being accepted here.

**Verdict:** the mechanic lands and the writing is disciplined. The post fails at its own payoff — it diagnoses a problem in four numbered steps and then never says what happens to those four messages. That is the single biggest defect, and two of the three reviewers found it independently.

---

## Blocking — fix before publish

### 1. The most quotable line is wrong

> «Итого: одно сообщение участника и три рекламных ответа за **0,09 секунды**.»

All three reviewers read this as the whole event taking 0.09 s. The measured facts are 9.374 s from the summon to the first reply, then 0.053 s and 0.035 s. The three replies span **0.088 s** — so the number is right for one thing and the sentence claims another. It also directly contradicts the timeline printed above it, which is the worst place for an arithmetic slip: a careful reader stops trusting every other number.

**Fix:** separate the two facts. «Первый бот ответил через 9,4 секунды. Два следующих — в течение 0,09 секунды после него.»

### 2. «Обычный аккаунт стоит телефонного номера» — broken sentence, bolded, load-bearing

Missing word; reads as a typo inside a definition. It is the premise the whole economics list rests on.
**Fix:** «Обычный аккаунт стоит **одного телефонного номера**.»

### 3. «аккаунты бесплатны» contradicts «аккаунт стоит номера»

Closing sentence of the list: «чёрные списки ботов тут бесполезны — список всегда отстаёт на один аккаунт, а аккаунты бесплатны.» The list's first bullet says accounts cost a phone number. Blurring account and bot cost at the conclusion is the one place the reader is most likely to remember it.
**Fix:** keep the distinction and say the stronger thing — bots are free and the list can never catch up, while the *account* is the thing that costs.

### 4. «сколько угодно ботов» is false

BotFather caps bot creation at **20 per account (40 with Premium)**; MTProto returns `BOT_CREATE_LIMIT_EXCEEDED` against `bots_create_limit_default` / `_premium`. Twenty throwaway bots per account is still plenty for the argument, but the sentence is checkable and wrong, and the wrong version is the more convenient one.
**Fix:** «с одного аккаунта можно создать десятки ботов».

### 5. The post never says what happens now — the payoff is missing

The opening is concrete and timestamped; the payoff is abstract: «проверяет такое сообщение как обычное», «наказание получает тот, кто вызвал бота». A reader cannot answer the two questions an admin actually has: *are the ads deleted? what happens to the person?* The only mention of deletion in the whole post is a subordinate clause about admins — the central operational fact is delivered by negation, in the wrong paragraph.

Verified in code, so the sentence can be written as fact:
- `handle_spam_message_deletion(message)` — the bot's reply is deleted (`handle_spam.py:193`);
- `ban_user_for_spam(chat_id, effective_user_id, …)` — and the ban lands on `effective_user_id`, which for a guest bot is the summoner (`utils.py:259`, `handle_spam.py:195`).

**Fix:** one sentence in the same register as the opening — «Если бы это повторилось сейчас: рекламные ответы удаляются, а аккаунт, который вызвал ботов, получает бан.»

### 6. «наказание» is never defined

Delete? Mute? Ban? For how long? For an admin the difference between "delete the message" and "ban a member" is the entire risk calculation, and the post leaves it open while using a word that sounds maximal.

### 7. No answer for the ordinary member who mentions a useful bot

The admin-exemption paragraph proves false positives were considered — and then answers only for admins. Silence about everyone else reads as "your members are on their own", and it is the first worry a real admin has (translators, QR generators, currency converters — mentioning a bot is normal, useful behaviour). **Fix:** one clause — mentioning a bot is not itself an offence; the ban follows a *spam* verdict, and an admin can reverse it with the "not spam" button.

---

## Should-fix

| # | Finding | Fix |
|---|---|---|
| 8 | «каждый появился ровно один раз» contradicts «участником которого **не является**» — "появился" implies he joined; he never did | «каждый ответил ровно один раз и больше не появлялся» |
| 9 | The post ends on the admin exemption — a carve-out that plants a new worry ("could *I* be banned?") at the last moment, deflating the strongest sentence in the piece | move the exemption up next to "who gets punished"; end on the cost asymmetry |
| 10 | Footer has no verb: «Бот: … · Сайт: …». Reader is left doing nothing | one verb, not a how-to section: «Добавьте бота в группу — он проверит такие сообщения.» |
| 11 | «в мае этого года» — relative date in an undated post; rots the moment it is forwarded | «в мае 2026 года» |
| 12 | «Раньше бот мог писать только там, куда его добавили» — inline mode already put bot content into chats the bot was not in | acceptable simplification for this audience, but a technical reader will flag it; consider «не мог писать в чат сам, от своего имени» |
| 13 | Five bullets carry about three distinct ideas — 2 and 3 both say "many bots", 4 repeats 2 with a blacklist angle | tighten to 4 |
| 14 | «список всегда отстаёт на один аккаунт» — a new figure nothing supports, and it *understates* the case | «список не догонит: боты бесплатны» |

---

## Owner decision — not the author's to make

**Content protection.** Guest bots cannot be queried at all in groups with content protection enabled (`core.telegram.org/api/bots/guest-mode`). That is the one toggle that kills the attack outright, and the post is silent about it — while recommending the vendor's bot. The skeptic's read: the free alternative to the product is omitted, which looks self-serving whether or not it is.

The owner cut the "как проверить свою группу" section on 2026-10-26 and that decision stands. But "можно ли это выключить?" is the first question an admin asks after "чёрные списки бесполезны", and the honest answer is a single clause: *отключить это на уровне группы нельзя — поэтому мы ловим инициатора.* That clause is honest **and** strengthens the sales case. One clause is not a how-to section, but re-adding the fact is the owner's call, not an edit to make quietly.

---

## Where the reviewers disagreed

- **Timeline's role.** The auditor calls 9.4 s / 0.05 / 0.04 "decoration" because nothing is concluded from them; the non-technical reader found the sequence genuinely gripping and would keep reading because of it. Both are right: it earns attention, it does not earn an argument. Keeping it and *interpreting* it (automation, not humans) satisfies both.
- **Incident credibility.** The skeptic calls the timings credible in shape ("one backend generating once, then firing from three bots — exactly how a single operator behaves") but says tenth-of-a-second precision with no artefact shown is over-confident. The detail that reads as most real to all three: *«тот же текст с другим вступлением»* — nobody invents that.
- **Self-interest disclosure.** Only the skeptic raised it, and it is a genuine structural point: first person appears for the first time at the heading «Что мы сделали», three-quarters down. A single clause early («мы делаем антиспам-бота») would defuse it — but it also changes the post from a warning into an announcement, which is the owner's editorial call.

---

## What all three agreed on

1. The incident opening works and should stay.
2. The mechanic explanation is clear and "гостевые боты" is a term that explains itself.
3. No API jargon survived the rewrite — that instruction held.
4. The post reads as 60 % warning / 40 % pitch, and the pitch half is the half that gives the reader nothing to do.
5. Fixing the payoff (finding 5) is worth more than every other edit combined.
