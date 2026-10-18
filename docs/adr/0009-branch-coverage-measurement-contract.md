# ADR 0009: Branch coverage measurement contract

Status: **accepted** (2026-10-18, see "Acceptance record" at the end). Addresses ADR 0000 open point 10. It fixes
*what is measured, against which threshold, and what counts as a valid measurement* for branch coverage in Rust and
Go, provides the gate that enforces it, and records the selected measurement tools (`gobranch`, `rustbranch`). Line
coverage and mutation score are separate requirements of `docs/PHASES.md`; they stay strictly above 90% and get
their own reports in the same discipline later.

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

Recorded after the tool qualification spike (PR #18, `spike/coverage-qualification`). At that point the ADR was
proposed and no tool was selected; the selection and the acceptance followed once the pipeline was qualified (see
"Tool selection (decided)" and the acceptance record).

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

- **Rust:** the primary measurement is our own: a `syn`-based enumerator and instrumenter (`rustbranch`) that counts
  every edge of `cic-branch/1` in a throwaway copy of the crate. `cargo-llvm-cov` is kept as an **independent
  cross-check** for the edges LLVM reports as branch regions. The first version of this decision kept `cargo-llvm-cov`
  as the evidence base with a source classifier on top; the owner approved the change after the compound-sample
  examination showed that the region data does not map unambiguously to the edges (a `match` arm region starts at the
  first expression of the arm body, a `for` loop has no exit region, an empty arm may have no region).
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
   including nested and compound cases, not only the flat samples. A defect found in review (a Go switch clause
   entered by `fallthrough` was counted as selected) is the example: an edge is the *selection* of a branch, not the
   execution of its body.
   An instrumented copy is a different program from the shipped one, with the same decisions; the Rust copy
   even relaxes `forbid(unsafe_code)` to `deny` for the generated counter module. This is a stated qualification risk,
   covered by the golden samples and the cross-check, and must never be described as the shipped program being measured
   byte for byte.
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
`NOT a conformance result`, whatever it finds. Today CI runs the self-test and the qualification run on the samples;
no product code exists to measure, and the real `check` on product code becomes a required CI step with the first
implementation (P1).

## Tool selection (decided)

Selected, with no `approved_deviations`, in `coverage/tools.yaml`:

| Language | Tool | Version | Pinned with |
|---|---|---|---|
| Rust | `rustbranch` | `0.1.0` | `rustc 1.101.0-nightly (5d8fec066 2026-10-16)` (the dated nightly `nightly-2026-10-17`), source digest in `tools.yaml` |
| Go | `gobranch` | `0.1.0` | `go1.27.2`, source digest in `tools.yaml` |

`cargo-llvm-cov` 0.9.1 stays as an independent cross-check of the Rust counters (not as a measurement). The version
`0.1.0` of our own tools is meaningful only together with the repository commit, the toolchain and the source
digest: `tools/branchcov/qualify.py` recomputes the toolchain and the `sha256sum`-manifest digest of the sources,
tests and lock file of each tool and of the adapter, and fails if they differ from `tools.yaml`; the `branchcov` workflow
runs it whenever a tool, the adapter, the gate or `tools.yaml` changes. A rebuilt "0.1.0" with other sources or another
compiler is not the qualified tool, and a changed tool must be re-qualified and re-pinned in the same change.

Assessed and not selected (`spike/coverage-qualification/EXIT-REPORT.md`): `cargo-llvm-cov` (20 of 27 edges, categories
unavailable), `gobco` (30 outcomes for 34 edges, not a subset; no `select` or `range`; ignores build constraints), `gocove`
(7 conditions on the sample, a silent no-op on Go 1.27.2), `go test -cover` (statements only).

### The qualification run

`tools/branchcov/qualify.py` runs on the real `coverage/tools.yaml`. On the four hand-counted samples it measures with the
selected tools, compares with the golden tables, feeds the normalized reports to the real `coverage_gate.py check` (the
full test sets pass, the reduced ones fail with `BELOW_THRESHOLD` in both languages), proves that a wrong `selected`
version is a `TOOL_MISMATCH`, checks that a second measurement is byte-identical and, for Rust, compares the counters with
LLVM's branch regions. Fail-closed cases are tested separately.

### Where the proof stops

The contract and the measurement method are accepted. That the future Rust and Go **product code** exceeds 90% is not
proven and is not claimed: the product code does not exist yet. `docs/PHASES.md` makes the quality gates of the product
code mandatory in CI from P1; the real `coverage_gate.py check` becomes a required CI step together with the first Rust
and Go implementation, and a missing measurement must then fail. It is deliberately not simulated with empty
implementation directories.

### No automatic fallback

If branch coverage cannot be measured comparably in both languages there is **no automatic fallback** to statement or
line coverage or to mutation score. That needs an explicit amendment of this ADR and a separate decision of the owner.
Replacing the metric lowers the gate.

## Not decided here

- Whether Rust `#[cfg(test)]` modules or other test code count as own source: they do not (they are dropped from the
  measured copy), which is the rule of the tools; a different rule would be an amendment.
- Line coverage and mutation score reports, and mutation equivalents.
- Coverage of generated code beyond the manifest globs.
- The wiring of the required CI step on product code (P1, with the first implementation).
- Macros and conditional compilation that hide decisions are refused, not measured; supporting them is future work.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision, after the tool qualification (PR #18), the decision A record (PR #19) and
the measurement pipeline (PR #20). *Accepted* means the measurement contract and the selected measurement method are
the normative decision of the P0 scope; it is **not** evidence that product code exists or exceeds 90%.

| Requirement | State |
|---|---|
| Five categories and the exact edge definition | proven on the samples |
| Go and Rust golden equality, per function and category | proven (34, 49, 27 and 69 edges, full and reduced) |
| Normalized report | proven |
| Real gate, negative test | proven in the qualification (full passes, reduced fails, wrong version is `TOOL_MISMATCH`) |
| Determinism | proven on the samples |
| Refusal of unsupported or faulty constructs | tested, within the documented scope |
| The measurement tools as a tested trust base | supported by the qualification tests and the source-digest pin |
| Tool selection | decided and recorded in `coverage/tools.yaml` |
| Full coverage of real product code | P1, not yet proven |
| Required product CI gate and a result above 90% | P1, not yet active |

Stated risks that stay in force: an instrumented copy is a different program from the shipped one with the same
decisions (the Rust copy relaxes `forbid(unsafe_code)` to `deny` for the generated counter module); the golden tables
and the cross-check cover the samples, not all of Rust and Go; a counting defect was found in review (a Go switch
clause entered by `fallthrough`), which shows that the trust base needs the tests it has and more as it meets real code.
