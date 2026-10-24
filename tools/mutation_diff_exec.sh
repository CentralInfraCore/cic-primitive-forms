#!/bin/bash
# go-mutesting executor for the diff gate (docs/DELTA-DIFF-GATE.md, section 3): like tools/mutation_shard_exec.sh, but a mutant that
# does not change a line of the diff of the pull request is not tested: it is written to the ledger as `offdiff` (exit 2, skipped).
#   MUTATE_CHANGED / MUTATE_ORIGINAL / MUTATE_PACKAGE   set by go-mutesting
#   CIC_MUTATION_SHARD   `k/N`   this shard of the diff run (#92); a mutant belongs to shard (digest mod N), as in tools/mutation_shard_exec.sh,
#                        and only its own shard decides it: every other shard writes `offshard`, so each mutant has one verdict
#   CIC_MUTATION_LEDGER  the ledger file: `<mutant>\t<shard>\t<shards>\t<verdict>`
#   CIC_DIFF_LINES       tools/diff_gate.py scope: diff-lines.json (the changed line ranges per product file)
#   CIC_REPO_ROOT        the root the keys of diff-lines.json are relative to
#   CIC_MUTATION_LIST_ONLY  `1`: count, do not test - a mutant of the diff that this shard owns answers PASS (exit 0) and nothing is written
#                          (diff-plan counts the mutants of the diff with shard 0/1 to size the shards)
#   CIC_MUTATION_TEST_RUN  optional `go test -run` regex: the development loop runs only the tests of the code (a killed mutant stays killed
#                          in the full run; a survivor must be rechecked without it). CI never sets it.
# Verdicts: killed (the tests failed: exit 0), survived (they passed: exit 1), uncompilable (exit 2), offdiff (exit 2), unknown (exit 3).
set -u
: "${MUTATE_CHANGED:?}" "${MUTATE_ORIGINAL:?}" "${MUTATE_PACKAGE:?}" "${CIC_MUTATION_SHARD:?}" "${CIC_MUTATION_LEDGER:?}" "${CIC_DIFF_LINES:?}" "${CIC_REPO_ROOT:?}"
HERE=$(cd "$(dirname "$0")" && pwd)
shard=${CIC_MUTATION_SHARD%/*}
shards=${CIC_MUTATION_SHARD#*/}
id=$(sha256sum < "$MUTATE_CHANGED" | cut -c1-16)
owner=$(( 16#${id:0:8} % shards ))
ledger() { [ "${CIC_MUTATION_LIST_ONLY:-}" = 1 ] || printf '%s\t%s\t%s\t%s\n' "$id" "$shard" "$shards" "$1" >> "$CIC_MUTATION_LEDGER"; }

# the shard first: offdiff is a verdict, and only the owner may give one
if [ "$owner" != "$shard" ]; then
	ledger offshard
	exit 2
fi
rel=$(realpath --relative-to="$CIC_REPO_ROOT" "$MUTATE_ORIGINAL")
if ! python3 "$HERE/diff_gate.py" in-diff --lines "$CIC_DIFF_LINES" --path "$rel" --original "$MUTATE_ORIGINAL" --changed "$MUTATE_CHANGED"; then
	ledger offdiff
	exit 2
fi
[ "${CIC_MUTATION_LIST_ONLY:-}" = 1 ] && exit 0

diff_out=$(diff -u "$MUTATE_ORIGINAL" "$MUTATE_CHANGED")
mv "$MUTATE_ORIGINAL" "$MUTATE_ORIGINAL.tmp"
trap 'mv "$MUTATE_ORIGINAL.tmp" "$MUTATE_ORIGINAL"' EXIT
cp "$MUTATE_CHANGED" "$MUTATE_ORIGINAL"
go test ${CIC_MUTATION_TEST_RUN:+-run "$CIC_MUTATION_TEST_RUN"} -timeout "${MUTATE_TIMEOUT:-10}s" "$MUTATE_PACKAGE" >/dev/null 2>&1
case $? in
0) ledger survived; echo "$diff_out"; exit 1 ;;
1) ledger killed; exit 0 ;;
2) ledger uncompilable; exit 2 ;;
*) ledger unknown; exit 3 ;;
esac
