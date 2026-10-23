# The quality job in shards

The `quality` gate of workflow `p1` (line coverage and mutation score strictly above 90%, both languages,
`tools/quality_gate.py`) used to run its four measurements one after the other in one job, about an hour. It
runs now as parallel jobs and one deciding job. **The gate, its thresholds and the mutants are unchanged**; only
how the mutants are spread over machines is new.

| Job | What it does |
|---|---|
| `quality-lines` | line coverage of both languages |
| `quality-mutation-rust` × 6 | `cargo mutants -F <the shard's mutants>`, dealt by measured cost (below), one `outcomes.json` each |
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
* **A report must agree with itself** (#77). A cargo-mutants `outcomes.json` carries its totals and one outcome per
  mutant; the gate counts the outcomes and requires `caught`, `missed`, `timeout`, `unviable` and `total_mutants` to
  match them, and a mutant outcome without a verdict (`Success`, `Failure`) is refused. A go-mutesting stdout carries
  its summary line and a `PASS` / `FAIL` / `SKIP` / `UNKOWN` line per mutant; `passed`, `failed`, `skipped` and
  `total` must match those lines. The score is computed from the per-mutant results, and any disagreement is
  `REPORT_INCONSISTENT`. Before this, totals of 500 caught over a single missed outcome passed as 100%.

`quality_gate.py selftest` has a fault for each of these (a shard that did not run, a mutant tested twice, a mutant
tested by none, a mutant not in the list, shards that disagree, a garbage or empty ledger, a merged score below
90%), next to the 21 it had.

The number of shards is written twice, in the matrix and in `RUST_SHARDS` (Rust), or in the matrix and in the command
(Go); if they disagree a shard is missing and the gate is red. A test (`tools/test_shard_plan.py`) also checks that the Rust
matrix and `RUST_SHARDS` agree.

## The Rust shards are dealt by measured cost

`cargo mutants --shard k/N` deals the mutants round robin. A mutant that runs into the test timeout costs the whole
timeout (120 s plus the build), and on the sharded runs of 2026-10-19/20 nine such mutants made one of four shards take
20 minutes while the others took 5 to 10: that shard was the longest job of the workflow and decided its length.

`tools/shard_plan.py` deals them from a table of measured costs instead (`tools/mutation-costs.json`, seconds per mutant, taken
from the `outcomes.json` of a real run): the most expensive first, each onto the shard with the least load so far, ties by name,
so every shard job computes the same answer. With the measured costs and 6 shards the predicted load is 6.5 minutes on every
shard (the sum is 39 minutes).

* **Nothing about the measurement changes.** The mutants, the tests, `--timeout 120` and the thresholds are the same. The
  timeout is not shortened for speed: a timed-out mutant counts as caught, so a shorter timeout would change what the score
  means.
* **The gate is the check.** It wants every listed mutant in exactly one shard's report, however they were dealt. In addition,
  each shard runs `cargo mutants --list -F <regex>` before it runs anything and `shard_plan.py verify` stops it unless the
  regex selects exactly the planned mutants.
* **A stale table costs balance, not correctness.** A mutant the table does not know gets the mean cost; a cost for a mutant that
  is gone is ignored. Keys do not contain line numbers, so moving code does not invalidate them. The log of every shard says how
  many mutants had no measured cost.
* **Refreshing the table** is a reviewed data change: download the `rust-mutants-*` artifacts of a green run on `main`, then
  `python3 tools/shard_plan.py costs --outcomes <dir>/rust-mutants-*/outcomes.json --out tools/mutation-costs.json`.
* The tests are `tools/test_shard_plan.py` (run by `quality-gate-selftest`, which also checks that every file `p1` uses is in its `pull_request.paths`), with 12 seeded defects of the planner (longest
  first turned around, round robin, a mutant dropped, an unanchored or unescaped regex, a weak `verify`, ...), each of which has
  to turn a test red.

## The tool cache

`cargo-mutants`, `cargo-llvm-cov`, `cargo-fuzz` and `go-mutesting` were built from source in every job (one to two and a half
minutes each, in the deciding job all three). `.github/actions/cached-tool` caches the built binary and nothing else. Its key
holds the kind, the exact pinned crate or module version, the compiler that builds it (`rustc +stable --version` or `go
version`), the runner image (`ImageOS`, `ImageVersion`) and the CPU: a new Rust release, a new image or a new tool version is a
cache miss and the tool is built again from its pinned source. A restored binary is used only if it passes a check, otherwise it is built again from the pinned source
(`.github/actions/cached-tool/install.sh`): a cargo tool has to run as `cargo <subcommand> --help`; a Go tool has to carry the build
information of the pinned package and module version (`go version -m`) and has to be startable (exit status below 126). That the
executable bit is set is not enough. `tools/test_cached_tool.py` covers a good hit, a miss, a missing file, a corrupt or foreign
binary, another version, another package, another module, a tool that cannot start and a bad fresh build, and has 10 seeded
defects of the script, each of which has to turn a test red. The evidence
record reads the tool versions from the binaries at run time, not from the cache key. Caches written on a pull request are not
readable on `main`.

## Same numbers as before

The sharded run gives the numbers of the one-job run: the PR that introduced this compared them on the same
commit (Rust and Go survivors and counts, see the PR).
