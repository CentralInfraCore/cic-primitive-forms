# ADR 0009: Branch coverage measurement contract (draft)

Status: **proposed, not approved.** Addresses ADR 0000 open point 10. It fixes *what
is measured, against which threshold, and what counts as a valid measurement* for
branch coverage in Rust and Go, and it provides the gate that enforces it. It does
**not** select the measurement tool per language: that needs sample evidence from P1,
so **point 10 of issue #3 stays open** until that evidence exists. Line coverage and
mutation score are separate requirements of `docs/PHASES.md`; they stay strictly above
90% and get their own reports in the same discipline later.

## Principle

A green run proves nothing by itself; what proves something is knowing exactly what
was measured and that breaking the contract turns the gate red. So the gate fails
closed, names every reason, and carries seeded faults for every reason.

## Decisions

1. **Threshold.** Branch coverage must be **strictly above 90%**, separately for the
   Rust and the Go implementation, over all of its own source, not a critical subset.
   Exactly 90.00% fails. The comparison is exact integer arithmetic
   (`covered * 100 > total * 90`), never a rounded percentage.
2. **Branch.** A branch is a possible outgoing edge of a control-flow decision
   (definition `cic-branch/1`). Five categories, all counted:

   | Category | Edges counted |
   |---|---|
   | `conditional` | both outcomes of every `if` / `else if`, including the implicit else |
   | `match` | each arm of a Rust `match` and each guard outcome (an exhaustive `match` has no implicit edge); each `case` of a Go `switch` or type switch and, when there is no `default`, the implicit no-match edge; each communication `case` of a Go `select` and its `default` when present (a `select` without `default` blocks until a case is ready, so it has no implicit edge) |
   | `loop` | for every loop whose body is guarded by a test, the two outcomes of that test: iterate and exit. This covers `while`, `while let`, Rust `for`, Go `for cond`, three-clause `for` and `range` (an empty range exits at the first test). An unconditional loop, Rust `loop {}` and Go `for {}` or a constant-true test, always enters and has no such decision |
   | `short_circuit` | both outcomes of every operand of `&&`, `\|\|` |
   | `error_path` | both edges of Rust's `?` (continue, propagate); Go's `if err != nil` is a `conditional` |

   `if let` is a `conditional` (matched or not). Not branches by themselves: an
   unconditional loop's entry, and `break`, `continue`, `return`, `goto`, `panic`
   and other jumps; they are real control-flow edges, but the decision that leads to
   them is already counted where it is made (a `break` inside an `if` is covered by
   that `if`'s two outcomes). A `break` is therefore never a stand-in for an entry
   test an unconditional loop does not have.

   A tool that does not measure a category must say so in the report
   (`unmeasured_categories`). That is a deviation, and a deviation is accepted only
   when the tool entry in `coverage/tools.yaml` lists it under `approved_deviations`,
   by a recorded decision of the owner. Otherwise the gate fails
   (`CATEGORY_UNMEASURED`).
3. **Same requirement, not same count.** The two implementations have different
   structure and need not have the same number of branches. What is identical is the
   definition, the threshold and the conformance rule above, applied to each language
   on its own numbers.
4. **Valid measurement.** A measurement is valid only as a **normalized report**
   (`cic-coverage-report/1`, JSON, no floats) that is reproducible, machine-checked and
   traceable: tool name and version, the commit measured, every own source file with
   its SHA-256, and total and covered branches per file and per category. The gate
   recomputes all sums and trusts no total. A missing, unreadable, empty, stale or
   inconsistent report is never green.
5. **A gate that tests itself.** `make coverage` runs the gate against fixtures with
   34 seeded faults, one or more for each of its 15 failure reasons, among them the
   exact-90% report, an empty, partial, unknown or repeated language set, a report that is missing, a disabled (empty) measurement, a
   report for another commit and a source file changed after measuring. The self-test
   fails if any reason has no fault that turns the gate red. A mutant of the gate that
   accepts exactly 90% was checked to turn the self-test red.

## Decision on the measurement approach (owner, 2026-10-18)

Recorded after the tool qualification spike (PR #18, `spike/coverage-qualification`). The ADR stays
`proposed`, and `coverage/tools.yaml` keeps `selected: null` for both languages.

### What the spike showed

Against samples whose branches were counted by hand (27 edges in Rust, 34 in Go), neither candidate
measures `cic-branch/1`, and neither reports categories:

- Rust, `cargo-llvm-cov` with `-Zcoverage-options=condition`: 20 of 27 edges. Not reported as branches: the
  `match` arms, the `for` edges and both edges of `?` (they exist only as plain code regions). The `--branch` flag
  also omits the second operand of `&&` / `||`; `--mcdc` fails on the current nightly (rustc renamed the option
  value); 0.9.1 has no `--fail-under-branches`.
- Go, `gobco`: 30 outcomes against 34 edges, which is not a subset: `select` (4) and `range` (2) are missing and a
  `switch` is counted as per-case conditions (2 extra outcomes). It also ignores `//go:build` constraints.
- Go, `gocove` (beta, `v0.0.0-20260427173940-a0dceb9dadca`): 7 conditions on the same sample (no value or type
  switch cases, no `&&` / `||` outside an `if`, no `select`, no `range`), and on Go 1.27.2 it produced no catalogue
  at all while the tests still passed: a silent no-op. It worked on Go 1.23.12.

### Decision: A

`cic-branch/1` is kept in full. The measurement capability that the tools lack is provided by this project, as an
adapter and, where a tool cannot supply the evidence, an instrumenter:

- **Rust:** `cargo-llvm-cov` stays the source of execution evidence; a source classifier enumerates every edge of
  `cic-branch/1` and assigns the evidence (branch and code regions) to it. That a `match` arm that is visible as a
  region maps to an unambiguous edge coverage in every compound case is *not* assumed; it is shown by golden tests.
- **Go:** an AST-based enumerator and instrumenter that implements `cic-branch/1` directly. `gobco` and `gocove` do
  not qualify; they remain evidence in the spike.

Alternatives **B** (redefine the metric as what a pinned tool reports, with `approved_deviations`) and **C**
(Rust under A, Go under B) are rejected for the P0 acceptance contract. Documenting the Go deviations would be
possible, but the Go tool does not measure a subset of the Rust metric: it has missing edges, differently counted
edges (`switch`) and a wrong file selection (build tags) at the same time, so a high percentage would not be
comparable with the Rust result or with `cic-branch/1`. The aim is not that the gate prints a number above 90%, but
that we can show which branching possibilities it counted.

### Qualification requirements for the measurement solution (per language)

1. It **enumerates every edge** of `cic-branch/1` from the source, each with an identity, a location and one of the
   five categories; the denominator comes from this enumeration, not from the tool's own counts.
2. It attaches **execution evidence** to each edge and reports covered / total per file and per category in the
   existing `cic-coverage-report/1`.
3. On the qualification samples, numerator and denominator **equal the hand-counted golden tables, category by
   category**, for the full and the reduced test set.
4. An edge or construct it cannot reconstruct, a file of the scope that is not part of the measured build (build
   constraints, platform files), a package it cannot instrument, or evidence it cannot attach: the pipeline **does
   not produce a successful report** (fail closed). Silent partial results are a defect.
5. The normalized reports are fed to the real `tools/coverage_gate.py check`, which must turn red on the reduced
   set by the strict threshold, and which still rejects a missing report, a disabled measurement and an unselected
   tool on the real workflow.
6. It is deterministic (byte-identical reports on repeated runs), and every tool and compiler is pinned as a pair
   and re-qualified together (the Rust option rename is the example).
7. The enumerator, instrumenter and adapters are part of the trust base of the gate and are tested like code,
   including nested and compound cases, not only the flat samples.
8. `coverage/tools.yaml` is changed only after the solution is qualified and the owner approves it.

## Scope

`coverage/scope.yaml` names, per language, the own source by `include` globs minus
`exclude_test` and `exclude_generated` globs. That is the only way to leave anything
out. A report must list exactly those files (`SCOPE_INCOMPLETE`, `SCOPE_EXTRA`) with
the digests of the files under test (`SOURCE_DIGEST`). There is **no per-branch
exclusion**: a branch that cannot be reached stays counted and uncovered.

## The gate

`tools/coverage_gate.py check` takes the scope, the tool pins, the commit under test
and one report for each of the two languages, and prints per language `covered/total = x.xx%`
and a final `BRANCH COVERAGE: PASS` or `FAIL`. Failure reasons: `REPORT_MISSING`,
`REPORT_FORMAT`, `LANGUAGE_SET_INVALID`, `LANGUAGE_NOT_REPORTED`, `MEASUREMENT_EMPTY`, `TOOL_NOT_SELECTED`,
`TOOL_MISMATCH`, `DEFINITION_MISMATCH`, `COMMIT_MISMATCH`, `SCOPE_INCOMPLETE`,
`SCOPE_EXTRA`, `SOURCE_DIGEST`, `COUNT_INCONSISTENT`, `CATEGORY_UNMEASURED`,
`BELOW_THRESHOLD`. A conformance result needs **exactly** Rust and Go: an empty, one-language, unknown or
repeated set is `LANGUAGE_SET_INVALID`, never a pass. A per-language diagnostic run
exists (`--only LANG`) and can only end with exit status 3 and the line
`NOT a conformance result`, whatever it finds. With the real `coverage/tools.yaml` (no tool selected) it is red
(`TOOL_NOT_SELECTED`): the quality gate is not satisfied, and no document can say
otherwise. Today CI runs only the self-test, because no Rust or Go code exists; the
real `check` becomes a required CI step with the first implementation code (P1).

## Tool selection (open)

`coverage/tools.yaml` lists candidates as leads, none verified here:

- Rust: `cargo-llvm-cov` has a branch mode; its README documents it as optional and
  nightly-only. Open: stability of the counts, and whether `match` arms, loops,
  short-circuit operands and `?` are branches in its report.
- Go: the standard `go test -cover` is statement coverage only (the Go issue
  [golang/go#28888](https://golang.org/issue/28888) asks for branch coverage).
  `gobco` is a third-party tool that reports whether each condition was true and
  false, which is condition coverage, narrower than the definition above. It is
  described as syntactic and as not covering `select`. **No known Go candidate
  covers all five categories**; this is a finding, not a verdict, and it must be
  settled on a real sample.

Sources read: [cargo-llvm-cov](https://github.com/taiki-e/cargo-llvm-cov), [gobco](https://github.com/rillig/gobco) (via search results; nothing was run).

### Requirements for a candidate

A tool may be selected for a language only if, on the P1 sample, it (a) measures all
five categories, or the missing ones are covered by an approved deviation; (b) is
available at a pinned version in CI; (c) gives deterministic counts for the same
source and tests; (d) yields output an adapter can turn into `cic-coverage-report/1`
without guessing; (e) can be made to fail the gate by a seeded fault.

### Closing point 10

Point 10 closes only when, for each language, there is recorded evidence: a sample
with every branch category and known expected counts, the adapter's output equal to
those counts (a golden test), a seeded fault that takes the sample below the
threshold and turns CI red, and the selected tool and version in `coverage/tools.yaml`.
Adapters are part of the trust base of the gate and are tested like code.

### No automatic fallback

If branch coverage cannot be measured comparably in both languages there is **no
automatic fallback** to statement or line coverage or to mutation score. That needs an
explicit amendment of this ADR and a separate decision of the owner; until then the
gate stays unsatisfied. Replacing the metric lowers the gate.

## Not decided here

- The tool per language, and the adapters (P1).
- Whether branches in Rust in-file test modules (`#[cfg(test)]`) are own source; it
  depends on what the chosen tool can exclude and is fixed on the sample.
- Line coverage and mutation score reports, and mutation equivalents.
- Coverage of generated code beyond the manifest globs.
- Branch counts across inlined or monomorphized code beyond what a tool reports.
