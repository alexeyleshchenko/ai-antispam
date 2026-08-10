# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ai-antispam** — Telegram AI spam blocker bot using LLMs for classification.

- Bot: [@ai_spam_blocker_bot](https://t.me/ai_spam_blocker_bot)
- Channel: [@ai_antispam](https://t.me/ai_antispam)
- Site: [ai-antispam.ru](https://ai-antispam.ru)

## Commands

```bash
# Install dependencies
uv sync

# Run tests
pytest tests/ -v

# Lint & format
ruff check src/ tests/
ruff format src/ tests/

# Type check
uvx ty check src

# Run locally (from project dir)
python -m src.app.main
```

## Architecture

```
src/app/
├── main.py           # Entry point
├── bot_commands.py   # Telegram bot commands
├── handlers/         # Message/event handlers
├── spam/             # Spam detection logic
├── database/          # DB queries and models
├── background_jobs/  # Async tasks
├── common/           # Shared utilities
├── types.py          # Pydantic types
├── i18n.py           # Internationalization
└── locales/          # Translation files
```

## Database

**DBHub MCP** connected to PostgreSQL at `144.31.188.163:5432/ai_spam_bot`.

## MCP Servers

- **DBHub**: PostgreSQL database access
- **Context7**: API documentation
- **logfire**: Logs and metrics
- **MiniMax**: Coding plan MCP
- **telegram**: Telegram bot integration

## Memory Bank

At start of dialog, read relevant memory-bank files:
- `memory-bank/activeContext.md` — current system state
- `memory-bank/confirmedSpamExamples.md` — labeled spam examples
- `memory-bank/progress.md` — recent work log
- `memory-bank/techContext.md` — technical details
- `memory-bank/opsPlaybook.md` — deploy/broadcast/migration pitfalls for agents

## Docker

**Image:** `ghcr.io/leshchenko1979/ai-antispam` — **184MB** (reduced from ~800MB+)

- Base: `python:3.14-alpine` (Alpine Linux, ~5MB base vs ~25MB Debian)
- `pydantic-ai-slim[openai,logfire]` instead of full `pydantic-ai`
- Builder stage: no system deps (pre-built wheels only)
- Runner stage: `apk add --no-cache curl` for healthcheck only

## Key Patterns

- Spam classification uses LLM with confidence thresholds (default 90%)
- Admin can set auto-delete mode or notification-only mode
- Billing via Telegram Stars
- Personal spam examples per admin for fine-tuning

## Logging

**Logging conventions are codified — read `docs/LOGGING.md` before writing or
touching any log line.** Core rules: every side-effect logs its outcome (success
or failure) with chat/admin context; correlation ids in plain-text log lines;
`@logfire.instrument(extract_args=True, record_return=True)` on flow-boundary
functions; absence of a log line is never evidence of absence of execution.
