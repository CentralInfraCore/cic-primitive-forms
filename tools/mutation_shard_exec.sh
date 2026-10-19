#!/bin/bash
# go-mutesting executor for one shard of a sharded run. It is go-mutesting's own test-mutated-package
# script (replace the file with the mutant, `go test` the package, exit 0 = killed, 1 = survived,
# 2 = did not compile) with two additions:
#   - a mutant is tested by the shard that owns it, the one numbered `hash(mutant) mod N`; every other shard
#     reports it as skipped (exit 2) without running a test;
#   - every mutant, owned or not, is written to the ledger (CIC_MUTATION_LEDGER) as
#     `<mutant>\t<shard>\t<shards>\t<verdict>`, so that tools/quality_gate.py can check that each mutant was
#     tested by exactly one shard and that no shard is missing.
# Each shard runs on its own checkout (the executor swaps the file in place). The same CIC_MUTATION_SHARD
# (`k/N`) and a ledger of its own go with each. The mutant is named by the digest of the mutated file.
set -u
: "${MUTATE_CHANGED:?}" "${MUTATE_ORIGINAL:?}" "${MUTATE_PACKAGE:?}" "${CIC_MUTATION_SHARD:?}" "${CIC_MUTATION_LEDGER:?}"
shard=${CIC_MUTATION_SHARD%/*}
shards=${CIC_MUTATION_SHARD#*/}
id=$(sha256sum < "$MUTATE_CHANGED" | cut -c1-16)
owner=$(( 16#${id:0:8} % shards ))
ledger() { printf '%s\t%s\t%s\t%s\n' "$id" "$shard" "$shards" "$1" >> "$CIC_MUTATION_LEDGER"; }

if [ "$owner" != "$shard" ]; then
	ledger offshard
	exit 2
fi

diff_out=$(diff -u "$MUTATE_ORIGINAL" "$MUTATE_CHANGED")
mv "$MUTATE_ORIGINAL" "$MUTATE_ORIGINAL.tmp"
trap 'mv "$MUTATE_ORIGINAL.tmp" "$MUTATE_ORIGINAL"' EXIT
cp "$MUTATE_CHANGED" "$MUTATE_ORIGINAL"
go test -timeout "${MUTATE_TIMEOUT:-10}s" "$MUTATE_PACKAGE" >/dev/null 2>&1
case $? in
0) ledger survived; echo "$diff_out"; exit 1 ;;
1) ledger killed; exit 0 ;;
2) ledger uncompilable; exit 2 ;;
*) ledger unknown; exit 3 ;;
esac
