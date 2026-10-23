#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# The diff gate on your own machine (docs/DELTA-DIFF-GATE.md): exactly what the pull request runs, so a survivor shows up before the push.
#
#   [FORCE_DIFF=1] tools/diff-mutation-local.sh [BASE]    BASE defaults to origin/main (the merge base with HEAD is used)
#
# 1. tools/diff_gate.py scope: the mode (diff or full) and the changed lines; in `full` it says why and stops - the pull request would run everything.
# 2. Rust: cargo mutants --in-diff; Go: go-mutesting with tools/mutation_diff_exec.sh (a mutant outside the diff is `offdiff`).
# 3. The score of the mutants in the diff, by the rule of the gate (more than 90% killed, all of them when fewer than 10), and the survivors.
# Needs cargo-mutants and go-mutesting on the PATH, and CIC_PRIMITIVES_ROOT / CIC_REGISTRY_ROOT set as for the tests. Takes minutes, not an hour.
set -u
JOBS=${JOBS:-3}   # cargo-mutants jobs, each with its own copy of the tree; Go runs one process per changed file, also in its own copy
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BASE=$(git -C "$ROOT" merge-base "${1:-origin/main}" HEAD) || exit 2
T=$(mktemp -d "${TMPDIR:-/tmp}/diff-mutation.XXXXXX")
echo "scope against $BASE"
python3 "$ROOT/tools/diff_gate.py" scope --base "$BASE" --head HEAD --out "$T" --root "$ROOT" || exit 2
mode=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mode"])' "$T/scope.json")
if [ "$mode" != diff ] && [ -z "${FORCE_DIFF:-}" ]; then echo "mode is $mode: the pull request would run the full mutation; set FORCE_DIFF=1 to measure only the mutants in the changed lines anyway"; exit 0; fi

mkdir -p "$T/rust" "$T/go"
(cd "$ROOT/rust" && cargo mutants --list --json --in-diff "$T/rust.patch") > "$T/rust-listed.json" 2>/dev/null
n=$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$T/rust-listed.json" 2>/dev/null || echo 0)
echo "rust: $n mutants in the diff"
if [ "$n" -gt 0 ]; then
  (cd "$ROOT/rust" && CIC_FORMS_ROOT="$ROOT" cargo mutants --in-diff "$T/rust.patch" --output "$T/rust" --jobs "$JOBS" --timeout 120 -- --tests) > "$T/rust.log" 2>&1 &
  rust_pid=$!
else
  echo '{"caught": 0, "missed": 0, "timeout": 0, "unviable": 0, "total_mutants": 0, "outcomes": []}' > "$T/rust/mutants.out.json"
fi

command -v go-mutesting >/dev/null || { echo "go-mutesting is not on PATH (go install github.com/avito-tech/go-mutesting/cmd/go-mutesting; add GOPATH/bin to PATH)" >&2; exit 2; }

files=$(python3 -c 'import json,sys; print(" ".join(p[len("go/"):] for p in json.load(open(sys.argv[1]))))' "$T/diff-lines.json")
: > "$T/ledger"
echo "go files in the diff: ${files:-none}"
for f in $files; do
  # go-mutesting rewrites the file under test in place, so each file gets a copy of the module and they run side by side
  c="$T/gocopy/${f//\//_}"; mkdir -p "$c"; cp -a "$ROOT/go" "$c/go"
  (cd "$c/go" && CIC_FORMS_ROOT="$ROOT" CIC_MUTATION_SHARD=0/1 CIC_MUTATION_LEDGER="$T/ledger" CIC_DIFF_LINES="$T/diff-lines.json" CIC_REPO_ROOT="$c" \
     go-mutesting --exec "$ROOT/tools/mutation_diff_exec.sh" "$f") > "$T/go-${f//\//_}.log" 2>&1 &
done
wait
[ -n "${rust_pid:-}" ] && tail -n 1 "$T/rust.log"
cat "$T"/go-*.log > "$T/go.log" 2>/dev/null

python3 - "$T" "$ROOT" <<'PY'
import json, os, re, sys
t, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "tools"))
import quality_gate as q
ok = True
try:
    out = os.path.join(t, "rust", "mutants.out", "outcomes.json")
    d = json.load(open(out)) if os.path.exists(out) else {"caught": 0, "missed": 0, "timeout": 0}
    k, total = d["caught"] + d["timeout"], d["caught"] + d["timeout"] + d["missed"]
    good, line = q.judge_diff("rust mutation (diff)", lambda: (k, total), out)
    print(("ok   " if good else "FAIL ") + line)
    ok &= good
    for o in d.get("outcomes", []):
        if o.get("summary") == "MissedMutant":
            print("   survived:", o["scenario"]["Mutant"]["name"])
    good, line = q.judge_diff("go mutation (diff)", lambda: q.go_ledgers([os.path.join(t, "ledger")], allow_empty=True), "ledger")
    print(("ok   " if good else "FAIL ") + line)
    ok &= good
    log = open(os.path.join(t, "go.log")).read() if os.path.exists(os.path.join(t, "go.log")) else ""
    for m in re.finditer(r'FAIL "[^"]*/([^"/]+)\.(\d+)"', log):
        print("   survived: %s mutant %s (see %s/go.log)" % (m.group(1), m.group(2), t))
except q.Refused as r:
    print("FAIL %s: %s" % (r.reason, r.detail)); ok = False
print("DIFF MUTATION: %s   (work files: %s)" % ("PASS" if ok else "FAIL", t))
sys.exit(0 if ok else 1)
PY
