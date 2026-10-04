#!/usr/bin/env python3
# =============================================================================
# board_migrate — move the ai-antispam service board to leshchenko1979.
#
# Owner order 2026-10-04 (Telegram, ai-antispam HQ thread) + Open Questions q16
# answer B: recreate the 62 issues of alexeyleshchenko/ai-antispam on
# leshchenko1979/ai-antispam, RENUMBERED, preserving body text, comments and
# state. The source account is being retired, so the board is recreated on the
# canonical account before it disappears.
#
# WHY RECREATE, NOT TRANSFER:
#   * `gh` (authed as leshchenko1979) holds PULL-ONLY on the source repo, so
#     GraphQL TransferIssue is refused even for our own issues;
#   * a repo transfer is impossible because the target repo name
#     `leshchenko1979/ai-antispam` is already taken by the canonical
#     deploy/image/landing repo (and a transfer would not merge two issue spaces).
#
# RESCUE: docs/board-migration/board-export.json is the full pre-migration
# snapshot (62 issues, 148 comments) taken while read access still existed.
#
# Verbs:
#   plan    dry-run: print the ordered plan + the predicted number map, no writes
#   run     execute the migration (resumable; --limit N for a canary)
#   verify  read the target board back and check every migrated item
#
# State: docs/board-migration/number-map.json, written after EVERY issue so an
# interrupted run resumes instead of duplicating. Never hand-edit it.
# =============================================================================

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(ROOT, "docs/board-migration/board-export.json")
STATE = os.path.join(ROOT, "docs/board-migration/number-map.json")
SRC = "alexeyleshchenko/ai-antispam"
DST = "leshchenko1979/ai-antispam"
MIGRATION_DATE = "2026-10-04"
PACE = 1.0  # seconds between content-creating calls (GitHub: 80/min secondary limit)


def gh(args, check=True):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(
            "gh %s failed rc=%d\n%s" % (" ".join(args[:4]), r.returncode, r.stderr.strip())
        )
    return r.stdout.strip()


def load_export():
    with open(EXPORT) as f:
        d = json.load(f)
    d.sort(key=lambda x: (x["createdAt"], x["number"]))
    return d


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"source": SRC, "target": DST, "migrated_at": None, "map": {}}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def next_free_number():
    nums = []
    for cmd in (["issue", "list"], ["pr", "list"]):
        out = gh(cmd + ["--repo", DST, "--state", "all", "--limit", "500", "--json", "number"])
        nums += [x["number"] for x in json.loads(out)]
    return max(nums) + 1


def provenance_header(it):
    author = it["author"]["login"]
    closed = " · closed %s" % it["closedAt"] if it.get("closedAt") else ""
    return (
        "> **Migrated %s from `%s#%d`** — original: %s\n"
        "> Opened %s by @%s%s.\n"
        "> The ai-antispam service board moved to `%s` on %s; numbers were "
        "reassigned. Old→new map: `docs/board-migration/number-map.json`.\n\n---\n\n"
        % (MIGRATION_DATE, SRC, it["number"], it["url"], it["createdAt"], author,
           closed, DST, MIGRATION_DATE)
    )


def write_temp(text):
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return path


def create_issue(it):
    body = provenance_header(it) + (it.get("body") or "")
    p = write_temp(body)
    try:
        url = gh(["issue", "create", "--repo", DST, "--title", it["title"], "--body-file", p])
    finally:
        os.unlink(p)
    return int(url.rstrip("/").split("/")[-1]), url


def post_comment(issue_num, old_num, c):
    author = c["author"]["login"] if c.get("author") else "ghost"
    text = ("> *migrated from `%s#%d` — originally by @%s on %s*\n\n%s"
            % (SRC, old_num, author, c["createdAt"], c.get("body") or ""))
    p = write_temp(text)
    try:
        gh(["issue", "comment", str(issue_num), "--repo", DST, "--body-file", p])
    finally:
        os.unlink(p)


def close_issue(num, reason):
    gh(["issue", "close", str(num), "--repo", DST, "--reason",
        "completed" if reason == "COMPLETED" else "not planned"])


def cmd_plan(_a):
    d = load_export()
    s = load_state()
    base = next_free_number()
    done = len(s["map"])
    print("source %s: %d issues" % (SRC, len(d)))
    print("target %s: next free number = %d" % (DST, base))
    print("already migrated: %d" % done)
    print()
    print("%4s  %4s  %-8s  %5s  %s" % ("old", "new", "state", "cmts", "title"))
    for i, it in enumerate(d):
        key = str(it["number"])
        new = s["map"].get(key, {}).get("new") or (base + i)
        print("%4d  %4d  %-8s  %5d  %s"
              % (it["number"], new, it["state"], len(it.get("comments") or []), it["title"][:70]))


def cmd_run(args):
    d = load_export()
    s = load_state()
    if s.get("migrated_at") is None:
        s["migrated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    def incomplete(it):
        e = s["map"].get(str(it["number"]))
        if e is None or not e.get("new"):
            return True
        if e.get("posted_comments", 0) < len(it.get("comments") or []):
            return True
        return it["state"] == "CLOSED" and not e.get("closed")

    todo = [it for it in d if incomplete(it)]
    if args.limit:
        todo = todo[:args.limit]
    print("migrating %d issue(s) -> %s" % (len(todo), DST))
    for it in todo:
        key = str(it["number"])
        entry = s["map"].get(key)
        if entry and entry.get("new"):
            num, url = entry["new"], entry["url"]
            print("resuming #%d -> #%d (comments %d/%d%s)"
                  % (it["number"], num, entry.get("posted_comments", 0),
                     len(it.get("comments") or []),
                     ", close owed" if it["state"] == "CLOSED" and not entry.get("closed") else ""))
        else:
            num, url = create_issue(it)
            entry = {"new": num, "url": url, "comments": len(it.get("comments") or []),
                     "state": it["state"], "posted_comments": 0}
            s["map"][key] = entry
            save_state(s)
            time.sleep(PACE)
        comments = it.get("comments") or []
        while entry["posted_comments"] < len(comments):
            post_comment(num, it["number"], comments[entry["posted_comments"]])
            entry["posted_comments"] += 1
            save_state(s)
            time.sleep(PACE)
        if it["state"] == "CLOSED" and not entry.get("closed"):
            close_issue(num, it.get("stateReason"))
            entry["closed"] = True
            save_state(s)
            time.sleep(PACE)
        print("%4d -> %4d  (%d comments, %s)" % (it["number"], num, entry["posted_comments"], it["state"]))
    print("run complete: %d/%d in map" % (len(s["map"]), len(d)))


def cmd_verify(_a):
    s = load_state()
    out = gh(["issue", "list", "--repo", DST, "--state", "all", "--limit", "500",
              "--json", "number,state,comments,title"])
    target = {x["number"]: x for x in json.loads(out)}
    bad = 0
    for old, e in sorted(s["map"].items(), key=lambda kv: int(kv[0])):
        num = e["new"]
        t = target.get(num)
        if not t:
            print("MISSING  #%s -> #%d" % (old, num)); bad += 1; continue
        got_c = len(t.get("comments") or [])
        want_state = "CLOSED" if e["state"] == "CLOSED" else "OPEN"
        if got_c != e["comments"]:
            print("COMMENTS #%s -> #%d: want %d got %d" % (old, num, e["comments"], got_c)); bad += 1
        if t["state"] != want_state:
            print("STATE    #%s -> #%d: want %s got %s" % (old, num, want_state, t["state"])); bad += 1
    print("verify: %d mapped, %d discrepancies" % (len(s["map"]), bad))
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run"); r.add_argument("--limit", type=int, default=0)
    sub.add_parser("verify")
    args = ap.parse_args()
    {"plan": cmd_plan, "run": cmd_run, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
