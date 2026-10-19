#!/bin/bash
# go-mutesting executor that runs only the tests named in CIC_MUTATION_RUN (a `go test -run`
# pattern) instead of the whole package. It exists for the seeded proof that the mutation gate can
# fail: with tests that do not exercise the mutated code the mutants survive and the score drops.
# Same contract as go-mutesting's default script: exit 0 = mutant killed, 1 = survived, 2 = did
# not compile.
set -u
: "${MUTATE_CHANGED:?}" "${MUTATE_ORIGINAL:?}" "${MUTATE_PACKAGE:?}" "${CIC_MUTATION_RUN:?}"
diff_out=$(diff -u "$MUTATE_ORIGINAL" "$MUTATE_CHANGED")
mv "$MUTATE_ORIGINAL" "$MUTATE_ORIGINAL.tmp"
trap 'mv "$MUTATE_ORIGINAL.tmp" "$MUTATE_ORIGINAL"' EXIT
cp "$MUTATE_CHANGED" "$MUTATE_ORIGINAL"
go test -timeout "${MUTATE_TIMEOUT:-10}s" -run "$CIC_MUTATION_RUN" "$MUTATE_PACKAGE" >/dev/null 2>&1
case $? in
0) echo "$diff_out"; exit 1 ;;
1) exit 0 ;;
*) exit 2 ;;
esac
