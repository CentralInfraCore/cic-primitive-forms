# C0: can a Go mutant have a stable identity? A feasibility and cost study

Status: **study, nothing in CI changes.** Step C0 of `docs/FULL-DELTA-PLAN.md` (section 5.4 and the plan table). The question the
plan asks: *can a `go-mutesting` mutant get an id that survives unrelated edits, at a cost that does not eat the saving, and what
must a reused verdict additionally be bound to so that reuse is sound?* If not, Go mutation stays on FULL and DELTA covers Rust only.

Code and scripts: `tools/c0/` (`mutid`, a stdlib-only Go prototype with its own module so it is outside the gates of `go/`;
`enumerate.sh`, `record_exec.sh`, `edits.py`, `cmp.py`, `impact.py`). Measured on `main` at `8f19f4d`, `go/` (4898 lines, 7
non-test files), with the `go-mutesting` version pinned in `p1.yml` (`v0.0.0-20261002081621-9ce5f731e321`), Go 1.27.2, one machine.

## Verdict

**A stable id for Go mutants is feasible, cheap, and unique and stable on every experiment below. But the id alone cannot carry a
verdict from one commit to another: reuse is only sound if the id is bound to more than the mutated function.** What the plan's
`{ file, function, operator, ordinal, body digest }` (5.4) gives is *identity* and *"this function is unchanged"*. A mutant's verdict
is a function of the mutated code, of **all code reachable from it**, of the **tests and test data**, and of the toolchain. The body
digest covers the first only.

So the answer to the plan's decision point is: **Go mutation can leave FULL only together with a reach rule (steps D and E), not
with the id alone.** With the conservative reach rule measured here (call closure, section 5) a typical edit still leaves most mutants
reusable; with the plan's minimal rule (own body) the saving would be larger and *unsound*. Whether the saving is worth the machinery
is decision point 4 of the plan, and this study gives it numbers, not an answer to "worth".

## 1. The candidate id

`mutid ids` derives it from the original file and the mutated file alone, without line numbers and without a test:

1. tokenize both with `go/scanner` (comments are not tokens, whitespace does not exist);
2. the change is what is left after removing the longest common prefix and suffix of the two token streams: `from` (tokens of the
   original) and `to` (tokens of the mutant); either may be empty (a statement removed, a statement inserted);
3. the **enclosing unit** is the top-level function or method (`Recv.Name`) whose text contains the first differing token, or the
   package-level declaration (`<var x>`, `<const y>`, `<type T>`);
4. the **ordinal** counts the mutants of the same `(file, unit, from, to)` in the order of their position;
5. `id = sha256(file | unit | from | to | ordinal)[:16]`;
6. the **body digest** is `sha256` of the token stream of the unit.

The id says which mutant it is; the body digest says whether the function it lives in has changed.

## 2. Measurements

### Enumeration, uniqueness, cost (baseline)

| | |
|---|---|
| mutants enumerated | **824** (`go-mutesting` reports 33 more as duplicates of a mutant it already has: it dedupes by the digest of the mutated file) |
| by file | `parse.go` 475, `access.go` 116, `emit.go` 82, `recognize.go` 68, `validate.go` 59, `node.go` 17, `errors.go` 7 |
| shape | replaced 748, removed 45, inserted 31 |
| **ids unique** | **824 of 824**; 186 of them need an ordinal above 1 (the same change twice in one function) |
| mutants outside a function or declaration | 0 (8 are in package-level declarations) |
| deterministic | two enumerations of the same tree: identical ids and digests |
| **enumeration time, no test run** | **8.9 s** (8.93 and 8.83 s) |
| deriving the ids | 0.4 s |
| for scale | one Go shard of `quality` runs 3-6 minutes; the whole FULL ~25 minutes |

**Without running the tests (the need of step E):** yes. `go-mutesting --exec` with an executor that records the pair
(original, mutated file) and exits 2 ("did not compile", so the mutant is skipped) enumerates all mutants in 9 seconds
(`tools/c0/record_exec.sh`). The set of mutants and their ids are a function of the source alone.

### Stability under edits that should not matter, and under edits that should

Baseline 824 mutants. "Reuse candidate" = same id **and** same body digest. For comparison, "today" is the number of mutants whose
current id (digest of the whole mutated file, `tools/mutation_shard_exec.sh`) survives the same edit.

| edit | mutants | reuse candidates | id kept, body changed | id gone | id new | today's ids that survive |
|---|---|---|---|---|---|---|
| 3 comment lines + blank lines at the top of `parse.go` | 824 | **824** | 0 | 0 | 0 | 349 |
| a new unrelated function at the top of `parse.go` | 828 | **824** | 0 | 0 | 4 | 349 |
| a new unrelated function in the middle of `parse.go` | 828 | **824** | 0 | 0 | 4 | 349 |
| a new unrelated function at the end of `parse.go` | 828 | **824** | 0 | 0 | 4 | 349 |
| a new unrelated function at the end of `node.go` | 828 | **824** | 0 | 0 | 4 | 807 |
| indentation of one function (tabs to spaces) | 824 | **824** | 0 | 0 | 0 | 823 |
| one string literal in `walker.mapping` | 824 | 799 | 24 | 1 | 1 | 350 |
| one statement added inside `startsScalar` | 828 | 794 | 30 | 0 | 4 | 349 |
| a local variable renamed inside `startsScalar` | 824 | 794 | 22 | 8 | 8 | 349 |
| a constant changed in the body of the callee `patternSpace` | 824 | 818 | 4 | 2 | 2 | 710 |

Reading it:

* **An edit elsewhere in the file leaves every other id and digest alone.** Today, an edit to `parse.go` invalidates all 475 of its
  mutants (the id is the digest of the whole mutated file); 349 of 824 survive, only because the other files were not touched. That
  is the gain the id buys.
* **An edit inside a function moves that function's digest** (24, 30, 22 mutants, the whole function) and, when the changed token
  sits in the changed span of a mutant (the renamed variable), the id of that mutant too. That is correct: a mutant of a function
  that changed is not the same mutant.
* A whitespace-only edit is almost invisible even today (823 of 824; the
  one that differs was not investigated, `go-mutesting` appears to print the mutated files with `go/printer`); comments move nothing
  in the new id, but they do move today's (the 349).
* **The callee row is the point of section 4:** editing `patternSpace` left 818 mutants "reusable", among them the mutants of
  `IsCertPattern`, which calls it. Their id and body digest did not move. Whether their verdict is still right is not something the
  id can say.

### What an edit reaches (the callee rule)

`mutid closure` builds a call graph of the non-test files by names (a selector call `x.m()` is taken to reach every method named
`m`, a function used as a value counts as a call), an over-approximation. For an edit of function `f`, the mutants whose verdict can
change are those of every function that reaches `f`:

| rule | per edited function: mutants that may change (of 824) |
|---|---|
| own body only (the plan's digest rule) | median 7, mean 11, max 154 |
| **call closure** | **median 52 (6.3%), mean 123 (14.9%), max 560 (68%)** |

74 functions, 61 with mutants. By the share of all mutants an edit reaches: 31 functions reach at most 5%, 22 reach at most 25%,
16 at most 50%, 5 reach more than half (`Error.Code`, `Error.Error`, `unsupported`, `table.pos`, `walker.at`: the error and
position plumbing everything calls). Even this rule leaves the typical edit with a large majority of mutants reusable, and the
worst edit with a third. The call graph ignores package-level variables, constants and types, which the real rule has to follow
(section 4); this number is therefore a lower bound of what an edit reaches.

## 3. The rule, and where it breaks

**Rule.** `id` as in section 1; per mutant also store the body digest; a stored verdict of a mutant is reusable at a new commit only
if the same id exists, its body digest is equal, **and** the additional bindings of section 4 hold.

**Failure modes** (all found or reasoned in this study; each should be a fail-closed refusal or a re-run, never a reuse):

* *Ordinal shift.* A new site of the same `(from, to)` earlier in a function renumbers the later ones: `#2` then names another
  site. The body digest changes in the same edit, so no reuse follows, but the **id alone must never be trusted without its digest**.
* *Two mutants with one id* cannot happen by construction here (ordinal), and none did (0 of 824). The plan's fail-closed rule
  (duplicate id makes the record invalid) stays as a check.
* *A change that contains an edit.* Removing a whole statement puts the statement's text into `from`; any edit inside it renames the
  mutant. Conservative, not unsound: it costs a re-run.
* *Unattributed mutants.* A mutant outside every function and declaration gets `<none>`; there were none. It must never be reusable.
* *Dedup by `go-mutesting`.* It drops a mutant whose whole mutated file equals an earlier one. Two mutators giving the same file give
  the same token change, so the same id; nothing is lost.
* *A new mutator set* (tool version) changes the mutants: the tool version is already in the validity key of the record.
* *The token diff is canonical, not semantic.* In repeated token sequences the maximal common prefix places the change at the first
  of equal alternatives. Deterministic, so the id is stable; but `from`/`to` are then not always "the node that was mutated".

## 4. What a reused verdict must additionally be bound to

A verdict `killed` / `survived` / `timeout` / `unviable` is a statement about *the whole program under its tests*. The id and the
body digest of the mutated function do not bind:

1. **Reachable code.** The mutated function calls others; a change there can turn a killed mutant into a surviving one, or the
   other way. Measured above: after editing `patternSpace`, every caller kept its id and digest. **Bind:** a digest over the body
   digests of the call closure, or a coverage-based set of functions that the killing test(s) executed (step D). Over-approximating
   (names, as here) is sound; under-approximating is not.
2. **Package-level state.** Variables, constants, types and their initialisation (including `init`) that the closure uses; an
   interface satisfied by a type the id never mentions. **Bind:** their declaration digests, reached from the closure. The prototype's
   graph does not follow them yet.
3. **Tests and test data.** `*_test.go`, `testdata/`, and here the shared `corpus/` and the pinned upstream (`CIC_PRIMITIVES_ROOT`)
   that the Go tests read. A mutant is killed by a test; change the test and the verdict is another statement. **Bind:** at least a
   digest over all of them (then any commit that touches a test forces Go to FULL), better the coverage map of step D (which tests
   execute which functions) so that only the mutants a changed test covers are re-run. Rough size of the conservative rule: 13 of the
   30 commits on `main` touch `go/`, `corpus/` or `testdata/`; this repository's history is short and this is not a measured rate.
4. **Toolchain and measurement parameters.** Go version, `go.mod` / `go.sum`, `go-mutesting` version and mutator set, the
   per-mutant timeout (a `timeout` verdict is a statement about the timeout), the runner image. These are in or near the validity
   key of the record (step A); the Go ones must be added if they are not.
5. **Flaky verdicts.** `timeout` counts as killed in the gate; a mutant near the timeout can differ between runs. Reusing one
   verdict is then reusing a noisy value; the study did not measure how many Go mutants are near the timeout.

Not measured: **the rate at which a verdict actually flips when only a callee or only a test changes.** Constructing a flip is
easy in principle (weaken a test that kills one mutant: the id and digest of the mutant do not change), and finding one by a
callee edit is hard (the same tests that killed the mutant also pass through the callee). A sampling experiment (mutate, edit a
callee, rerun the real executor on all mutants of the callers) would put a number on it; it needs a mutation run per edit, not a
design, and was not done.

## 5. Cost and economy, honestly

* **Enumeration and id:** 9.3 s per commit, no test. That is free next to a 25 minute FULL.
* **Implementation:** the prototype is 343 lines of stdlib Go (the id, the closure and the command line); the executor
  (`mutation_shard_exec.sh`) can call it or the ids can be computed by one pass over the record files after the run. The
  gate's completeness check keys on the existing whole-file digest; step C would key it on the stable id and keep the same
  "every mutant in exactly one shard, none missing" rule. Sharding by cost (PR "balanced shards") does not depend on the id.
* **What the saving depends on:** not on the id but on the reach rule (section 2) and on how often tests change (section 4.3).
  With the call-closure rule a typical edit leaves about 94% of Go mutants reusable (median), a hub edit (error plumbing) about a
  third; a test edit with the conservative rule leaves none. Go is about 28 of the ~67 machine-minutes of mutation in `quality`
  (6 shards of 3-6 minutes; the Rust shards add ~39), so DELTA for Go saves real time only on commits that touch non-test Go code and
  nothing else, and the wall time of `quality` is set by the slowest shard, not by the sum.
* **Risk:** the new risk is unsound reuse, not a wrong id. The bindings above are what make it sound; each is a place where a
  mistake silently reuses a stale verdict, which is why they belong behind negative probes (a changed callee, a changed test, a
  changed constant, each must force a re-run) before any DELTA verdict may stand in for a FULL one.

## 6. What this study did not do

* It did not run the tests of any mutant, so it did not measure verdict stability, flip rates, or flakiness.
* It did not follow package-level declarations in the closure, nor use `go/types` (the closure is by name: sound as an
  over-approximation for functions, silent about variables, constants and types).
* One package, one commit, one machine; generics, build tags, cgo and `go:generate` are absent from `go/` and were not exercised.
* It did not measure the economy of a whole DELTA (that is step G, in shadow mode).

## 7. Reproduce

The prototype is a Go module of its own (`tools/c0/mutid/go.mod`); the repository root has no Go module, so build and test from its
directory. Workflow `c0-selftest` runs the first line on every change to `tools/c0`; it is research and not a required check.

```
cd tools/c0/mutid && go vet ./... && go test -count=1 ./... && go build -o /tmp/mutid .
cd ../../..                                                # back to the repository root
tools/c0/enumerate.sh go /tmp/rec                          # go-mutesting (the pinned version) and go on the PATH; no test runs
/tmp/mutid ids -orig /tmp/rec/orig -mut /tmp/rec/mut -index /tmp/rec/index.tsv > /tmp/ids.tsv
/tmp/mutid closure -src go > /tmp/closure.tsv
python3 tools/c0/impact.py /tmp/closure.tsv /tmp/ids.tsv
python3 tools/c0/edits.py comments go /tmp/exp/go          # one experiment of section 2; the names are in edits.py. Then:
tools/c0/enumerate.sh /tmp/exp/go /tmp/exp/rec
/tmp/mutid ids -orig /tmp/exp/rec/orig -mut /tmp/exp/rec/mut -index /tmp/exp/rec/index.tsv > /tmp/exp/ids.tsv
python3 tools/c0/cmp.py /tmp/ids.tsv /tmp/exp/ids.tsv
```

The unit tests check the id rule on small sources (a line shift and comments keep it, an edit elsewhere keeps the digest, an edit
inside moves it, equal changes are told apart, a mutant that changes no token is refused).

Nothing here is wired into the gate, the shard tools or the evidence record, and no verdict of an earlier commit is reused anywhere:
this is a study of whether such a reuse could be made sound, not a step towards switching it on.
