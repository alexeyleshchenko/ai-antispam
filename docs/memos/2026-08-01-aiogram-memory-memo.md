# aiogram Memory Footprint & the 512 MiB Limit — Memo

## Executive summary

**Decision: keep `mem_limit: 512mb`. Do NOT trim aiogram's unused methods/types.**

The bot idles at **~293 MiB**, dominated by aiogram itself: `import aiogram` 3.30 compiles pydantic models for the entire Telegram Bot API and costs **~234 MiB** on its own. The old `256mb` limit sat *below* this floor, so the container OOM-crash-looped (`RestartCount=9`, kernel `Memory cgroup out of memory` in dmesg) — which was the real cause of the "duplicate tap" webhook retries (each restart opened a downtime window; Telegram/the client retried, producing distinct `update_id`s). Raising the limit to 512 MiB stopped the restarts and the duplicates with it.

Trimming the unused aiogram models was investigated and rejected: the only safely-removable slice is the **methods (~25 MiB)** — below the threshold where the surgery is worth it — and the **types are not safely removable at all** (aiogram's `types/__init__.py` references every type by name in a model-rebuild loop, so deleting any one breaks import). The ~25 MiB a trim could buy is headroom nobody needs on a host that isn't memory-constrained. Cost and fragility vastly exceed the benefit.

## Background — why this came up

- The container was OOM crash-looping. dmesg: `Memory cgroup out of memory: Killed process (python)`, `oom_memcg=/system.slice/docker-<id>.scope`. `RestartCount` climbed to 9.
- Idle RSS was ~293 MiB against a 256 MiB limit (98.9%) — any traffic spike tipped it over.
- The crash-loop produced the "duplicate tap" symptom investigated under issues #32/#33: restarts → webhook retry → distinct `update_id`s (726121960/61/62 in Logfire). Fixing the memory fixed the duplicates; the dedup guard was dropped and #33 closed.

## Measurement (in-container, aiogram 3.30 / Python 3.14)

`aiogram/__init__.py` force-loads both `methods` and `types` on any import, so the split was measured by copying the package to a temp dir, rewriting its `__init__.py` to load `types`-only vs `methods`-only, and measuring each in a fresh subprocess:

| Component | RSS | Notes |
|---|---|---|
| python baseline | 8.6 MiB | empty interpreter |
| **aiogram types** | **197.6 MiB** | 391 model classes; 186 reachable from `Update` |
| **aiogram methods** | **26.5 MiB** | 374 model classes; the only easily-trimmable part |
| aiogram core (Bot/Dispatcher/filters) | 10.3 MiB | irreducible |
| **aiogram total** | **234.4 MiB** | |
| + pydantic-ai, asyncpg, aiohttp, OTel/Logfire, app modules | ~59 MiB | |
| **Idle footprint** | **~293 MiB** | plateaued, not leaking (flat over 5+ min) |

## Why trimming was rejected

**1. The methods are the only safely-trimmable part, and they're small.** The app calls ~17 of 374 methods (`send_message`, `edit_message_text`, `delete_message`, `ban/unban_chat_member`, `ban/unban_chat_sender_chat`, `get_chat`, `get_chat_administrators`, `get_chat_member`, `leave_chat`, `set_webhook`, `set_my_commands`, `get_me`, `send_invoice`, `answer_pre_checkout_query`, `get_webhook_info`). Deleting the other ~357 would save ~25 MiB — but requires surgery on `bot.py`'s 6,661-line generated method wiring plus `methods/__init__.py` (186 explicit imports).

**2. The types are not safely removable.** A graph walk (BFS over pydantic field annotations from `Update`) found 205 of 391 types unreachable from `Update`. But building a trimmed `types` package and importing it **failed**: `types/__init__.py:676` runs `_entity = globals()[_entity_name]` — an entity-registry / model-rebuild loop that references *every* type by name. Remove any type import and it dies with `KeyError`. So trimming types means patching aiogram's internal metaprogramming — brittle to the point of absurdity across upgrades. (Several unreachable types are also app-used — `BotCommand`, `ChatMember`, `ErrorEvent`, `WebhookInfo` — shrinking the set further.)

**3. The breakage risk is high and the gain is unneeded.** Any trim is pinned to one aiogram version and breaks on every bump (needs a guard test + re-pin). On a production spam bot a broken import = bot down. Meanwhile the OOM is already solved by 512 MiB (RestartCount 0, plateaued at 57% of limit), and the host runs ~18 containers and is not memory-constrained.

**Decision rule applied:** trimmable-and-safe saving (~25 MiB) < ~50 MiB threshold **and** high breakage risk → **accept 512 MiB**.

## Actions taken

- `mem_limit` raised **256mb → 512mb** on the deployed container (`/data/projects/ai-antispam/docker-compose.yml` on apps); container recreated, healthy, RestartCount 0, ~293 MiB / 57%.
- This repo's `docker-compose.yml` updated to match (`mem_limit: 512mb`) so a redeploy from source doesn't regress to the OOM-prone 256mb.
- Dedup issues closed: #33 (dedup guard dropped — duplicates were restart-caused), #32 (superseded).

## Known minor issue (not addressed)

- Healthcheck `start_period: 60s` is tight: startup can take ~83s (slow Logfire handshake to logfire-us.pydantic.dev), so the container briefly flaps `unhealthy` before recovering. Bumping `start_period` to ~120s would remove the flap. Left as-is; cosmetic.

## If memory ever becomes a hard constraint

The only lever with dramatic payoff is **replacing aiogram with a thin aiohttp Bot-API client** (the app already uses aiohttp): ~234 MiB → ~15 MiB. But that rewrites the Dispatcher/filters/webhook layer on a stable production bot — only worth it if memory becomes a genuine constraint, which 512 MiB ensures it isn't.

## Investigation log

1. Container inspection: single process (`python -m app.main`, PID 1), VmRSS 293 MiB, RssAnon 280 MiB (heap-dominated, not workers/file-backed).
2. Import-cost probe: `import aiogram` = +234.5 MiB in a fresh process; aiogram.types has 866 public names, aiogram.methods 374.
3. RSS split: temp-copy + rewritten `__init__.py`, measured types-only (206 MiB) vs methods-pull (233 MiB) vs full (243 MiB) → types 197.6 / methods 26.5 / core 10.3.
4. Type graph: BFS from `Update` over pydantic field annotations → 186 reachable, 205 unreachable.
5. Trim test: dropped 202 unreachable imports from a temp `types/__init__.py` → `import aiogram` failed with `KeyError: 'AcceptedGiftTypes'` at line 676 (`globals()[_entity_name]`), proving the registry coupling.
