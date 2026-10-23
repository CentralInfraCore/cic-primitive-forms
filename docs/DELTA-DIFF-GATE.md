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
| a removed or modified line of an **existing** test | **FULL** on that PR |
| a modified existing shared helper, fixture or `testdata` line | **FULL** on that PR |

The classification is made from the diff by a script (a hunk with no removed line whose added lines are complete new `func Test...` /
`#[test] fn` items, or a new file, is an addition; a line added inside an existing function is a modification). When the classifier is
unsure, it says FULL. Narrowing the FULL case by per-test coverage (only the mutants on lines the changed test executes) is a later
optimisation, and only if the FULL case turns out to be frequent.

## 3. How each language does it

* **Rust:** `cargo mutants --in-diff <patch>` (cargo-mutants 27.1 has it) runs only the mutants in code the patch touches. The existing
  shard tooling and plan stay.
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
