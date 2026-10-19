# The quality job in shards

The `quality` gate of workflow `p1` (line coverage and mutation score strictly above 90%, both languages,
`tools/quality_gate.py`) used to run its four measurements one after the other in one job, about an hour. It
runs now as parallel jobs and one deciding job. **The gate, its thresholds and the mutants are unchanged**; only
how the mutants are spread over machines is new.

| Job | What it does |
|---|---|
| `quality-lines` | line coverage of both languages |
| `quality-mutation-rust` × 4 | `cargo mutants --shard k/4`, one `outcomes.json` each |
| `quality-mutation-go` × 6 | `go-mutesting` with `tools/mutation_shard_exec.sh`, one ledger each |
| `quality` | downloads the reports, lists the Rust mutants, runs the gate, then the two seeded proofs that weakened tests turn it red |

The job that is named `quality` is the one that decides, so a required check of that name keeps its meaning. It runs
`if: always()` and starts by requiring the three measurement jobs to have succeeded: a skipped job would count as
passed, a failed shard must not.

## Why sharding does not make the gate weaker

A sharded measurement can lose mutants without anyone noticing (a shard that did not run, a mutant that two shards
tested, one that none tested, shards that disagree on which mutants there are), and the score would still look
fine. The gate therefore checks completeness before it computes anything, and any gap is `INCOMPLETE_SHARDS`:

* **Rust.** `cargo mutants --list --json` of the whole crate is the set of mutants there are. Every one of them
  must appear in exactly one shard's `outcomes.json`, and none may appear that is not in the list. Several shard
  reports without that list are refused.
* **Go.** go-mutesting has no shard option. Every shard generates all mutants and `tools/mutation_shard_exec.sh`
  tests the ones whose digest modulo the number of shards is its own, reporting the others as skipped. Each shard
  writes a ledger, a line per mutant (`<mutant> <shard> <shards> <verdict>`). The gate requires shards `0` to `N-1`,
  the same mutants in every ledger, and each mutant tested by exactly one shard. The score is read from the
  ledgers, not from go-mutesting's own summary.
* The score is the sum over the shards, with the same rules as before: Rust `(caught + timeout) / (caught +
  timeout + missed)`, Go `killed / (killed + survived)`, unviable and uncompilable mutants in neither.

`quality_gate.py selftest` has a fault for each of these (a shard that did not run, a mutant tested twice, a mutant
tested by none, a mutant not in the list, shards that disagree, a garbage or empty ledger, a merged score below
90%), next to the 21 it had.

The number of shards is written twice, in the matrix and in the command (`4` and `6`); if they disagree a shard
is missing and the gate is red.

## Same numbers as before

The sharded run gives the numbers of the one-job run: the PR that introduced this compared them on the same
commit (Rust and Go survivors and counts, see the PR).
