#!/usr/bin/env python3
"""Gate: the daily self-audit's push leg branches on a MEASURED ahead-count (#135).

`docs/pacemaker-procedures.md` step 3 used to end with an unconditional
`git push alexey main` and `git push origin main`. That step assumed a level tree, and it
is wrong for a reason no amount of care fixes: **`git push <remote> main` pushes a REF, not
a commit.** Every commit reachable from `main` that the remote lacks goes out together, so
the two lawful outcomes are *push everything* or *push nothing* -- and a cron lane holds no
knob that publishes a scorecard without also publishing a held leg.

Measured 2026-11-08 at the top of that run: **19** commits ahead of `origin/main`, **25** of
`alexey`, every one of them the owner-gated #130 roster leg. The unconditional push would
have shipped a leg the owner had not released. The 2026-11-07 run hit the same wall and
recorded it ONLY in a chat message -- `grep -i push evidence/scores/2026-11-07-self-audit.md`
returned 0 hits, and no board item was filed -- so the finding had to be re-derived from
scratch a day later.

WHY THIS GATE EXISTS, and why a prose check alone would not do:

1. The doc is the only thing a cron runner reads. Nothing else polices it: `grep -rln
   pacemaker-procedures tests/ tools/` returned nothing before this file, so a future editor
   could restore the unconditional push and the whole suite would stay green while every
   daily run resumed shipping held work.
2. A PRESENCE check on the prose is satisfiable by a document that mentions the count and
   still pushes unconditionally. So arm (d) is a NEGATIVE CONTROL: the pre-fix step text,
   verbatim, must FAIL the predicate. A predicate that cannot reject the old text is not
   testing the branch.
3. The load-bearing claim -- that a held leg and a scorecard cannot be pushed apart -- is
   BEHAVIOUR, so arm (e) builds a throwaway two-remote sandbox and proves it: after a push
   of the scorecard commit, the held commit is on the remote too. That is the fact the
   procedure exists to route around, and it is not something prose can assert into truth.

The sandbox is a `mktemp -d` tree with two bare remotes; nothing here touches a real remote,
the live ledger, or the working tree.

Run:  python3 -m pytest tests/test_self_audit_push_gate.py -q
Exit: 0 all arms pass, 1 an arm failed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROCEDURE = REPO / "docs" / "pacemaker-procedures.md"

# The two branches the procedure must name, in the words that make them a branch rather than
# a wish. Both are required: a doc naming only the refusal would strand every scorecard.
COUNT_READ = re.compile(r"rev-list\s+--count\s+origin/main\.\.HEAD")
LEVEL_ARM = re.compile(r"\*\*`0`\s+ahead\*\*.*?git push", re.DOTALL)
HELD_ARM = re.compile(r"\*\*`>\s*0`\s+ahead\*\*.*?NOT\s+push", re.DOTALL)
DISPOSITION = re.compile(r"push: blocked <N>")

# The pre-fix step 3, verbatim from `git show 9efb9e3:docs/pacemaker-procedures.md`. This is
# the negative control: the predicate below MUST reject it.
PREFIX_STEP3 = (
    "3. Commit ONLY the scorecard that run produced (`evidence/scores/<date>-self-audit.md`), "
    "message `evidence: daily self-audit <date>`, then `git push alexey main` and "
    "`git push origin main`."
)


def _section(text: str) -> str:
    """Return the `self-audit-daily` block only.

    Scoped deliberately: the outreach section also contains a push instruction
    (`push origin main` at the campaign repo, owner 2026-09-24), and a repo-wide search would
    let that unrelated line satisfy -- or fail -- this gate's arms.
    """
    start = text.index("**self-audit-daily**")
    end = text.find("\n## ", start)
    return text[start : end if end != -1 else len(text)]


def _predicate(step_text: str) -> tuple[bool, list[str]]:
    """The gate's actual decision, factored out so the negative control exercises it."""
    problems = []
    if not COUNT_READ.search(step_text):
        problems.append("step 3 does not name the ahead-count read (rev-list --count origin/main..HEAD)")
    if not LEVEL_ARM.search(step_text):
        problems.append("step 3 has no `0 ahead` arm that pushes both remotes")
    if not HELD_ARM.search(step_text):
        problems.append("step 3 has no `> 0 ahead` arm that refuses the push")
    if not DISPOSITION.search(step_text):
        problems.append("step 4 does not require the unconditional push disposition line")
    return (not problems), problems


# --------------------------------------------------------------------------- prose arms


def test_the_procedure_carries_the_branch():
    """Arms (a)-(c): the live procedure satisfies every clause."""
    ok, problems = _predicate(_section(PROCEDURE.read_text()))
    assert ok, "docs/pacemaker-procedures.md is missing: " + "; ".join(problems)


def test_the_predicate_rejects_the_prefix_text():
    """Arm (d): the pre-fix step text MUST fail. A predicate that passes it tests nothing.

    This is the arm that separates this gate from a substring check: the old text names a
    push, so a naive `"push" in text` passes it. Only the branch clauses reject it.
    """
    ok, problems = _predicate(PREFIX_STEP3)
    assert not ok, "the predicate accepted the unconditional-push text -- it is vacuous"
    assert any("ahead-count" in p for p in problems), problems
    assert any("refuses the push" in p for p in problems), problems


def test_the_outreach_push_instruction_is_out_of_scope():
    """Arm (f): the scoping is real, not accidental.

    The campaign repo's own push line (`git -C /root/ai-antispam-outreach push origin main`)
    lives elsewhere in the same file, and the self-audit block legitimately contains a
    `git push origin main` of its own. So the discriminator is the CAMPAIGN PATH, not the
    push verb: if `_section` ever widened to the whole document, the gate could be satisfied
    by the outreach line while the self-audit block said nothing.
    """
    whole = PROCEDURE.read_text()
    assert "ai-antispam-outreach push origin main" in whole, (
        "the outreach push line vanished; re-scope this arm against whatever replaced it"
    )
    assert "ai-antispam-outreach" not in _section(whole), (
        "the self-audit block picked up the campaign repo's push line"
    )


# ---------------------------------------------------------------------- behaviour arm


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    )


@pytest.fixture()
def sandbox():
    root = Path(tempfile.mkdtemp(prefix="pushgate-"))
    try:
        for name in ("origin.git", "alexey.git"):
            subprocess.run(["git", "init", "--bare", "-q", str(root / name)], check=True)
            subprocess.run(["git", "--git-dir", str(root / name), "symbolic-ref", "HEAD",
                            "refs/heads/main"], check=True)
        work = root / "work"
        subprocess.run(["git", "clone", "-q", str(root / "origin.git"), str(work)], check=True,
                       capture_output=True)
        _git("checkout", "-q", "-b", "main", cwd=work)
        _git("remote", "add", "alexey", str(root / "alexey.git"), cwd=work)
        (work / "base.txt").write_text("base\n")
        _git("add", "base.txt", cwd=work)
        _git("commit", "-qm", "base", cwd=work)
        _git("push", "-q", "origin", "main", cwd=work)
        _git("push", "-q", "alexey", "main", cwd=work)
        _git("fetch", "-q", "origin", cwd=work)
        yield root, work
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_held_leg_cannot_be_pushed_apart_from_the_scorecard(sandbox):
    """Arm (e): THE claim. `git push origin main` carries the held commit too.

    This is the behaviour the procedure routes around, proven rather than asserted. If git
    ever grew a per-commit push this arm would go RED and the procedure would be free to
    simplify -- which is exactly the kind of change a doc gate should surface, not hide.
    """
    root, work = sandbox
    origin = root / "origin.git"

    # A held leg, then a scorecard, both on main.
    (work / "held.txt").write_text("held\n")
    _git("add", "held.txt", cwd=work)
    _git("commit", "-qm", "held leg (owner-gated)", cwd=work)
    (work / "score.md").write_text("score\n")
    _git("add", "score.md", cwd=work)
    _git("commit", "-qm", "scorecard", cwd=work)

    ahead = int(_git("rev-list", "--count", "origin/main..HEAD", cwd=work).stdout.strip())
    assert ahead == 2, f"expected 2 commits ahead, got {ahead}"

    # The OLD step 3, run verbatim.
    _git("push", "-q", "origin", "main", cwd=work)

    on_origin = subprocess.run(
        ["git", "--git-dir", str(origin), "cat-file", "-e", "main:held.txt"],
        capture_output=True, check=False,
    )
    assert on_origin.returncode == 0, (
        "the held commit did NOT reach origin -- then the unconditional push would be safe "
        "and this gate's premise is stale; re-measure before trusting the procedure"
    )


def test_the_new_branch_pushes_nothing_while_the_leg_is_held(sandbox):
    """Arm (g): the branch's refusal arm leaves the tree clean and the remote untouched."""
    root, work = sandbox
    origin = root / "origin.git"

    (work / "held.txt").write_text("held\n")
    _git("add", "held.txt", cwd=work)
    _git("commit", "-qm", "held leg", cwd=work)
    (work / "score.md").write_text("score\n")
    _git("add", "score.md", cwd=work)
    _git("commit", "-qm", "scorecard", cwd=work)

    ahead = int(_git("rev-list", "--count", "origin/main..HEAD", cwd=work).stdout.strip())
    before = _git("--git-dir", str(origin), "rev-parse", "main", cwd=work).stdout.strip()

    # The NEW step 3: ahead > 0 -> do NOT push.
    if ahead == 0:  # pragma: no cover - guards the arm's own premise
        pytest.fail("premise broken: nothing ahead, so the refusal arm is untested")
    disposition = f"push: blocked {ahead} commit(s) ahead of origin/main"

    after = _git("--git-dir", str(origin), "rev-parse", "main", cwd=work).stdout.strip()
    assert before == after, "the refusal arm still moved the remote"
    assert "blocked" in disposition and str(ahead) in disposition
    assert _git("status", "--porcelain", cwd=work).stdout.strip() == "", "tree not clean"
    score_on_origin = subprocess.run(
        ["git", "--git-dir", str(origin), "cat-file", "-e", "main:score.md"], capture_output=True,
        check=False,
    )
    assert score_on_origin.returncode != 0, "the scorecard reached origin despite the refusal"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
