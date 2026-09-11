# Admin broadcast draft — silent auto-delete mode

**Run after** channel post is published on @ai_antispam. Channel post: https://t.me/ai_antispam/43

**Format:** HTML (`--parse-mode HTML`)
**Audience:** active admins (~262), via `scripts/run_broadcast_on_vds.sh --new-campaign`

---

## Message (RU)

```html
<b>Новый режим: тихое автоудаление спама</b>

Команда <code>/mode</code> теперь переключает <b>три</b> режима:
1. 🔔 Только уведомления — бот ждёт вашей кнопки «Удалить»
2. 🗑 Автоудаление — удаляет спам сам и присылает отчёт с кнопкой «Не спам»
3. 🔇 <b>Тихое автоудаление</b> — удаляет спам без личного сообщения на каждое удаление

<b>Ваш текущий режим не меняется</b>, пока вы сами не нажмёте <code>/mode</code>.

<b>Как включить тихий режим:</b> откройте бота в личке → <code>/mode</code> → нажимайте, пока не увидите подтверждение тихого автоудаления.

Если бот не уверен, что сообщение — спам, он не удаляет его сам: пришлёт уведомление с кнопками «Удалить» и «Не спам». Так во всех режимах.

Подробнее: <a href="https://t.me/ai_antispam/43">пост в канале @ai_antispam</a>
```

---

## Ops checklist

1. ~~Publish channel post~~ — https://t.me/ai_antispam/43
2. ~~Copy HTML body~~ → `scripts/broadcast_message.txt`
3. ~~Export admin IDs~~ → `scripts/admin_ids.txt` (262 active)
4. ~~Dry-run~~ — 262 recipients
5. ~~Live broadcast (2026-05-16)~~ — **252 sent**, 10 unreachable (deactivated Telegram accounts)
