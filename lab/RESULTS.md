# Verification lab: results

Status: **measurement on the `lab/verification` branch. Nothing here is a gate of `main`, and no test or mutant result is skipped
or reused by any CI.** The numbers say how much *could* have been reused on the history of `main`, under rules that are checked
against what the tests actually did. They are not a measured CI speed-up: the cost of the analysis is listed (section 7), the
time saved is not measured.

## 1. The question

A test result (or a mutation verdict) of an earlier commit can stand for the current commit when nothing it depends on has
changed. That needs three things to be complete and correct:

1. **the code it depends on** (a static dependency graph, its closure hashed into a Merkle key);
2. **the data it reads** (files, recorded while the test runs);
3. **the environment** (lockfiles, toolchain and tool pins).

The lab builds each, tries to refute each against what the tests actually execute and read, and then replays the history of
`main` to count what the rules would have reused.

## 2. What was built

| Part | What it does | Where |
|---|---|---|
| Go graph | `go/packages` + `go/types`: a node per top-level declaration; an edge for every object an identifier resolves to (unexported ones, package-level types, constants, variables too), the implementers of a called interface method, and every method of a named type whose value becomes an interface value (`fmt` calling `String`, `errors.Is` calling `Unwrap`) | `lab/extractor/typeddeps` |
| Rust graph | rust-analyzer's SCIP index: an edge for every occurrence; a field or variant is an edge to its type; a type has an edge to every method of its impl blocks (`Display::fmt` through `format!`); a trait of the crate itself is a failure (dispatch through it is not modelled; the crate has none) | `lab/extractor/scipdeps` |
| The knowledge base's graph | `go.meta.gen.py` / `goastdump`, copied unchanged from CIC_Relay `86eaca2`, measured for comparison | `lab/extractor/relay` |
| Coverage check | every test alone with coverage (Go cover profile, LLVM for Rust): every function it ran must be in its closure | `lab/measure/graph_vs_coverage.py`, `collect_rust.py` |
| Merkle key | per node: its own source digest and its dependencies' keys; a cycle is one strongly connected component with one key | `lab/measure/merkle.py` |
| Data edges | every test alone under `strace -y`: the repository paths it opens, stats or lists; a read in a pinned upstream checkout is a dependency on the pin file | `lab/measure/trace_data.py` |
| Data check | a data file is corrupted and every test runs: a test that starts failing must have read the file | `lab/measure/data_soundness.py` |
| Backtests | the history of `main`, commit by commit: tests (`backtest.py`, `backtest_data.py`), mutants (`backtest_mutants.py`) | `lab/measure/` |
| Mutant samples | sampled mutants run on the parent and the child; the failing tests are the killers; carried verdicts are checked | `lab/measure/mutant_killers.py` |

The lab workflow (`.github/workflows/lab.yml`) runs the coverage checks, the Merkle tests, the data record and check, and the
planted defects on every push to `lab/**`. The backtests and the mutant samples take hours and run by hand.

## 3. The graphs against what the tests execute

On the tree of `8b0ccc1` (#131):

| | Go | Rust |
|---|---|---|
| tests | 264 | 244 |
| product functions | 279 | 295 |
| tests that ran a function outside their closure (**typed graph**) | **0** | **0** |
| the same for the knowledge base's graph (`go.meta.gen.py`) | 248 (261 distinct functions) | (no generator) |
| precision: share of the closure that runs, mean | 0.59 | 0.58 |
| tests to rerun when one product function changes, median (graph / coverage) | 13% / 6% | 16% / 6% |

The knowledge base's graph records only exported calls and only references through an imported package, so inside one package
(all of `go/`) it sees almost nothing. It serves navigation; it cannot decide reuse.

Both rules for indirect calls were found by this check, not designed ahead: without them, `ScalarType.String` (Go, 11 tests)
and `ReceiptError.Unwrap` (5 tests), and the `Display::fmt` of three Rust error types, ran outside the closure.

## 4. Tests over the history of `main`

113 commit pairs, 29096 test runs (Go and Rust), `lab/results/backtest-*-314475ab14e0.json`. **Read with section 9:** the graph
behind this number has a known gap (implicit `iota`), and 94% of the reusable Go runs reach a const it concerns.

| Rule | Reusable |
|---|---|
| same Merkle key and environment, **any** data change reruns every test | 55.4% (16110) |
| same Merkle key and environment, a test reruns only if a **file it read** changed (read on the parent) | **85.8%** (24973) |

* 45 of the 93 pairs with tests reuse everything (documentation, CI, decision records). Of the 38 that reused nothing under
  the first rule, 37 did so only because a data file changed.
* With data edges, typical product pull requests keep most tests: P4-1 (60 patch vectors) 316 of 344, P5-1 differential 453 of
  456, #130 (P5-3 schema identity, code changed widely) 293 of 487.
* Reads are recorded on the **parent**: a deterministic test whose closure and read files are unchanged opens the same files on
  the child, because what it opens is decided by its code and by what it read. A test that failed on the parent is never reused.

The data record checked by corruption (`lab/results/data-soundness-8b0ccc1.json`): 15 read files, 15 unread ones, the two
testdata files no test reads, `corpus/pins.yaml`, and a new file in six listed directories. No test that failed after a
corruption lacked the file in its record. 15 of the 36 corruptions made some test fail.

## 5. Mutation verdicts over the history of `main`

92 commit pairs, `lab/results/backtest-mutants-314475ab14e0.json`.

| | Go | Rust |
|---|---|---|
| mutant verdicts | 183219 | 97370 |
| **strict rule**: the same mutant, the same tests reach its declaration, all of them reusable, the same tool and flags | **56%** | **59%** |
| **kill rule**, upper bound: also a mutant that some reusable test of the parent reaches | 82% | 81% |
| strict rule, weighed by measured cost | — | 53% (the cost table covers 43% of the mutants) |

The kill rule carries the verdict of a mutant **killed** on the parent when one of the tests that killed it is reusable. Which
test killed a mutant is not in the history, so the backtest gives only its upper bound; the samples measure it:

| Sample | Run on parent and child | Carried | Held on the child |
|---|---|---|---|
| #131 (`704c41d` -> `8b0ccc1`), Go, kill rule | 30 | 20 | 20 |
| #131, Rust, strict rule | 30 | 30 | 30 |
| #131, Rust, kill rule | 30 | 27 | 27 |
| #132 (`8b0ccc1` -> `314475a`, tests only), mutants new tests reach, Go and Rust | 50 | (none carried) | — |

With the kill rule's share of the samples, an estimate of what could be carried is about 73% (Go) and 79% (Rust). It rests on
one commit pair.

## 6. Planted defects (each must be seen, and is)

| Defect | Seen as |
|---|---|
| 5% of the Go graph's edges dropped | 134 to 138 tests with a miss (the property tests draw new cases) |
| the Go interface conversion rule switched off | 16 tests miss `ScalarType.String`, `ReceiptError.Unwrap` |
| 5% of the Rust graph's edges dropped | 58 tests with a miss |
| the Rust type -> impl rule switched off | 3 tests miss `Display::fmt` |
| `corpus/patch/vectors.yaml` forgotten from every data record | 37 misses |
| a test with no node in the graph (synthetic, from the review) | `--expect sound` fails |
| `openat(fd, "name")` relative to a directory fd (synthetic, from the review) | resolved through `strace -y`; unresolvable is "opaque", never reused |

## 7. What this does not show

* **No general proof.** The checks refute; they do not prove. Coverage sees which functions ran, not which constants, types or
  data paths mattered; the corruption check sees a read only when the corruption makes a test fail; code no test executes has
  its edges unchecked.
* **The mutant samples have little power.** In 140 mutants run on both commits no verdict changed, so a wrong rule would have
  passed them too. A planted case is still to be built: a survivor and, in a trial commit, a new test that kills it.
* **Not a CI speed-up.** The plain tests take minutes; the CI time is in mutation and fuzzing, and fuzzing explores by chance and
  is not reused at all. The analysis has a cost per commit: Go graph about 2 s, Rust SCIP index about 15 s, the data record
  (every test alone under strace) about 10 s on 12 cores, Go mutant enumeration about 40 s; none of it was set against a
  saving.
* **Determinism is assumed.** The property tests (rapid, proptest) draw new cases on each run; reusing their result means
  accepting the earlier draw.
* **Environment is coarse.** Lockfiles, manifests and the pinned toolchain and tool versions of the p1 workflow; the runner
  image is not in the key.
* **Commit pinning.** The lab branch is based on `8b0ccc1`; the backtests read `main` up to `314475a`.

## 8. The external review (2026-11-01)

A review of the branch reproduced two defects; both are confirmed here and fixed with tests
(`lab/measure/test_review_findings.py`):

1. `graph_vs_coverage.py --expect sound` passed when a test had no node in the graph (its closure unknown). It now fails.
2. `trace_data.py` dropped a path opened relative to a directory fd (`openat(3, "config.yaml")`): the directory was recorded, the
   file was not. The tracer now runs `strace -y` and resolves the path through the fd; a path it cannot resolve becomes an
   "opaque" read, and a test with one is never reused. Re-recorded on `8b0ccc1`, the read sets of all 508 tests are identical to
   the earlier record and no read is opaque. The data backtest was rerun with the fixed tracer on all 38 parents it traces: the same 24973 reusable runs and 205 runs whose read files changed, and no opaque read anywhere in the history.

The review's conclusion stands: the lab shows how much reuse is possible and how to check it; it does not yet show that a
skip decision would be safe in general.

## 9. What the 85.8% rests on: exposure to implicit `iota` (2026-11-01)

Section 4's number was made with a graph that has no edge from a constant to the group it is declared in (`lab/testdata/iota`:
`A = iota + 1; B` becomes `iota + 2` and `B`, the key of every test using `B`, and the test result change differently). The extractor
now reports the constructs it meets (`lab/extractor/typeddeps`, schema v2) and `lab/measure/iota_exposure.py` re-reads the same 113
commit pairs with it. The reusable runs are recomputed exactly (24973, or the script stops); nothing in the Merkle algorithm or the
reuse rule is changed, and the iota defect is **not fixed** here.

Three states, side by side. Each says something different, and none replaces another:

| State | Reusable runs | Share of 29096 | What it is |
|---|---|---|---|
| **Original** (section 4) | 24973 | 85.8% | Potential reuse by a graph with a known gap. Not a statement that each of these results is valid. |
| **Conservative**: every run whose Go closure reaches an implicit-repeat or `iota` const is excluded | 12689 | 43.6% | What stands if evidence whose validity cannot be shown is not reused. A lower bound by construction, not an observed loss of correct decisions. |
| **With the const group rule** (`a1d189f`): the Go keys made again, all 113 pairs, the same rule and traces | **24973** | **85.8%** | **Measured.** The same runs, run by run (the sorted lists are identical). The rule closes the two mechanisms below and the exposed runs stay reusable, now with a qualified construct (section 9.1). History of this repository only. |

The numbers behind the second row, Go only (Rust has no `iota`; its 11900 reusable runs are counted as they were, and they are
still `UNVERIFIABLE` for the kernel because the Rust extractor reports no constructs):

| Measure | Value |
|---|---|
| reusable Go runs | 13073 |
| of these with an exposed const in the closure (**exposure**) | **12284 (94.0%)** |
| exposed because the closure could not be read | 0 |
| exposed const reached, over the Go tests of all pairs | 14344 of 15171 |
| **semantic change candidates** (an exposed const whose whole `const ( ... )` changed while the test key stayed equal) | **0** |
| **verified failures** (a candidate that passed on the parent and fails on the child) | **0** (none to run) |

### 9.1 The fix, measured

The const group rule (`a1d189f`): a constant that repeats the previous spec or uses `iota` depends on a node for its whole
`const ( ... )`, and an implicit repeat depends on what its inherited expression depends on. Two mechanisms, not one: the first
(position) is what the `candidate` count above looks for; the second (an inherited expression that reaches a constant that changed,
`lab/testdata/iota-inherit`) it cannot see, and is measured here by the new keys.

| | |
|---|---|
| Go test keys | 15171 |
| of these different under the new model (the closure now holds a group node) | 14344, the exposed tests |
| Go runs with an equal key on parent and child under the old keys | 13157 |
| of these with unequal keys under the new keys (**runs the fix would rerun**) | **0** |
| reusable runs, old keys / new keys | 24973 / **24973**, the same runs |

So, for this history: neither mechanism moved a value under a test that was called reusable, **and** the model that now keys them
is the one `qualify.py` has qualified on the three probe pairs (the old model fails all three). The 12284 exposed runs are not lost;
nothing was recovered either, because nothing was in the set the rule reruns. The value of the fix here is the second row's
bound turned into a statement the model can make, not a number: with the old model the 24973 had a gap that history happened not to hit.
The cost is on the side of reruns when a group does change (a constant added after the last use reruns every test that reaches
the group, although no value moved); it is zero in this history.

`lab/results/iota-fix-rekey-314475ab14e0.json`; reproduce with `backtest_rekey.py` then `iota_exposure.py --work DIR2`.
Keys of Rust are the earlier ones (the change is a Go one); the Rust extractor still reports no constructs.

Reading the exposure measure:

* **Exposure is wide because it is concentrated.** At the last commit with Go tests, 254 of 269 Go tests reach one of 46 consts,
  declared in **10 const groups**: `Kind*` (7), `rf*` (7), `Err*` (6), `Shape*` (6), `Rule*` (5), `Pos*` (4), `Mode*` (3), `Scalar*` (3),
  `Policy*` (3) and `Mandatory`/`Optional` (2). The groups are what a verified model (or an assurance for exactly those groups)
  would have to cover.
* **Zero candidates is a result about this history, not about the model.** The group of an exposed const changed in 4 pairs
  (#24, `650b4c1`, `bf7f901`, #101); in each, every test that reaches it also changed its key because other code in its closure
  changed in the same commit. The detector is tested on synthetic cases (`test_iota_exposure.py`, including the `lab/testdata/iota`
  shape); that it finds nothing here is not because it cannot see.
* **So no historical wrong decision is shown, and the model is still vulnerable.** The `lab/testdata/iota` pair is the proof of the
  second; the 0 of the first says only that main's history, commit by commit, did not contain such a pair for a reusable run.
  Both are true together, and the cost of not trusting the gap is the second row.
* **What this does not cover:** other constructs the extractor recognises and the assurance does not yet vouch for are not
  measured here (generics: 2 functions in the tree); constructs it does not recognise are not seen at all. The extraction was
  done with Go 1.26.9 and `x/tools` v0.44.0 on the cache of the earlier run (`cache.json`, `traces/` of the fixed tracer); the
  pinned CI toolchain gives the same keys in the iota case (`lab/results/iota-red-ci-run41306166178-*.json`) but this backtest was
  not repeated on it.

## 10. The kernel's reading of history (Go and Rust)

Section 4's rule says what an equal key, environment and read-file set allow. The verification kernel (`lab/measure/evidence.py`)
decides the same 113 pairs from evidence built out of the extractors' output (`kernel_backtest.py`): it adds the qualification of
the constructs in the closure, the completeness of the analysis, the identity of the extractor, and it refuses an opaque
dependency. Go with the const group rule and its measured qualification; Rust with `scipdeps` v2 and its qualification
(`qualify_rust.py`). The kernel never allowed what the older rule does not (the script stops otherwise), and never answered
`RERUN_REQUIRED` where the older rule reuses: it only withholds.

| Runs the older rule reuses | Go | Rust | Both |
|---|---|---|---|
| older rule (section 4) | 13073 | 11900 | **24973 (85.8% of 29096)** |
| kernel: `REUSE_ELIGIBLE` | 8268 (63.2%) | 10465 (87.9%) | **18733 (64.4% of 29096; 75.0% of the above)** |
| kernel: `UNVERIFIABLE` where the older rule reuses | 4805 | 1435 | 6240 |

**What the withheld runs cost is concentrated, and is a statement about the model, not about the code:**

* Go, 4700 runs: `CONSTRUCT_NOT_VERIFIED` only. The constructs are `go.func.generic` (two functions of the tree) and `go.type.alias`
  (one), reached by 5812 of the 15171 Go test-commit combinations. Nobody has shown the graph wrong on them; nobody has qualified
  it. A probe pair for each would put them in or leave them out.
* Go, 78 runs: `OPAQUE_DEPENDENCY` only, the tests whose closure reaches `reflect.ValueOf`/`Elem`/`Interface`/`IsNil` (the extractor
  says the deps of such a node may be incomplete); 27 more runs have both reasons (4700 + 78 + 27 = 4805).
* Rust, 1435 runs: all the `proptest!` tests (`tests/properties.rs`): `ANALYSIS_INCOMPLETE` (found by their text) and `rs.macro.generated`
  unqualified.

Where the kernel and the older rule differ in the other direction (the kernel's `RERUN_REQUIRED`/`UNVERIFIABLE` where the older
rule does not reuse) is not a loss: 867 + 1533 `RERUN_REQUIRED` and 963 + 243 `UNVERIFIABLE` runs the older rule also reruns.

What this does not say:

* `REUSE_ELIGIBLE` is not "safe in general". It means every condition the kernel can check holds, for constructs qualified by the
  probes of `qualify.py` / `qualify_rust.py` (a regression probe each, not a proof), plus the item kinds the reviewer claims.
* Two assumptions of the script: a test with no trace on the parent had a pass there (main was green), and a test that is new on the
  child is not counted.
* Not a CI saving. Nothing here was skipped, and the cost of building the evidence (a rust-analyzer index of about 8 s per commit, the
  Go graph about 2 s, the data record about 10 s) is not set against the runs it spares.
* The numbers belong to these extractor builds (`extractors` in the JSON, with the binary digests), Go 1.26.9 and Rust 1.99.1 as
  recorded in the report; the CI uses the pinned Go 1.27.2.

`lab/results/kernel-backtest-314475ab14e0.json`.

### 10.1 The first qualification round: aliases in, generics out

The 4700 Go runs withheld for an unqualified construct held two constructs. Probe pairs were written for each
(`qualify.py`, `CONSTRUCT_PROBES`; real `PASS -> FAIL` changes, the key of the test must move; negative controls with planted builds,
`test_qualify_go.py`). `lab/results/kernel-backtest-alias-qualified-314475ab14e0.json`.

| Go runs the older rule reuses (13073), by what the closure holds | Runs | Before | Now |
|---|---|---|---|
| neither generics nor aliases, no opaque | 8268 | eligible | eligible |
| **aliases only** | **628** | withheld | **eligible** (3 of 3 probes) |
| generic functions only | 2683 | withheld | withheld (1 of 2 probes fails) |
| generic functions and aliases | 1389 | withheld | withheld |
| generic functions and an opaque `reflect` use | 27 | withheld | withheld |
| an opaque `reflect` use, nothing else | 78 | withheld | withheld |

So the kernel now calls **19361** runs `REUSE_ELIGIBLE` (Go 8896, Rust 10465): **66.5%** of 29096, up from 18733 (64.4%). The
scenario "if all 4700 were qualified" (80.5%) is not reached, because 87% of those runs sit under the **generic functions**, and
those are not qualified: one of their two probes fails.

* **The failing probe is a real defect, not a missing proof.** `testdata/go-generic-fmt`: a generic function that formats its
  argument (`fmt.Sprint(x)`); the type argument's `String` changes, the test goes from pass to fail, the Merkle key does not move.
  The conversion rule skips a type parameter on purpose, and `types.IsInterface` is true for one, so the method set of the type
  argument is never reached. It is the second red case of this kind (after implicit `iota` and the Rust macro of the crate). It
  is **not fixed**; until it is, the kernel is right to refuse generics.
* **Aliases: what is claimed.** Three mechanisms: an alias retargeted, the method of the target of an unchanged alias changed, a
  value of the alias formatted by `fmt`. Not "all aliases"; the negative control (`-planted-no-conversions`) fails the third.
* **What is left in Go** is almost entirely generics (4072 + 27), and in Rust the `proptest!` tests (1435). The next number depends
  on a fix of the generic gap, not on more probes.

### 10.2 The dynamic dispatch gap closed

Eleven pairs of Go sources (`testdata/go-generic-*`, `go-conv-*`, `repro_dispatch.py`) showed that the graph did not follow a method
that `fmt` calls by dynamic dispatch: on a type argument (also a pointer one, a promoted one, one handed on to another generic
function, a slice of it, a generic type's field) and, without generics, on a promoted method, a slice element and a struct field. Nine
were red, the key of the test not moving although it went from pass to fail. One reachability function now serves the conversion rule
and every instantiation (`Info.Instances`): the whole method set (promoted and pointer methods too), then type arguments, fields and
elements. On `go/`: 225 edges added on 117 declarations, none removed. `lab/results/kernel-backtest-dispatch-fixed-314475ab14e0.json`.

| The history decided by the kernel | Go | Rust | Both |
|---|---|---|---|
| older rule, keys of the extractor before the fix | 13073 | 11900 | 24973 |
| first round, section 10 | 8268 | 10465 | 18733 (64.4%) |
| aliases qualified, section 10.1 | 8896 | 10465 | 19361 (66.5%) |
| **dispatch fixed, generics qualified** | **12907** | 10465 | **23372 (80.3% of 29096)** |

The scenario sketched before the measurement (80.5%) was an upper bound on what the 4072 generic runs could give; the measurement
gives 80.3%. What remains withheld is almost only the same two things as before: the `reflect` uses in Go (105 runs, `OPAQUE_DEPENDENCY`)
and the `proptest!` tests in Rust (1435).

**The fix changed the keys of 61 runs the older rule reused** (13073 -> 13012 under the corrected keys): 30 tests in each of two commits
(`2a96304`, `56e222d`) and one in `b5be6e7`. Run alone on the parent and on the child (`verify_moved.py`,
`lab/results/moved-by-the-dispatch-fix-314475ab14e0.json`): **59 pass on both, 2 are inconclusive** (`TestRealRegistrySchemas` needs the
upstream registry checkout, absent where this ran, and fails on both sides), **0 pass on the parent and fail on the child**. So:

* the gap was real in the model and **no proven wrong decision in the history of main**, again;
* those 61 reruns are the price of the over-approximation (0.5% of the Go runs), not of wrong reuse;
* the 8268 Go runs called eligible in section 10 that had no generic or alias in the closure were not safe by construction: 8227
  of them are eligible again after the fix, **41 are not** (their keys moved, they are among the 61, and the rest of the 61 lie under
  generics or aliases). Section 10's number was therefore an over-estimate of what the fixed model allows, which is the direction
  the fix was expected to move it.

What is claimed and what is not: the dispatch rule covers the mechanisms of the eleven cases; it does not follow what `fmt` reaches by
reflection beyond fields, elements and type arguments, nor the methods of a type of a package that is not loaded (the toolchain's and
go.sum's business, the environment key). `-planted-no-implementers` has no probe that fails any more: the instance rule makes the
implementers rule redundant for the probed cases, and the rule is held only by the coverage check of section 3. A type argument that
cannot be resolved marks the node opaque. `qualify.py` now treats the three conversion cases as REQUIRED probes: the old conversion rule
is qualified for nothing.

## 11. Reproducing

Toolchains: Go 1.27.2, Rust 1.99.1 with `llvm-tools` and `rust-analyzer`, `strace`, `cargo-mutants` 27.1.0 and the pinned
`go-mutesting` for the mutant parts; the `CIC_*_ROOT` checkouts as in CI.

```
lab/measure/run.sh OUT          # Go graph vs coverage, the knowledge base's graph, planted defects
lab/measure/run_rust.sh OUT     # the same for Rust (RUST_TOOLCHAIN)
lab/measure/run_data.sh OUT     # data record, corruption check, planted defect (corrupts and restores files in the checkout)
python3 lab/measure/backtest.py ...           then backtest_data.py, backtest_mutants.py, mutant_killers.py (see each --help)
python3 lab/measure/iota_exposure.py --repo . --work DIR --typeddeps BIN --rev 314475ab14e0 --out OUT   # needs DIR/cache.json and traces/
python3 lab/measure/kernel_backtest.py --repo . --work DIR --typeddeps BIN --scipdeps BIN --rust-toolchain 1.99.1 --rev 314475ab14e0 --out OUT   # about 15 minutes
```
