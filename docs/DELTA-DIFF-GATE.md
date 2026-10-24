# The diff gate and the nightly FULL: the decided model

Status: **decision record and plan, nothing here is implemented.** The owner changed the DELTA model of `docs/FULL-DELTA-PLAN.md` on
2026-10-22. This document is the model that now applies; the plan keeps what it says about FULL evidence (sections 5, A and B) and is
amended where this one replaces it (see "What this replaces"). It does not change a threshold.

## 1. The model in one paragraph

A pull request is gated on the mutants **in the code it touches** (the diff gate), not on a re-run of every mutant. The whole
repository's mutation score is measured by a **FULL run on `main` every night**, only if `main` had development in the last two days. If
that run fails, a pull request is opened that carries the identifier of the failed run. DELTA is no longer a shadow mode: the diff
gate is the PR gate. The nightly FULL is the safety net, and the place where the two can be compared.

## 2. The diff gate (what a pull request must show)

1. The mutants are enumerated on the merge candidate and **restricted to those whose mutated lines are lines the PR adds or
   changes** (the cumulative diff against the target branch).
2. More than 90% of those mutants must be killed (a timeout counts as killed, as in the FULL gate). With **fewer than 10** mutants in
   the diff, all of them must be killed, or each survivor has a written justification in the PR (the reviewer decides on it).
3. **No mutant in the diff** (documentation, a pure test addition without product code): the gate is green and the report says so.
4. The per-mutant timeout is **not** shortened and the threshold is not lowered. Only the set of mutants changes.
5. There is no baseline-diluted aggregate: a weak diff is not hidden behind a large old baseline (a diff of 10 surviving mutants on a
   baseline of 2000 at 95% would still read 94.5%). The repository-wide score is the nightly run's job.

### 2.1 Changes in test files

What counts is whether the change can **weaken** the protection that existing tests give to code the diff does not touch.

| Change | Mutants run |
|---|---|
| a **pure addition**: a new test function, a new test file, a new case in a table; no removed or modified line, the added lines are whole new tests | the diff's mutants only (an addition can only add kills) |
| a removed, modified or added line **inside an existing test function** | the diff's mutants **plus the mutants on the product lines the base version of that test executed** (2.2) |
| a modified existing shared helper, fixture or `testdata` line, a line outside any test function, a deleted test file | **FULL** on that PR |

The classification is made from the diff by a script (a hunk with no removed line whose added lines are complete new `func Test...` /
`#[test] fn` items, or a new file, is an addition; a line added inside an existing function is a modification). When the classifier is
unsure, it says FULL.

### 2.2 A changed test is narrowed by its coverage (#92)

Narrowing the FULL case by per-test coverage was the "later optimisation" of the first version of this section, to come if the FULL case
turned out to be frequent. It did: every product pull request of 2026-10-23/24 ran FULL, most of them because a fix that changes behaviour
changes the test that pinned the old behaviour.

* **Which tests.** Each hunk of a test file is mapped, on the base, to the test function that holds it: Go `func TestX`/`FuzzX` up to its
  closing `}`, Rust a `fn` with `#[test]` among its attributes (in `rust/tests/<target>.rs`, in a `#[cfg(test)]` module of a source file,
  in a `proptest!` block). A line that no test function holds keeps the PR FULL, as before.
* **What they executed.** The `scope` job checks the base out in a worktree and runs exactly those tests, in their base version, with line
  coverage (`go test -run '^(...)$' -coverprofile`, `cargo llvm-cov --lcov --lib|--test <target> -- <names>`). Every named test must run
  and pass.
* **Why that is enough.** A mutant on a line the old test did not execute was not killed by it, so replacing the test cannot change the
  mutant's status; it still depends only on the unchanged tests. The executed lines are carried over to the head (a line the PR changed
  is in the diff already) and join the diff: their mutants run as the diff's do, under the same rule.
* **Fail closed.** `diff_gate.py scope` without `--measure` reports a pending measurement as a reason for FULL; the measurement turns it
  into `diff` only when it worked, and any failure (a test that does not run or fails on the base, a coverage of nothing, a tool error)
  leaves FULL with the reason. The toolchains are set up in `scope` only when a changed test is the only reason for FULL.
* **The diff is sharded.** A narrowed diff can be large (a test that parses YAML executes much of the parser), so the diff mutation runs
  in shards like the full run: the `diff-plan` job counts the mutants of the diff (Rust `cargo mutants --list --in-diff`, Go the diff
  executor in its list mode), `tools/shard_count.py` cuts them with the same budget, Rust shards are dealt by measured cost
  (`shard_plan.py`) and Go shards by digest, as in the full run; the gate checks every shard against the one list.

## 3. How each language does it

* **Rust:** `cargo mutants --in-diff <patch>` (cargo-mutants 27.1 has it) runs only the mutants in code the patch touches; in shards,
  `-F` with the regex `shard_plan.py` gives each shard (2.2).
* **Go:** `go-mutesting` has no diff option (only `--match`, a function-name regex). The executor `tools/mutation_shard_exec.sh`
  already receives the original and the mutated file for every mutant; it compares the changed lines with the PR's changed lines and
  answers a mutant outside the diff with the verdict `offdiff` (exit 2, like `offshard`), written to the ledger. `quality_gate.py`
  must count `offdiff` as neither killed nor survived, and the completeness check must know that every mutant has exactly one of
  killed, survived, uncompilable, offshard, offdiff. A Go mutant that spans a diff boundary is run (conservative).

## 4. The nightly FULL

* A scheduled workflow, every day at **02:00 Europe/Budapest**. GitHub cron is UTC only, so it is set to 01:00 UTC for winter time
  (CET) and needs one change at the start of summer time (CEST, 00:00 UTC), unless the workflow itself checks the local hour.
* It first checks whether `main` has commits in the last **48 hours** (two days, not one: a scheduled run can start late by an unknown amount, so the window must not depend on the hour it started); without development it ends green and says so. A manual run can force it.
* It runs the full `p1` gate on `main` (every mutant, both languages), through a reusable workflow so that there is one definition of
  the gate.
* **On failure it opens a pull request.** The minimum content of the description: the identifier and URL of the failed run, the commit
  SHA, the failed jobs. The branch carries a small record file (`ci-incidents/<date>-<run id>.md`) with the same facts, because a pull
  request needs a commit. Actions must be allowed to create pull requests (a repository setting only the owner can change); without
  it the failure is still visible on the run, and the workflow says so.
* The nightly result is also the **comparison**: a PR that passed the diff gate and a `main` that fails FULL the next night shows
  where the diff gate missed something, and that is a defect of the gate to be fixed, not a reason to relax it.

### 4.1 No heavy post-merge on `main`

Decided by the owner on 2026-10-22: once the diff gate is the PR gate (step S), a push to `main` no longer starts the full mutation
run. The check of `main` is the nightly FULL above; running the whole gate again after every merge, when `main` is measured every
night, is waste. What stays on a push, by default, is only what is cheap (the `corpus` job and the unit and property tests, a minute or
two), as a smoke test that `main` still builds; the owner can drop it.

The price is known and accepted: two pull requests that were each fine can break something together, and `main` shows it only the next
night (or at the next smoke test, for the cheap kind of breakage). The 48-hour window and the pull request about the failed run are
what make that visible.

**Implemented** in `p1.yml`: the `scope` job answers `light` to a push; `fuzz`, `seeded-defects`, `evidence-selftest`, the mutation jobs and `quality` do not run then; the unit tests, the corpus, the coverage and the parity do. The nightly FULL (`nightly.yml`, `workflow_call`) and by-hand runs stay `full`. The evidence workflow (`evidence.yml`, off) collects the `p1` runs of a commit of main as its FULL record; with a light push there is none, so it has to be pointed at the nightly run before it is switched on.

Consequences for the tools, done in step S: the post-merge profile (`tools/ci-required/<repo>.post-merge.txt` of cic-factory) lists
only the light set, and `land-chain.sh` waits for that set, not for `quality` and the mutation shards. Until S the full `p1` still runs on
every push, and the profile is the old one.

### 4.2 A change of the gate workflow (`p1.yml`): the pull request is fast, a FULL runs in the background

Owner's decision, 2026-10-23. A change of `.github/workflows/p1.yml` used to make the pull request run `full` (it changes how the gate
measures, 2.1). It no longer does: the pull request runs the diff mode, and the **gate self-tests stay mandatory on it**
(`quality-gate-selftest`, the seeded proof, `scope`: they show in minutes whether the gate can still fail). The full proof moves
behind the merge:

* `nightly.yml` also runs on a **push to `main` that changes `p1.yml`** (the `changed` job answers yes for a push), through the same
  `p1` and `corpus` workflows, with `full: true` (the called `p1.yml` would otherwise read a push as `light`).
* It can be started by hand at any time: `gh workflow run nightly.yml -f force=true` (the same workflow, `workflow_dispatch`).
* If it fails, a pull request is opened that carries the failed run (an issue if the repository does not allow it), as for the nightly
  run; the report names the kind: "FULL after a change of the gate workflow", "Nightly FULL" or "FULL (by hand)".
* The cost, stated: for the time the FULL runs, `main` holds a change of the gate that has not yet been proved on every mutant. A
  weakening that the self-tests do not show is found by the FULL after the merge, not before it. Other gate files
  (`quality_gate.py`, `diff_gate.py`, `mutation_*`, the build configuration) still make the pull request `full`.

## 5. What this replaces in `FULL-DELTA-PLAN.md`

* Section 2, first bullet (DELTA starts as shadow mode, "reports, does not decide"): replaced; the diff gate decides.
* Section 6 (the selection by baseline records, the coverage map, body digests) and section 8 (`delta-shadow`): superseded; not built.
* Steps C, D, E, F of section 9 (stable mutant ids, the coverage map, the selector battery, `delta-shadow`) are not done. C0 stays as
  the study (it showed that a stable Go mutant id is feasible, and it is not needed now).
* Section 2, "`main` gets a FULL measurement every day": changed to every night **if there was development**.
* Steps A and B (the verified evidence record, the `ci-evidence` branch, the attestation) are **not** part of this decision and stay
  as they are: switched off, an owner decision.
* Section 11 ("what does not change"): thresholds, the per-mutant timeout, the engine, Relay and the ADRs are still unchanged. "A
  merge needs a green FULL, as today" becomes: **a merge needs a green diff gate; the FULL gate on `main` is the nightly run.**

## 6. Proving the gate (a gate that cannot fail proves nothing)

1. A seeded PR whose diff adds product code with a surviving mutant turns the gate red (Rust and Go).
2. A seeded PR that only adds a test is green and runs no extra mutants (the classifier says addition).
3. A seeded PR that modifies an existing test line triggers FULL.
4. A seeded PR that touches only documentation is green with "no mutants in the diff".
5. The ledger completeness check turns red if an `offdiff` mutant is also reported killed, or a mutant has no verdict.
6. The nightly workflow, run by hand on a `main` with a deliberately failing check, opens the pull request with the run id.

## 7. Order of work, each its own PR

| Step | Content |
|---|---|
| N | The nightly workflow, the reusable gate, the failure PR (section 4). Small, and independent of the diff gate. |
| R | The diff gate for Rust (`--in-diff`), the test-change classifier, the proofs of section 6 for Rust. |
| G | The diff gate for Go (`offdiff` in the executor, `quality_gate.py`), the proofs for Go. |
| S | The switch: the PR gate becomes the diff gate; FULL on a PR only in the cases of 2.1; no full run on a push to `main` (4.1) and the post-merge profile of cic-factory and `land-chain.sh` move to the light set. A measurement window against the nightly run first, and the owner's decision to switch. |

## 8. Open points

1. The "fewer than 10" boundary of 2 is a default; the owner can change it.
2. The classifier of 2.1 is conservative by design; its false FULLs are visible in its report and can be tuned.
3. Summer time handling of the nightly cron (section 4).
4. Whether the nightly failure pull request should be a draft, and whether it gets a label; default: draft with the label `ci-nightly-failure`.

## 9. The development loop: the same gate, sooner

The gate is not weakened here; the three tools below only bring its verdict forward, to where the code is still fresh.

1. **Measure while writing.** `WORKTREE=1 tools/diff-mutation-local.sh` measures the working tree (staged, unstaged and new files)
   against the merge base, through a scratch index; the repository is not touched. Run it after each finished function, not after the
   last one. Rust and Go run side by side, `JOBS` (default 3) cargo-mutants jobs, one Go process per changed file, each in its own copy
   of the tree.
2. **Targeted tests, only in the loop.** `TESTS=regex` runs only the tests that match (Go `-run`, Rust test-name filter) for every
   mutant. A killed mutant stays killed when the whole suite runs, so a targeted score can only be **lower** than the real one; a
   survivor must be rechecked without `TESTS` before it is believed or "fixed". CI never sets it, and a green loop is not a green gate. (The Go side needs one line in the diff executor, `tools/mutation_diff_exec.sh`, which changes how the gate measures and therefore comes in its own pull request; until it is merged `TESTS=` has no effect on Go and the whole suite runs, which is slower but never wrong.)
3. **A short local check, not a copy of CI.** `make quick` (`tools/quick-check.sh`): the texts of the seeded defects are checked against
   the code (`seeded_defects.py all --stale-only`, seconds: a stale text would fail the CI step ten minutes in), then the mutants of the
   working tree (1) with the filter of (2). It does **not** run the suites, the coverage gate, the seeded defects or the fuzzing: CI is
   the authority for those, runs them in a clean environment and does not need a developer's machine. (A local copy of CI misleads in
   both directions: the first parallel script reported a void 100%, and a shared build directory of another working tree failed tests
   that pass in CI.) `seeded_defects.py --match SUBSTRING` and `--shard K/N` select or cut the defects when they are run by hand.

Whatever is run locally must be able to fail: the Go copy of the tree runs its unmutated tests first, and the script stops if they fail.

### 9.1 Writing code so that the gate has less to say

Most survivors of the first runs of a new module were **equivalent mutants**: a mutation that cannot change the behaviour, because the
code states the same thing twice. They cost more to remove than to avoid. In this order:

* Prefer string and slice operations (`strings.Index`, `CutPrefix`, `slices.IndexFunc`, `split`, `find`, `strip_prefix`) to index
  arithmetic and hand-written loops with `break`: every `+ 1`, `<` and `break` is a mutation site.
* Do not guard what the callee already guarantees (a bound check before an index that cannot be out of range; a `nil` check before a
  loop over a nil slice). Use a sentinel (append the delimiter you search for; treat NUL as the end of the text) instead of "not found"
  branches that no input reaches.
* Do not assign a value that is already there (`x = nil` after `var x`), and do not keep a branch that the parser makes unreachable (a tab
  handler where the parser refuses tabs); remove it, or test the parser's refusal.
* Keep one fact in one place, so that a mutation of it changes the result.

### 9.2 Bounded units for mutation and for fuzzing (shard count, fuzz and seeded defects implemented, see 9.4; the rest of the group is still a plan)

Observation (owner, 2026-10-23): the bites the tests work on keep growing. A unit of work for a mutation run or a fuzz run is bounded
twice: **about 100 mutants (or inputs) at most, and about 10 minutes of wall time at most, whichever is reached first**, so that a run is
short, a weak part is not diluted by a strong one, and the parts run side by side.

The measured state on the head of #72 (full mode): a Rust shard of 166 mutants took 16 to 25 minutes (6 to 9 seconds per mutant, build and
baseline included), a Go shard 10 to 17 minutes; about 55 to 70 mutants fit in ten minutes, so the time bound is the stricter one. The number
of shards is therefore **computed, not written into the matrix**: `max(ceil(mutants / 100), ceil(estimated seconds / 600))`, the fixed cost of a
job (checkout, build, baseline) included in the estimate. A `plan` job next to `scope` computes it from the listed mutants and the cost table
and passes the matrix on (`fromJSON`), so the shard count has one source; the gate's completeness check still requires every mutant in
exactly one shard. The cost table (`tools/mutation-costs.json`) is refreshed first: it holds 467 Rust mutants and the shards now carry about
1000, and a stale cost only makes the balance worse, never the measurement different.

**Mutation.**

* The unit is a group of about 100 mutants of the diff, cut along function boundaries (a function is never split; a function with more than 100
  mutants is a unit of its own and the report says so). Each unit has its own score and its own verdict by the rule of section 2 (more than
  90% killed, all of them when fewer than 10), and the gate is green only if every unit is. There is still no aggregate that hides a weak
  unit behind a strong one.
* Each unit runs in its own copy of the tree, and a unit can name the tests that cover it (the test file or the name filter of the module);
  that is the targeted run of 9 (2), and the CI run of the same unit uses the whole suite.
* Every copy of the tree must pass its tests unmutated before its mutants are counted. A copy that cannot run its tests (a missing
  `corpus/`, a missing tool) would "kill" every mutant and score 100%; this happened once in the first parallel local script and is the
  reason for the rule.
* Open: `cargo mutants` already has `--shard k/n`; `go-mutesting` has not. The Go units are cut by the executor of
  `tools/mutation_diff_exec.sh` (it already sees every mutant and its position), so the grouping is the same table in both languages
  (`tools/diff_gate.py` writes it into `scope.json`).

**Fuzz.**

* Today each language runs its targets one after the other in one job, 60 seconds each (8 targets: Go about 8 minutes, Rust about 8 minutes plus
  the build). The unit is **one target**, one job per target in a matrix, so the targets run side by side and a failure names its target.
* In diff mode a target runs when the diff touches code it reaches (the target-to-source table is a file in the repository, checked by a
  test, not guessed); the other targets run in the nightly FULL. A target that is not run is reported `not run (not reached by the diff)`,
  never as passed.
* The time of a target is not shortened; what changes is which targets a pull request needs. Where a target covers a large module,
  the corpus of that target is split by input class (the committed seed inputs), each a unit of about 100 seeds, so that no one run depends
  on one long session.
* The canary proof (a committed canary input must turn each target red) stays per target and runs in the same unit.

**The rest of the group** (the other quality steps of `p1.yml` that today run as one long step), by the same rule of a bounded unit
of about 100 with its own verdict, its own copy of the tree and its own name in a failure:

| Step | Today (as read from `p1.yml`; each row is rechecked when its pull request is written) | Unit |
|---|---|---|
| Seeded defects | all defects of a language, one after the other (each runs the whole suite) | the defects of one file (a defect is one test run); a language with more than about 100 defects is cut by file. Each runs in its own copy, the unmutated baseline first. In diff mode only the defects of files the diff touches run, the rest run in the nightly FULL; `--match` already selects them. |
| Branch coverage | one measurement run per language, then the gate | the measurement stays one run (a branch is counted on the whole suite); the **report** is judged per module/file group of about 100 branches, so a weak file shows by name and is not averaged away, and the gate still fails on the first one below its floor. |
| Parser parity | a sampled run, the allow list of exact inputs | the samples are cut into units of about 100 inputs, each its own job and its own digest line; the allow list stays by exact input. |
| Property tests | the cases of a property in one test | one test per property with a fixed case count; no unit has more than about 100 generated cases unless the run is the nightly FULL. |
| Unit tests, corpus | one run | unchanged: they are fast; they are the unit that the targeted run of 9 (2) selects. |

Common rules: a unit that was not run is reported `not run` with the reason, never as passed; the sum of the units is checked
against the whole (every mutant, defect, input and branch is in exactly one unit, as the completeness check of the ledger already
requires for mutants), so cutting cannot lose a part; no threshold and no timeout changes.

Each of these is implemented as its own pull request after the measurement window of section 7 (S); this section is the agreed shape,
not a change of any threshold.

### 9.3 Local sharding of the steps that take more than about ten minutes

Owner's rule (2026-10-23): a step that takes more than about ten minutes on one machine is sharded, at least locally. The measured
candidates and what is done for each:

| Step | Local time (one process) | Local shard |
|---|---|---|
| Seeded defects | about 10 minutes in CI, each defect a whole-suite run | **done:** `seeded_defects.py --shard K/N`; run the N processes side by side by hand. Every defect is in exactly one shard (checked: the shards cover each language once). CI itself is the next place for the same cut (a matrix over the shards). |
| Diff mutation, Rust | minutes per 50 mutants, serial | **done:** `cargo mutants --jobs` (`JOBS`, default 3); `--shard k/n` is available for a larger diff. |
| Diff mutation, Go | serial per file | **done:** one process per changed file, in its own copy (with its unmutated baseline checked); a file of more than about 100 mutants is the next cut (9.2). |
| Fuzz | 8 targets x 60 s per language, one after the other | planned: one process per target (9.2). |
| FULL mutation | an hour or more | not run locally; the CI shards (the existing `shard_plan.py`) are the shape: the same plan with `k/n` run as parallel local processes is the next step if it is wanted on a machine. |

A shard never changes what is measured: the same set, cut into parts, and the sum of the parts is the whole. A local shard count is
bounded by memory more than by cores (each process holds its own copy of the tree and its own target directory); the defaults are chosen
for a 12-core, 32 GB machine that is also in use.

### 9.4 What is implemented of 9.2 and 9.3

* **Shard count (`plan` job, `tools/shard_count.py`, `tools/shard_budget.json`).** `max(ceil(mutants / 100), ceil(mutants * seconds_per_mutant / (600 - 180)))`,
  at least 1 and at most 24. The mutants are listed in the run (Rust: `cargo mutants --list`; Go: the executor `true` answers every mutant, the `PASS` lines
  are counted), the matrices read `fromJSON(plan outputs)`, the Rust dealing and the Go `k/n` read the same count, and `quality` requires the `plan` job to have
  succeeded in `full` and to be skipped in `diff`. On the numbers of #72 (Rust 1059 mutants, Go 2045) this is 16 and 21 shards: the time bound is the stricter
  one for Rust, the size bound for Go. `max_shards` and `max_mutants_per_shard` are the two numbers to change if that is too many parallel jobs (a larger shard
  costs time, not correctness: the gate still requires every mutant in exactly one shard).
* **Fuzz (`fuzz-target` matrix, `tools/fuzz_matrix.py`).** One job per target; the matrix is all 16 targets in `full`, none on a push, and in a diff the targets of a
  language whose product code (ranges of `diff_gate.py`), fuzz targets or seed inputs the diff touches, or both languages when `corpus/` or `testdata/` change.
  The job named `fuzz` joins them (it stays the required name) and reports `not run - no target is needed for this diff`; a matrix that was expected and did not
  run fails it. The table is checked against the code by `tools/test_fuzz_matrix.py`.
* **Seeded defects in a diff.** `seeded_defects.py --changed-from scope.json` runs the defects of the product files the diff changes; the others are reported
  `not run`. A full run runs them all.
* **Not yet:** a finer cut inside a shard by function boundaries, the cost table refreshed from the shard outcomes (`shard_plan.py costs`), the same cut for
  coverage, parity and property tests (9.2), the one line of the diff executor for `TESTS=` on the Go side (#74), and the accepting of the previous run by the
  post-merge check when the merge changes nothing (cic-factory, `ci-gate.py`).
