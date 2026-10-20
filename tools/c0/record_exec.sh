#!/bin/bash
# Enumeration-only executor for go-mutesting: records each mutant (the mutated file) and the original it was made from, runs no test.
# exit 2 = "did not compile" in go-mutesting's exec protocol, so the mutant is skipped without a test run.
set -u
: "${MUTATE_CHANGED:?}" "${MUTATE_ORIGINAL:?}" "${C0_REC:?}"
id=$(sha256sum < "$MUTATE_CHANGED" | cut -c1-16)
base=$(basename "$MUTATE_ORIGINAL")
mkdir -p "$C0_REC/mut" "$C0_REC/orig"
[ -f "$C0_REC/orig/$base" ] || cp "$MUTATE_ORIGINAL" "$C0_REC/orig/$base"
cp "$MUTATE_CHANGED" "$C0_REC/mut/$id.go"
printf '%s\t%s\n' "$id" "$base" >> "$C0_REC/index.tsv"
exit 2
