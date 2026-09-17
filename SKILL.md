# ai-antispam — skill

**Version:** 0.1.0  
**Owns:** operational governance and process laws for the ai-antispam factory.

---

## Authoritative Writers & State Surfaces

| Surface | Holds | Authoritative writer | Everyone else |
|---|---|---|---|
| `evidence/ledger.jsonl` | every state transition — intake, claim, dispatch, close, score, ruling, run | `tools/ledger.py append` | `tail`, `verify` — read-only |
| `evidence/rework.md` | every defect, with its root cause and what now prevents it | `HQ`, at the close that resolved it | read-only, gated by `tests/test_rework.py` |
| `evidence/scores/<date>.md` | one measurement run, one file per run | the measurement job | read-only |
| `evidence/*.md` | durable evidence reports | `HQ` | read-only |
| `ONTOLOGY.md` | the canonical vocabulary | `HQ` | read-only, gated by `tests/test_ontology.py` |
| `docs/processes.md` | the canonical process register | `HQ` | read-only |
| this law | the process rules | `HQ` | read-only |

---

## Core Laws

1. **The Single Writer Law:** State that lives only in chat is not state. Every durable state surface in the factory must have exactly one named authoritative writer role.
2. **The Monotonic Sequence Law:** Events in `evidence/ledger.jsonl` are strictly ordered and monotonic. Work units follow `intake` -> `claim` -> `close`.
3. **The Zero-Defect Law:** Every defect found during operations is recorded in `evidence/rework.md` with root cause and durable prevention mechanism.
