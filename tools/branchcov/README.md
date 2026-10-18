# cic-branch/1 measurement tools (ADR 0009, decision A)

The candidate tools did not measure `cic-branch/1` (see `spike/coverage-qualification/EXIT-REPORT.md`), so the
measurement is provided here. **Nothing in this directory is selected for the product**: `coverage/tools.yaml` stays
`selected: null` until the pipeline is qualified and the owner approves it.

| Part | What it does |
|---|---|
| `go/` `gobranch` | Enumerates every edge of the five categories from the Go AST, instruments the own (non-test) files through `go test -overlay`, runs the tests, reports per edge how often it was taken. |
| `rust/` `rustbranch` | The same for a Rust crate with `syn`: instruments a throwaway copy, runs `cargo test`, counters flushed at process exit. |
| `adapter.py` | Adds the edges up per file and category into a `cic-coverage-report/1`; digests come from the repository; refuses unless the measured files are exactly the scope. |
| `qualify.py` | The qualification run: four hand-counted samples, full and reduced test sets, golden tables, the real `coverage_gate.py check`, determinism, optional LLVM cross-check. |
| `crosscheck_llvm.py` | Compares the Rust counters with LLVM's branch regions where LLVM has them (independent evidence only). |
| `test_failclosed*.py` | Cases the tools must refuse. |

## Edges (cic-branch/1)

The denominator is the enumeration, not a tool count. Go: `if` (true/false), `for cond` (iterate/exit by the test), `range`
(iterate / exit by exhaustion; a `break` is not an exit), each `case` clause and the implicit no-match edge of a
`switch` or type switch without `default`, each `select` clause, both outcomes of every `&&` / `||` operand.
Rust: `if` and `while` conditions, `if let` (matched / not matched), `while let` and `for` (iterate / exit), each
`match` arm and each guard outcome, `?` (continue / propagate), both outcomes of every `&&` / `||` operand.
`loop {}` and `for {}` have no entry decision.

## What is refused (fail closed)

Go: `goto`, a `fallthrough` into the implicit no-match edge, an existing `TestMain`, cgo, `go:embed` /
`go:linkname`, a module older than Go 1.18, a failing test run, a file of the scope that the build did not include
(build constraints) or that nobody measured, a file that changed after the measurement.
Rust: `let` chains, `let ... else`, `cfg` / `cfg_attr` (other than test code), a decision inside a macro invocation
or definition, a decision in a `const fn` or a `const` / `static` initializer, build scripts, workspaces, a failing
test run, a file that changed after the measurement. Test code (`#[cfg(test)]`, `#[test]`) is not own source and is
dropped from the measured copy.

## Known limits, stated

Cross-package credit: Go credits a package only with its own tests (unit coverage). The Rust runtime needs an
`unsafe` `atexit` call, so the measured copy relaxes `forbid(unsafe_code)` to `deny` and allows it in the generated
module only. The instrumented copy is a different program from the one shipped, with the same decisions; the
fixtures and the LLVM cross-check are the evidence that the counters are right. Code that panics or aborts in
a way the test harness does not survive loses its counters.
