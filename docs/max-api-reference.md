# MAX Bot API — working reference

**Owns:** the MAX (max.ru) domain for the ai-antispam bot — the API surface as it
actually behaves, the constraints that bound the product, and the live
infrastructure that carries it.

This is the **distilled working reference**. The chronological evidence record —
spikes, re-tests, retractions, dated receipts — stays in
`docs/memos/2026-07-26-max-antispam-memo.md`. Where the two disagree, the memo's
dated evidence wins and this file gets fixed.

**Owner lane:** the **Max lane** (ai-antispam factory, Max topic). Handed over by
the Bot lane 2026-09-12, on the owner's instruction. MAX moderation is that lane's
workstream; the Bot lane keeps the Telegram side.

Every claim below was read first-hand from the live API or the live hosts on
2026-09-12 (UTC). Receipts are named inline.

---

## 1. Access

| Thing | Value |
|---|---|
| Base URL | `https://platform-api2.max.ru` |
| Auth | `Authorization: <token>` header — the raw token, **no `Bearer` prefix** |
| Bot identity | `user_id 385916094`, `is_bot: true`, username `id773671678516_1_bot`, name «Антиспам» |
| Bot token (agents box) | `/root/ai-antispam/.secrets/max-bot-token` — mode 600, gitignored, **never echo it** |
| Webhook secret | `MAX_WEBHOOK_SECRET` in `apps:/data/projects/ai-antispam/.env` (43 chars); present in the container env (44 bytes incl. newline). The repo `.env` carries **no** MAX keys. |
| CA | `platform-api2.max.ru` fails standard verification **from the ops box** (`curl: (60)`) — box-side calls need `-k`. The Минцифры root CA **is** in the container trust store (#29), so in-container calls verify normally (`tls=0`). |

Docs: `https://dev.max.ru/docs-api` — per-method pages under `/docs-api/methods/…`.

---

## 2. Endpoint matrix — what works

| Call | Shape | Result |
|---|---|---|
| `GET /me` | — | 200 — bot identity |
| `GET /chats` | — | 200 — channels the bot is in |
| `GET /messages?chat_id=<id>` | — | 200 — **channel posts** |
| `GET /messages/{post_mid}/comments` | — | 200 — `{"messages":[…]}` |
| `GET /messages/{post_mid}/comments/{comment_mid}` | — | 200 — single comment |
| `POST /messages/{post_mid}/comments` | JSON body | 200 — create |
| `PUT /messages/{post_mid}/comments` | JSON body | 200 — edit |
| `DELETE /messages/{post_mid}/comments` | **`?comment_id=<comment_mid>` query param** | 200 `{"success":true}` |
| `GET /subscriptions` | — | 200 |
| `POST /subscriptions` | JSON body `{url, update_types[], secret?}` | 200 `{"success":true}` |
| `DELETE /subscriptions` | **`?url=<url>` query param** | 200 `{"success":true}` |
| Stories | — | **404 — no Bot API surface** |
| Native payments | — | **404 — no Bot API surface** |

**`GET /messages/{comment_mid}` (flat) → 404 `not.found`.** A comment is readable
only through its post — use the nested path.

---

## 3. The attribution limit — the product's binding constraint

**MAX does not tell a bot who wrote a comment.**

- A **third-party** comment returns `['body', 'recipient', 'timestamp']` — **no `sender` at all**.
- A **channel post** read by the bot likewise carries `sender: None`.
- `sender` appears **only on the bot's own comments** — MAX echoes the bot back to itself (`user_id 385916094`, «Антиспам»).

Consequence: **moderation is delete-only.** No ban, no repeat-offender tracking,
no "who posted this" — there is nothing to key on. Attribution was twice mistaken
for working (both retracted 2026-09-12); the retraction is the settled reading.

The comment id lives at **`body.mid`**. Top-level `mid` is `None` on list items —
reading the wrong one yields `None` and then `400 Invalid message_id: None`.

---

## 4. Delete semantics

| Case | Result |
|---|---|
| Delete a fresh comment — bot's own **or a third party's** | `200 {"success":true}` |
| Re-delete an id already deleted | `403 access.denied` |
| Delete a well-formed but non-existent id | **identical** `403 access.denied` |
| Delete a malformed id (`None`) | `400 Invalid message_id: None` |

So **403 means "no such comment to delete"** — not forbidden-by-policy, and not a
closed window. An earlier claim that self-delete 403s after ~2.5–5 min was
**falsified**: those 403s were re-deletes of ids already removed. **There is no
moderation window.**

**Cross-user delete is proven** (#33, 2026-09-12): a third-party comment deleted
`200`, the re-read showed it gone, two out-of-scope comments untouched. The MVP's
core capability exists.

`comment_id` goes in the **query string**, not the body — the same asymmetry as
`DELETE /subscriptions` (`url` as a query param) versus `POST` (JSON body).

---

## 5. Webhook push — proven end to end

**Registration**

```
POST https://platform-api2.max.ru/subscriptions
Authorization: <token>

{"url": "https://ai-antispam.l1979.ru/process-max-updates",
 "update_types": ["comment_created", "comment_edited", "comment_removed"],
 "secret": "<MAX_WEBHOOK_SECRET>"}
```

- `secret` is delivered on every push as the **`X-Max-Bot-Api-Secret`** header.
- **`GET /subscriptions` never echoes `secret`.** Whether one is set is
  **unverifiable from the API by construction** — a `200` on a real push is the
  only evidence it was accepted.

**Delivery — the decisive proof (2026-09-12 14:56:52Z)**

| Leg | Receipt |
|---|---|
| Push origin | POST from `89.221.230.112` — VK Services, AS47764, Moscow |
| Response | `200` — the secret was accepted |
| Container log | `update_type=comment_created mid.…1f0b404461 sender_id=None text_len=3` |
| Latency | same second — 450 ms |
| Actor | a **non-bot** comment, posted by the owner |

**Two rules that make or break a delivery test:**

1. **MAX suppresses the bot's own events.** A comment or post authored by the bot
   produces **no** delivery — a self-authored event can never prove the chain.
2. **A comment-only subscription fires nothing for a channel post.** Posts need
   `message_created` in `update_types`. Widen only as a temporary diagnostic, and
   put it back afterwards.

**A route probe is not a delivery proof** — see the router, `§Fact verification`.

---

## 6. Live infrastructure

| Piece | Where |
|---|---|
| Route | `POST https://ai-antispam.l1979.ru/process-max-updates` |
| Handler | `src/app/max_webhook.py`, route in `src/app/main.py` |
| Behaviour | authenticates → validates → logs (body-free) → acks. **Moderates nothing.** |
| Fail-closed | secret unset → `503`; wrong/absent → `403`; bad body → `400`; valid → `200` |
| Traefik | `apps:/data/projects/traefik/config/ai-antispam.yml` — the host rule **AND-s the path**; a path not listed 404s at Traefik before reaching the bot |
| Subscription | exactly one — the stable URL, `update_types` = the comment trio |
| Container | `ghcr.io/leshchenko1979/ai-antispam:main` on `apps` (image source moved to the canonical account 2026-09-25) |
| Deploy | `src/**` triggers the deploy workflow; `scripts/**` does **not** |

**Test surface:** MAX channel **«Тест антиспам»** — `chat_id -77345848199175`,
private, owner `190126855` — three posts: «Текст комментов 3»,
«Тест комментов 2», «Всем привет! Это тест комментов».

---

## 7. Traps that cost real time

- **The list key is `messages`, not `comments`.** Parsing `comments` makes every
  post look empty — this nearly sent a diagnosis after a phantom.
- **`body.mid`, not top-level `mid`.**
- **`DELETE` takes query params; `POST` takes a JSON body.**
- **`403 access.denied` = absent** — not policy, not a time window.
- **`GET /subscriptions` cannot confirm `secret`.**
- **Self-events are suppressed** — the bot cannot test its own push path.
- **A channel post is not a comment.** Different `update_types`.
- **Box-side `curl` needs `-k`**; the container does not.

---

## 8. Open / unproven

- **No MAX moderation is wired.** The handler logs; nothing deletes. That is the
  port's workstream — deliberately not smuggled in under the ingress issue.
- **Attribution is impossible** (§3) — permanent, not a gap to close.
- **Stories and native payments have no Bot API surface** (404).
- **Whether the bot can originate a channel post is unresolved** — one probe
  returned `Unknown recipient`, a later shape succeeded. Not re-verified; settle
  it before relying on it.
- **`apps:/data/projects/ai-antispam/.env` is mode 644, owner `501:staff`** —
  world-readable while holding 20 secrets. Flagged to the owner 2026-09-12;
  untouched, his call.

---

## 9. Verification recipes

```bash
# Box-side reads need -k (no Минцифры CA on this host). Never echo the token.
cd /root/ai-antispam
TOK=$(cat .secrets/max-bot-token)
curl -sS -k -H "Authorization: $TOK" https://platform-api2.max.ru/me
curl -sS -k -H "Authorization: $TOK" https://platform-api2.max.ru/subscriptions
curl -sS -k -H "Authorization: $TOK" https://platform-api2.max.ru/chats
curl -sS -k -H "Authorization: $TOK" "https://platform-api2.max.ru/messages?chat_id=<id>"
curl -sS -k -H "Authorization: $TOK" "https://platform-api2.max.ru/messages/<post_mid>/comments"

# Route, from outside the host — the fail-closed matrix
V=$(ssh apps 'grep "^MAX_WEBHOOK_SECRET=" /data/projects/ai-antispam/.env | cut -d= -f2-')
curl -sS -o /dev/null -w '%{http_code}\n' -X POST -H "X-Max-Bot-Api-Secret: $V" \
  -H 'Content-Type: application/json' -d '{"update_type":"probe"}' \
  https://ai-antispam.l1979.ru/process-max-updates          # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST -H 'X-Max-Bot-Api-Secret: nope' \
  https://ai-antispam.l1979.ru/process-max-updates          # 403
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  https://ai-antispam.l1979.ru/process-max-updates          # 403

# The ONLY delivery proof: a NON-BOT comment, then read BOTH logs
ssh apps 'docker logs --since 10m ai-antispam 2>&1 | grep -i max'
ssh apps 'docker logs --since 10m traefik 2>&1 | grep process-max-updates'
```

**Secret hygiene:** read the secret into a shell variable and never print it. The
container's log line is body-free by design — `update_type`, `mid`, `sender_id`,
`text_len`: no text, no secret.

---

## 10. Evidence index

| What | Where |
|---|---|
| Chronological investigation, spikes, retractions | `docs/memos/2026-07-26-max-antispam-memo.md` |
| Route + module + tests | commit `e39ca89` (#30) |
| Stable ingress receipt | #30 — `#issuecomment-5645793884` |
| Cross-user delete proven | #33 — `#issuecomment-5645799129` |
| Delivery proven with a non-bot actor | #31 — `#issuecomment-5646710233` |
| Минцифры CA in the container trust store | #29 — `#issuecomment-5645227900` |
| MAX API docs | `https://dev.max.ru/docs-api` |
