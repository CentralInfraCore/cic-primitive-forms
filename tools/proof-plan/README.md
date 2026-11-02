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
| `test_plan.py` | self-test of the pure parts |
