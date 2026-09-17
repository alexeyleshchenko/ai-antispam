# ai-antispam — ontology

**Version:** 0.1.0
**Owns:** the canonical vocabulary of this factory. One term, one meaning.

Every term below is the **only** word allowed for that concept — in issues, in
reports, in chat, in this repo's own prose. Anything in the *Not* column is a
defect to fix, not a synonym to tolerate.

---

## Canonical terms

| Term | Definition | Contextual synonyms (avoid when meaning this term) |
|---|---|---|
| `factory` | An autonomously self-improving delivery system consisting of versioned process laws, a chat surface whose place names carry state, and a repo whose issues are the task list | bot, agent, team, department, project |
| `owner` | The party accountable for a thing. When unqualified, refers to the human who approves and directs the factory. Not a lane, not a role | boss, admin, user, client, customer |
| `HQ` | The lane that rules, holds the owner relationship, and owns law authorship | supervisor, lead, manager, boss, admin |
| `lane` | A persistent session bound to one named place | worker session, thread, channel, chat session, agent |
| `work unit` | One issue, from opened to closed | task, ticket, job, item |
| `surface` | The chat product a factory runs on | chat, channel, platform, messenger |
| `law` | A factory's versioned, prescriptive process rules that bind agent execution | policy, guidelines, SOP, playbook, rules doc |
| `process` | A recurring act that keeps a property true. It has an owner, a declared cadence, product(s) it delivers to its client(s), and a run that leaves a trace | workflow, routine, procedure, pipeline |
| `process owner` | The role accountable for the design, health, and SLA of a process. Delegates execution to implementers | owner, lead, pipeline owner |
| `process client` | The party who triggers a process, sets its acceptance criteria, and consumes its output | client, requester, customer, upstream |
| `process implementer` | The actor (lane, tool, subagent, automated script) executing a run of a process and spending its resources | worker, executor, runner, actor |
| `ledger` | The append-only, monotonic event journal recording factory intake, claims, runs, and closes | log, journal, database, tracker |
| `rework` | Remediation of an accepted or shipped work unit triggered by a defect finding | fix, patch, bugfix, revision |

---

## Banned synonyms

| Don't say | Say | Why it matters |
|---|---|---|
| `supervisor` | `HQ` | The role was renamed to HQ. A report naming a supervisor describes a role that no longer exists |
| `meta-layer` | `meta-factory` | Two names for one thing breaks cross-repo searches and references |

---

## Enforcement

| Where | How |
|---|---|
| This repo's prose | `python3 tests/test_ontology.py` fails on any unexempted banned synonym from `## Banned synonyms` |
| Contextual synonyms column | Reference only: guidance for disambiguation |

The test parses the `## Banned synonyms` table above, so **the ban list is the
source of truth** and the gate cannot drift from it.
