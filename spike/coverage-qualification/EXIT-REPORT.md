# Exit report: branch coverage tool qualification

Date 2026-10-18. Scope: the isolated spike of ADR 0009 ("Closing point 10"). The tools were installed and run
locally in a throwaway directory and again on GitHub (see "Reproducibility"). Nothing in `coverage/tools.yaml`
was changed: **no tool is selected**, and selecting one needs the owner's decision (below).

## Tools and versions

| Language | Tool | Version | Notes |
|---|---|---|---|
| Rust | `cargo-llvm-cov` | 0.9.1 | `cargo install --locked` |
| Rust | toolchain | `nightly-2026-10-17` (rustc 1.101.0-nightly, `8006091eb`) with `llvm-tools-preview` | branch/condition coverage is unstable and needs nightly |
| Go | Go | 1.27.2 | |
| Go | `gobco` | 1.3.4 | `go install github.com/rillig/gobco@v1.3.4` |
| Go | `go test -cover` | (Go 1.27.2) | statement coverage; `-covermode` is `set`, `count` or `atomic`: no branch mode |

## Method

Two small samples (27 and 34 hand-counted edges under `cic-branch/1`) cover the five categories. Each runs
with a **full** test set (every edge covered) and a **reduced** one (20/27 and 24/34 covered, counted by hand,
below the threshold). The tool outputs are compared with the hand counts function by function
(`evidence/comparison.md`).

## Results

Hand counts against what each tool reports (outcomes = true/false outcomes of conditions):

| | Hand edges | Tool outcomes | Edges the tool does not report |
|---|---|---|---|
| Rust (`-Zcoverage-options=condition`) | 27 | 20 | **7**: 3 `match` arms, 2 `for` edges, 2 `?` edges |
| Go (`gobco`, default mode) | 34 | 30 | **4** hand edges not reported (2 `range`, 4 `select`, partly offset by `gobco` counting a switch differently) |

Per category:

| Category | Rust hand / tool | Go hand / tool |
|---|---|---|
| conditional | 6 / 6 | 8 / 8 |
| match | 5 / 2 (guards only, no arms) | 12 / 10 (switch counted as per-case conditions; **no `select`**) |
| loop | 6 / 4 (`while`, `while let`; **no `for`**) | 6 / 4 (`for cond`, three-clause `for`; **no `range`**) |
| short_circuit | 8 / 8 | 8 / 8 |
| error_path | 2 / 0 (**`?` not reported**) | 0 / 0 (`if err != nil` is a conditional) |

With the reduced tests the tools report 16/20 (Rust) and 22/30 (Go) outcomes covered, against 20/27 and
24/34 by hand; statement coverage of the Go sample is 100% (full) and 83.3% (reduced).

## Findings

1. **Neither tool reports categories.** `cargo-llvm-cov` reports branch regions with a position and two counts;
   `gobco` reports conditions with a position, a text and two counts. An adapter cannot fill the five
   categories of `cic-coverage-report/1` from that without classifying the source (a Rust and a Go syntax
   analysis in the trust base of the gate), or attributing the constructs by heuristics.
2. **Rust: seven hand edges are invisible as branches** (match arms, `for`, `?`). They are present as plain
   code regions with counts (an unexecuted arm, loop body or `?` error arm has count 0 in the JSON), so a source
   classifier could recover them; the tool does not label them.
3. **Rust: the flag matters.** `--branch` (the documented flag) reports 16 outcomes and omits the operands of
   `&&` and `||` beyond the first; `RUSTFLAGS="-Zcoverage-options=condition"` reports 20 and includes them.
   `--branch` and `--mcdc` exclude each other, and **`--mcdc` fails on this nightly** because rustc renamed the
   option value (`block | branch | condition`) and `cargo-llvm-cov` 0.9.1 still passes `mcdc`: tool and compiler
   must be pinned as a pair and re-qualified together.
4. **Rust: `cargo-llvm-cov` 0.9.1 has no `--fail-under-branches`** (only functions, lines, file-lines, regions).
   A claim to the contrary in third-party pages read earlier is false for this version; branch thresholds need
   our own gate.
5. **Go: `gobco` ignores build constraints.** It type-checks every file of the package and failed on a sample
   split by `//go:build` tags. Code with platform or tag files cannot be measured as it is.
6. **Go: `gobco` does not instrument `select`, `range` or `for {}`**, and counts a `switch` as one condition
   per case, so `case n == 1` false is not the implicit no-match edge of `cic-branch/1` (4 outcomes against 3
   hand edges). Its `-branch` mode drops the operands of `&&` and `||` (22 against 30 outcomes).
7. **No known Go candidate covers all five categories** (confirmed by the run, not only by the earlier search).
8. **Determinism holds.** Two runs of each tool give byte-identical outputs, and the CI run equals the committed
   evidence (below).
9. **Thresholding works at tool level.** On the tools' own numbers the full sample passes a strict 90% and the
   reduced one fails (Rust 16/20, Go 22/30), in a real CI step that must fail.

## Status of the nine acceptance criteria of ADR 0009 / review section G

| # | Criterion | Status |
|---|---|---|
| 1 | Hand-counted golden sample per language | **Met** (27 and 34 edges, `golden.json`) |
| 2 | Tool pinned, reproducible install in CI | **Met** (versions above; the workflow reproduces the outputs) |
| 3 | Adapter to `cic-coverage-report/1`, golden equality per category | **Not met**: findings 1, 2, 6, 7 |
| 4 | Determinism | **Met** (finding 8) |
| 5 | A real fault below the threshold turns a real CI run red | **Met at tool level** (finding 9); **not** through the ADR 0009 gate, which needs the adapter |
| 6 | The gate rejects missing report, disabled measurement, unselected tool on the real workflow | **Not done**: depends on 3 |
| 7 | Unmeasured categories listed | **Met** (tables above) |
| 8 | A language that cannot meet the contract is reported with a request for a decision | **This section** |
| 9 | Exit report | **This document** |

## What this changes for ADR 0009 (nothing is changed yet)

The contract asks for per-category counts and for every category measured. The tools give neither. Something
has to give: the measurement, the category structure of the report, or the definition of the branch.

## Decision requested from the owner (no automatic fallback)

- **A. Keep `cic-branch/1` as written; close the gap with our own measurement.** Rust: an adapter with a source
  classifier (a pinned syntax-analysis crate) that uses the region data for the seven invisible edges. Go: a
  custom instrumenter for `select`, `range` and the switch edges (extending the `gobco` approach), which makes
  a measurement tool part of this project. Strongest, most costly; the classifier and the instrumenter become
  code that must itself be qualified.
- **B. Redefine the metric as "the outcomes the pinned tool reports", with approved deviations.** The contract
  already allows `approved_deviations`; the report would drop the five-category structure (or make it a
  per-file measured subset). Unmeasured constructs (Rust: match arms, `for`, `?`; Go: `select`, `range`) would be
  covered only by the separate line-coverage and mutation-score gates and review. This **lowers** the branch gate and needs your explicit,
  recorded decision.
- **C. Mixed.** Rust under A (the data exists), Go under B with approved deviations for `select` and `range`
  until a measurement exists.

Recommendation, as a lean and not a decision: C, because the Rust region data makes the full definition
reachable with a classifier, while for Go the only way to the full definition is writing a measurement tool,
which should be a conscious investment and not a side effect. Under any option the tool and compiler pins
(finding 3) and the `gobco` build-tag limit (finding 5) must be recorded.

## What remains unproven

The adapter and the classifier (nothing built); the gate on real tool output; behaviour on larger code (the
samples are tiny); `#[cfg(test)]` modules (the sample moved its tests to `tests/`); other nightly dates; the
effect of inlining, generics and macros on the Rust branch regions.

## Reproducibility

`run_tools.sh` produces `evidence/*.json` byte-identically locally and on GitHub; the workflow
`.github/workflows/coverage-spike.yml` pins the toolchain and tools and checks it.
