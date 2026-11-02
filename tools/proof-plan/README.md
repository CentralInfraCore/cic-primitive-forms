# Proof-aware execution planner (report only)

Question asked before expensive work: **is there already valid evidence that makes this work unnecessary?** This directory is the first
consumer of the verification kernel studied on the branch `lab/verification` (its method, probes and measurements are in
`lab/RESULTS.md` there). It does not change what any gate of `p1` means, skips nothing, and no other workflow depends on it.

## What it does now

Workflow `proof-plan.yml` (not a required check, `continue-on-error`) on pull requests and on pushes to `main`:

1. makes the **evidence of the tree** for every Go and Rust test: the Merkle key of the closure of the test (graph of the lab's extractors:
   `extractor/typeddeps`, `extractor/scipdeps`), the constructs in it and whether the analysis was complete, the digest of the data files,
   the digest of the environment (lockfiles, toolchains, **the command the gate runs**);
2. **qualifies the extractor builds** by probes (`qualify.py`, `qualify_rust.py`, fixtures in `testdata/`): the Assurance is measured on the
   binary that runs, never declared; a build that fails a required probe is qualified for nothing;
3. finds the **evidence of an earlier run** (`find_prev.py`): the artifact `proof-evidence` of the newest successful run on `main` whose commit
   is an ancestor of the tested commit (not only the parent: the nearest ancestor that has evidence);
4. lets the kernel (`evidence.py`) decide each test: `REUSE_ELIGIBLE`, `RERUN_REQUIRED`, `UNVERIFIABLE`; `NO_EVIDENCE` when there is none;
5. **runs the tests anyway** with the commands of `p1` and sets each decision against what the test did (`record.py`): a reuse the test
   contradicts is `FALSE_REUSE`;
6. writes the **Proof-Aware Execution Report** to the job summary and leaves `evidence.json` (this tree with the observed verdicts) as the
   artifact for the next run. A test whose key did not move and whose outcome flipped is marked `unstable` and is not reused.

Everything without a rule of its own (mutation, coverage, fuzzing, parity, lint, the engine harnesses ...) is EXECUTE: the planner does not
decide it, and a reuse right earned for tests is not a right for them. A skipped job is never a success.

## What it does not do, yet

- It skips nothing. Turning decisions into a plan that the jobs obey is a later step, taken only after real runs show `FALSE_REUSE = 0` and a
  net saving that is measured (the report counts the cost of the planner against the test time it could have spared; Rust has no per-test
  time, so it is not counted).
- Data edges: each test is run alone under `strace` (`trace.py`, ported from the lab) and the evidence keeps the files it read; the next run
  compares the content, in the new tree, of exactly those files (and the listing of the directories it listed). A test with an unresolved read,
  or whose traced run failed, is never reused. Reads are measured for the tests that are not REUSE_ELIGIBLE and carried on for those that are
  (the same closure, data and environment: the same files; the lab made the same assumption). Without earlier edges the digest falls back to
  the coarse one (any data file changes every test). Tested locally on this tree: a file read by 2 tests changes 2 decisions, a file nobody
  reads none, `corpus/pins.yaml` (68 tests) 68.
- The evidence store is the artifact of the previous run (90 days). The signed publication of `evidence.yml` / branch `ci-evidence` is off
  (`EVIDENCE_ENABLED`) and is the owner's decision; this record is not signed and is not the record of `docs/TEST-EVIDENCE.md`.
- Two things in the lab history were weak and are weak here: the claims cover the **mechanisms the probes name** (interface conversions and
  type arguments in Go, implicit calls in Rust, `iota` groups), not all of Go or Rust; and tests that draw their cases or measure time are
  only caught as `unstable` after they flip once.

## Mutation carry-over (prediction only, offline prototype)

`mutplan.py` asks, for each CaughtMutant of an earlier FULL run (cargo-mutants `outcomes.json`), whether the verdict could stand for the
current tree: the enclosing function node is unchanged (source and Merkle key), and every test that reached it before is still there,
still reaches it, with the same key (tests added since only add kills). Missed, Timeout and Unviable are always run. Nothing is skipped.

Measured on two real nightly FULL runs (Rust only; `541ad60` -> `033b671`, 3138 mutants, graphs made locally with Rust 1.99.1):

- predicted carry 2628 of 3138 (83.7%), 85.1% of the mutant seconds; set against the real outcomes of the later run: 0 false carries.
  This is the weak half of the evidence: the two commits differ in tests and docs only, so a rule that carried everything would have
  scored the same.
- sensitivity control: one semantic edit in `checked()` (`access.rs`) drops the carry to 478 (15.2%): 10 mutants of the function itself
  (CODE_CHANGED), 4 of what depends on it, 2136 whose reaching tests changed. The rule is conservative on purpose: it cannot tell
  which of the reaching tests killed a mutant (cargo-mutants does not say), so all of them must hold.

The tool check is in (`env.json`: mutation tool, cargo, rustc must agree; missing or different carries nothing).

Not done: the data/environment digests of the planner (here test keys stand for them) and the comparison with a real run in CI.
**Go cannot be done from the existing artifacts:** the ledger of `mutation_shard_exec.sh` names a mutant by the digest of the whole mutated
file (`id<TAB>shard<TAB>shards<TAB>verdict`), so any edit of the file renames every mutant in it and the id carries no file or line. A Go
carry-over needs the stable mutant id of `docs/C-STABLE-MUTANT-ID-PLAN.md` (or a listing that records file and line per id) first; `go-mutesting`
is also not available in the local setup, so it was not tried. It is not a saving until a later real run confirms the carried verdicts.

## p1-proof: a pipeline next to p1 (step 1 of the build)

`.github/workflows/p1-proof.yml` is **generated** from `p1.yml` by `gen_p1_proof.py` (`--check` in the self-test of `proof-plan.yml`; do not edit it
by hand). p1 stays as it is and stays the required check. Step 1 changes only what two pipelines side by side need: the workflow name, a display
name `proof / <job>` for every job (the checks of a workflow are named by the job alone and the ruleset of `main` requires those names), no
`workflow_call`, and `inherit` asking about p1-proof.yml. The job graph (ids, `needs`, `if`) is p1's, and the test says so. The planner does not decide
anything in it yet: it runs exactly what p1 runs, which is the baseline the next steps are compared with.

Step 4: the `rust` and `go` jobs of p1-proof wait for `proof-plan`, fetch its plan and run their test step through `select_tests.py run`: the command of p1 minus
the REUSE_ELIGIBLE tests, but only when the plan is authorized (evidence and planner from main). On a pull request that is never so, and with no plan
at all (the planner failed) the command is p1's. Run locally with an authorized plan: Rust 233 skipped, the 20 others passed, doctests included; Go 266 skipped, rest passed.
Step 5: `proof-compare` waits for every other job and for the p1 run of the same commit, compares the conclusions job by job and the planner's FALSE_REUSE, and
fails on a divergence; when the comparison cannot be made it says NOT COMPARED (not a pass, not a failure).

The job runs the `select_tests.py` of the tree under test: that is acceptable while p1-proof is not a required check, and after the merge it is main's own code.

## Trust: what may ever authorize skipping

Report only is safe whoever writes the evidence. Skipping is not, and two things decide it:

- **The evidence.** Its trust is read from what GitHub says about the run that left it (`find_prev.py` writes `source.json`: branch, event), never from
  the artifact, which is input. Only a push to `main` is `main` evidence. A pull request's own earlier run is `branch` evidence: good for the
  report and for the iteration, not for skipping.
- **The planner.** A planner inside a pull request can make any evidence it likes. For a pull request the workflow takes the planner from the base
  commit (`git archive $BASE_SHA tools/proof-plan`); while the base has none (before this is merged) the one under test runs and the report says so.
  A push to `main` runs main's own code. A run on `main` is never cancelled by a newer push: its evidence is what the others are compared with.

The report states it on its second line: *Could authorize skipping: N tests* only when both are `main`; otherwise `0 tests` and the reason
(`pl.authority`). Unknown trust is the least trust, not the most. The cache is an accelerator (the qualification, which is checked against the
identities it was made for), not the store of record; the signed `ci-evidence` publication is still off.

## Two traps met while building it (both would have been silent)

- **The key must not depend on the process.** `merkle.py` hashed the internal edges of a cycle in the iteration order of a set, which changes with
  `PYTHONHASHSEED`: the same graph gave other keys in another process, and 230 of 253 Rust tests (about half of the Go tests) looked changed
  on an unchanged tree. Fixed (sorted), with a test that runs the keys under eight hash seeds (`test_merkle.py`).
- **The state of the crate matters to rust-analyzer.** It indexes a crate that has been built (build script outputs, proc-macro expansions)
  differently from one that has not. `plan.py` builds (`cargo test --no-run`) before indexing, so a restored or missing `target/` cannot
  move a key.

## Files

| File | Role |
|---|---|
| `plan.py` | evidence of the tree, qualification, previous evidence, the decisions (`plan.json`) |
| `record.py` | `observe-go`, `observe-rust` (the commands of `p1`), `finish`: comparison, report, `evidence.json` |
| `find_prev.py` | the nearest ancestor's artifact |
| `evidence.py` | the kernel: a pure function `evaluate_reuse(previous, current, assurance)` |
| `build_evidence.py`, `merkle.py` | evidence of one test from an extractor graph; Merkle keys |
| `qualify.py`, `qualify_rust.py`, `rust_graph.py`, `repro_rust_dispatch.py`, `testdata/` | probes that qualify an extractor build |
| `extractor/` | the Go extractors (their own modules) |
| `gen_p1_proof.py`, `test_gen_p1_proof.py` | generates and checks `p1-proof.yml` from `p1.yml` |
| `select_tests.py`, `test_select_tests.py` | the commands the decisions imply (`-skip`, `--exact --skip`), checked against the real test lists; report only |
| `compare_runs.py`, `test_compare_runs.py` | p1-proof against p1, job by job, for the same commit (the `proof-compare` job) |
| `mutplan.py` | mutation carry-over prediction (report only) |
| `test_plan.py`, `test_mutplan.py` | self-test of the pure parts |
