# Channel post draft — guest bots ("hire a bot to say it")

**Channel:** @ai_antispam
**Status:** DRAFT — **the owner publishes this.** The agent has no publish path to the channel and does not acquire one in this change.
**Describes deployed code:** `ef7a031`, run [40245529519](https://github.com/leshchenko1979/ai-antispam/actions/runs/40245529519) — success; container restarted `2026-10-25T23:41:47Z`.

---

## RU (primary)

**Спам чужим ботом: реклама приходит не с аккаунта спамера**

**Что произошло.** В одной из групп три сообщения с рекламой пришли за **0,09 секунды** — от трёх разных аккаунтов, каждый из которых появился ровно один раз и больше не писал. Все три были ответом на одно и то же сообщение участника, который сам ничего рекламного не написал.

Наш бот их **не тронул**. Удалять пришлось руками.

**В чём схема.** Рекламу присылает не сам спамер, а бот, которого он вызвал. Аккаунт спамера при этом остаётся чистым: он пишет безобидную реплику, вызывает бота — и текст приходит уже от бота. Каждый раз это новый бот, которого нет ни в одном чёрном списке. Завтра будет следующий.

Чёрные списки ботов тут бесполезны: список всегда отстаёт на один аккаунт.

**Что мы сделали.** Бот смотрит, кто вызвал бота, и проверяет такое сообщение как обычное — вместе с текстом и ссылками.

И второе, что важнее: наказание получает **тот, кто вызвал бота**, а не одноразовый бот. Бот — расходник, его не жалко. Аккаунт, который раз за разом вызывает новых ботов, — не расходник.

Администраторов и доверенных участников это не касается: если бота позвал админ, сообщение не удаляется — ровно как и сейчас, когда админ пишет что-то сам.

**Как проверить свою группу.**

1. **Права.** Бот должен быть админом с правом удалять сообщения. Без этого он видит спам, но не может его убрать — и честно предупредит вас об этом в личке.
2. **Режим.** `/mode` в личке с ботом: уведомление на каждое удаление, автоудаление с отчётом или тихое автоудаление. Режим не меняется сам.
3. **Язык.** `/lang` — интерфейс и уведомления переключаются на английский.
4. **На что смотреть.** Один и тот же участник за короткое время вызывает несколько ботов, и те отвечают рекламой. Если такое увидели — напишите нам, это самый полезный сигнал, который у нас есть.

Бот: [@ai_spam_blocker_bot](https://t.me/ai_spam_blocker_bot) · Сайт: [ai-antispam.ru](https://ai-antispam.ru)

---

## Notes for the owner (not part of the post)

- **Publish order is load-bearing.** This post describes behaviour that is deployed, not behaviour observed in production. Legs verified: run `40245529519` green on `ef7a031`, container `StartedAt 2026-10-25T23:41:47Z` (after the commit's own instant `2026-10-25T23:26:57Z`), and the new predicate present in the running container.
- **`#122` is regression-proven on live traffic.** Since the swap: **12 bans and 12 message deletions**, every pair matched, from `23:51:20Z` through `09:26:58Z`. Ordinary spam still gets classified, banned and removed.
- **The warning this issue was opened for has fired 0 times since the swap** — `not found in info`: 0, `Message lookup cache miss`: 0, `Origin carries no group message`: 0. Two WARNING lines exist in that window and neither is a moderation warning: one is `Server started`, the other is a credits notice for a chat with no paying admins.
- **Not yet measured:** zero guest-bot payloads have arrived since the swap, so the end-to-end path has not been exercised by real traffic. `docs/spamTactics.md` tactic 12 states this in its own Mitigation clause and gets rewritten when one arrives. If you would rather publish only after a live catch, hold this draft until then — nothing in it becomes false by waiting.
- **Nothing identifying is in the post:** no chat id, no admin id, no victim handle, no message ids.
- **Deliberately not stated (owner instruction, 2026-10-26):** the post does not name the delivery mechanism and does not describe prior handling. It states the tactic and the current behaviour only. Do not reintroduce either when editing.
- No publish path was added or invoked: no send tool, no bot-token call, no broadcast script.
