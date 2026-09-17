#!/usr/bin/env bash
# =============================================================================
# oc-tools test harness — the 5 opencrabs-dev process tools.
#
#   Usage:  bash tools/tests/run.sh            (from repo root, or anywhere)
#           OC_TOOLS_DIR=/path/to/tools bash tools/tests/run.sh
#
#   One command runs the whole suite:
#     • presence check  — all 5 oc-* tools must exist (missing => FAIL)
#     • selftest        — each tool's own --selftest (absolute path so the
#                         tool's internal `$0` recursion resolves)
#     • edge cases      — the dedicated regressions found during build, most
#                         critically the oc-seal-state IFS-pipe multi-filter
#                         merge: every update flag must be folded into the
#                         baseline JSON. If that merge regresses the suite
#                         returns NONZERO.
#
#   Exit: 0 if everything passes, 1 if any check fails. Meant for CI / a
#   one-liner human run. Every failing check is named so it is actionable.
# =============================================================================
set -u

# ---- locate the tools -------------------------------------------------------
# Default target is the opencrabs-dev skill tools dir these were built into.
TOOLS_DIR="${OC_TOOLS_DIR:-/root/.opencrabs/profiles/ops/skills/opencrabs-dev/tools}"
TOOLS=(oc-order-validate oc-job-verify oc-artifact-verify oc-post-receipts oc-seal-state)

PASS=0
FAIL=0

note() { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '  ok   %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL %s\n' "$*"; }

# t_run name expected_rc -- cmd...
# Runs cmd, compares its exit code to expected_rc, marks pass/fail.
t_run() {
  local name="$1" want="$2"; shift 2
  local rc
  "$@" >/dev/null 2>&1; rc=$?
  if [ "$rc" = "$want" ]; then ok "$name (rc=$rc)"; else bad "$name (want rc $want, got $rc)"; fi
}

# t_run_out name expected_rc needle -- cmd...
# Runs cmd, asserts exit code AND that stdout contains `needle`.
t_run_out() {
  local name="$1" want="$2" needle="$3"; shift 3
  local out rc
  out="$("$@" 2>/dev/null)"; rc=$?
  if [ "$rc" = "$want" ] && printf '%s' "$out" | grep -qF "$needle"; then
    ok "$name (rc=$rc, found '$needle')"
  elif [ "$rc" != "$want" ]; then
    bad "$name (want rc $want, got $rc)"
  else
    bad "$name (rc ok but output lacks '$needle')"
  fi
}

echo "== oc-tools test harness =="
echo "tools dir: $TOOLS_DIR"

# ---- 0. presence ------------------------------------------------------------
echo "-- presence (all 5 tools must be present) --"
MISSING=0
for t in "${TOOLS[@]}"; do
  if [ -x "$TOOLS_DIR/$t" ]; then ok "$t present"
  else bad "$t MISSING at $TOOLS_DIR/$t"; MISSING=1; fi
done
if [ "$MISSING" = 1 ]; then
  echo
  echo "ABORT: not all 5 oc-* tools present — refusing to run." >&2
  echo "SUITE RESULT: FAIL ($PASS ok, $FAIL fail)" >&2
  exit 1
fi

# helper: absolute path of a tool (absolute $0 makes every tool's internal
# recursion work even under a relative working directory)
exe() { printf '%s/%s' "$TOOLS_DIR" "$1"; }

# ---- 1. selftests -----------------------------------------------------------
echo "-- selftests (each tool's own --selftest) --"
for t in oc-order-validate oc-job-verify oc-artifact-verify oc-seal-state; do
  if bash "$(exe "$t")" --selftest >/dev/null 2>&1; then
    ok "$t --selftest"
  else
    bad "$t --selftest"
  fi
done

# oc-post-receipts has no --selftest; its dry-run modes are the offline cover.
echo "-- oc-post-receipts dry-run edge cases (offline, no real send) --"
P="$(exe oc-post-receipts)"
t_run_out "post-receipts dry-run topic" 0 "DRY:" bash "$P" --dry-run --topic 30129 --text "test receipt"
t_run    "post-receipts dry-run receipt-artifact" 0 bash "$P" --dry-run --receipt-artifact --unit testunit
t_run    "post-receipts no-args -> usage/exit3" 3 bash "$P"
t_run    "post-receipts --topic missing --text -> usage" 3 bash "$P" --topic 30129

# ---- 2. oc-order-validate edge cases (offline) ------------------------------
echo "-- oc-order-validate edge cases (offline) --"
O="$(exe oc-order-validate)"
t_run "order-validate ledgermiss -> exit1" 1 bash "$O" 0000000000000000000000000000000000000000 --ledger /nonexistent-ledger.json
t_run "order-validate --help -> exit1" 1 bash "$O" --help

# ---- 3. oc-job-verify edge cases (offline) ----------------------------------
echo "-- oc-job-verify edge cases (offline) --"
J="$(exe oc-job-verify)"
t_run "job-verify non-numeric run-id -> exit1" 1 bash "$J" abc 0000000000000000000000000000000000000000
t_run "job-verify short ref -> exit1" 1 bash "$J" 123 shortsha

# ---- 4. oc-artifact-verify edge cases (offline) -----------------------------
echo "-- oc-artifact-verify edge cases (offline) --"
A="$(exe oc-artifact-verify)"
AD="$(mktemp -d)"
printf '\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x3e\x00\x01\x00\x00\x00' > "$AD/bin"
printf 'BUILD_MARKER_XYZ_987\nplain text\n' >> "$AD/bin"
t_run "artifact-verify missing artifact -> exit2" 2 bash "$A" "$AD/nope"
t_run "artifact-verify marker-missing -> exit3" 3 bash "$A" "$AD/bin" --markers NO_SUCH_MARKER
t_run "artifact-verify marker-present -> exit0" 0 bash "$A" "$AD/bin" --markers BUILD_MARKER_XYZ_987
rm -rf "$AD"

# ---- 5. oc-seal-state: the IFS-pipe multi-filter regression -----------------
echo "-- oc-seal-state multi-filter IFS-pipe merge regression --"
S="$(exe oc-seal-state)"
SD="$(mktemp -d)"
printf '{"sha":"aaaa","name":"keep-me","already":true}' > "$SD/b.json"
printf '{"orders":[{"sha":"seed0000000000000000000000000000000000000000","features":"old","status":"queued"}]}' > "$SD/o.json"

FULL_SHA="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BIN_SHA="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

# THE regression test: every update flag must be folded into ONE baseline via
# the IFS-'|' join of the UPDATES array. If that join regresses (e.g. only the
# last filter survives, or the chain becomes invalid jq) the assertions below
# fail and the suite returns nonzero.
MERGE_OUT="$(bash "$S" --baseline "$SD/b.json" --orders "$SD/o.json" \
  --sha "$FULL_SHA" --run-id 42 --binary-sha "$BIN_SHA" --features telegram,fastlane \
  --marker width=1600 --found 1 --evidence 'probe line 12' \
  --dry-run 2>/dev/null)"
merge_rc=$?

if [ "$merge_rc" != 0 ]; then
  bad "oc-seal-state multi-filter merge: tool exit $merge_rc (expected 0) — IFS-pipe merge broken"
else
  if jq -e --arg s "$FULL_SHA" --arg b "$BIN_SHA" '.sha==$s and .deployed_sha==$s and .run_id==42 and .binary_sha256==$b and .features=="telegram,fastlane" and .name=="keep-me" and .already==true and (.feature_presence["width=1600"].found==1) and (.feature_presence["width=1600"].marker=="width=1600") and (.feature_presence["width=1600"].evidence=="probe line 12")' <<<"$MERGE_OUT" >/dev/null 2>&1; then
    ok "merge: sha+run_id+binary+features+marker all folded by the IFS-pipe join"
  else
    bad "merge: some update(s) dropped by the IFS-pipe join"
    printf '    --- got ---\n%s\n    --- end ---\n' "$MERGE_OUT" | sed 's/^/    /'
  fi
fi

# combined orders transition + baseline merge in one call (multi-path pipe)
ORD_OUT="$(bash "$S" --baseline "$SD/b.json" --orders "$SD/o.json" \
  --sha "$FULL_SHA" --run-id 7 \
  --add-order "$FULL_SHA" --order-features fast \
  --dry-run 2>/dev/null)"
ord_rc=$?
if [ "$ord_rc" != 0 ]; then
  bad "oc-seal-state baseline+orders multi-path: tool exit $ord_rc (expected 0)"
else
  ORD_JSON="$(printf '%s\n' "$ORD_OUT" | sed -n '/^--- orders ---$/,$p' | tail -n +2 | jq -c .)"
  if [ -n "$ORD_JSON" ] && jq -e --arg s "$FULL_SHA" \
      '.orders[] | select(.sha==$s and .features=="fast" and .status=="queued")' \
      <<<"$ORD_JSON" >/dev/null 2>&1; then
    ok "seal-state baseline+order-add multi-path works"
  else
    bad "seal-state baseline+order-add multi-path: order transition lost"
    printf '    --- orders got ---\n%s\n' "$ORD_JSON" | sed 's/^/    /'
  fi
fi
rm -rf "$SD"

# ---- result ---------------------------------------------------------------
echo
echo "SUITE RESULT: $PASS ok, $FAIL fail"
[ "$FAIL" = 0 ]