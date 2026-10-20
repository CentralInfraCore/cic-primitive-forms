#!/bin/bash
# Enumerate every go-mutesting mutant of a Go package without running a test, and time it (docs/C0-GO-MUTANT-ID-STUDY.md).
#
#   enumerate.sh <go package dir> <record dir>
#
# go-mutesting (the version pinned in .github/workflows/p1.yml) has to be on the PATH, and so does the Go toolchain; the corpus
# tests are not run, but go-mutesting type-checks the package, so CIC_PRIMITIVES_ROOT is set as in CI if it is given.
# The record dir gets: index.tsv (<digest of the mutated file> <file>), orig/<file>, mut/<digest>.go, out.txt, time.txt.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
export C0_REC="$2" MUTATE_TIMEOUT=10
rm -rf "$C0_REC"; mkdir -p "$C0_REC"
cd "$1"
start=$(date +%s.%N)
go-mutesting --exec "$HERE/record_exec.sh" ./... > "$C0_REC/out.txt" 2>&1 || true
end=$(date +%s.%N)
echo "enumeration seconds: $(echo "$end - $start" | bc)" | tee "$C0_REC/time.txt"
echo "mutants recorded: $(wc -l < "$C0_REC/index.tsv")"
