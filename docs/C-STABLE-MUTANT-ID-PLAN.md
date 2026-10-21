# Step C: stable mutant identity, shadow only. The implementation plan

Status: **plan, nothing is implemented, no code in the pull request that carries this document.** It refines step C of
`docs/FULL-DELTA-PLAN.md` (table in section 9, the id in 5.4) on the finding of step C0 (`docs/C0-GO-MUTANT-ID-STUDY.md`, merged).
The owner split C in two:

* **C1, stable mutant identity.** The stable id enters the real measurement flow *alongside* the existing identifiers. Every
  FULL run produces both. Uniqueness, determinism and completeness are checked. **No earlier mutation result is reused.**
* **C2, shadow validation.** On changed and unchanged code the stability of the ids is compared; every divergence, duplicate
  and gap is measured and reported. **The FULL result is never replaced by an earlier verdict.**

What C0 says an id alone cannot carry (the call closure, package-level state, tests and test data, toolchain) is **not built
in C**. It is the precondition of the later steps D and E; section 11 states only the interface C leaves for them.
Nothing here changes a threshold, a timeout, a test set, a gate or the fail-closed behaviour of any gate.

## 1. Where things are today (verified against `main`, 38928e3)

| | Go | Rust |
|---|---|---|
| mutation tool | `go-mutesting` (pinned), `--exec tools/mutation_shard_exec.sh`, 6 shards | `cargo-mutants` 27.1.0, 6 shards dealt by cost (`tools/shard_plan.py`) |
| identifier today | first 16 hex of the sha256 of the **whole mutated file** (changes with any edit of that file) | the name with line and column: `src/parse.rs:411:52: replace > with >= in scan_words` |
| line-insensitive key today | none | `tools/shard_plan.py: keys()` -> `file: what#ordinal` (used only for cost lookup) |
| verdict record | ledger, 4 tab separated fields per mutant per shard: `<id> <shard> <shards> killed\|survived\|uncompilable\|offshard` | `outcomes.json` per shard (`scenario.Mutant.name`, `summary`) |
| list of mutants to check against | none; consistency between the six ledgers only | `cargo mutants --list --json`, uploaded as `quality-listed` |
| consumed by | `quality_gate.py: go_ledgers` (fail closed: exactly 4 fields, known verdict, one owner per mutant, same mutants in every shard), `test-evidence/build.py` (re-reads the same ledgers) | `quality_gate.py: rust_mutants` (every listed mutant in exactly one report), `build.py` |
| in the evidence record | `quality_full` counts only; `record.mutants` is `null` | same |

Two facts shape the plan: (1) the ledger is parsed in **two** places with a fixed 4-field shape, so it must not change;
(2) `test-evidence/1` already has a nullable `mutants` member (an artifact reference: digest, size, artifact name) that B1's
builder sets to `null`, so a later carrier needs no format change.

## 2. Decisions

1. **The ledger and `outcomes.json` do not change.** The ids go into **new sidecar files**. `quality_gate.py`,
   `coverage_gate.py` and `test-evidence` are not touched in C, so the way they parse the ledger stays exactly as fail closed as
   it is (section 7).
2. **Two independent derivations of every table**, because one derivation checking itself proves nothing: the shard that
   actually ran the mutant emits the row of the mutants it owns (Go, C1a) / its mutants (Rust, C1b); a separate enumeration, which
   runs no test, derives the whole table again. C1c requires them to be byte equal.
3. **The checks live in a separate, non-required job** (`mutant-ids`). In C1 and C2 a failing id check never turns `quality`
   red, because the id tools are unproven; the exit criteria of section 10 are what may later make it blocking, by the owner's
   decision in G. The job is not added to `required-checks.json`.
4. **No consumer.** Nothing in the repository reads the tables to decide anything. A test (check G1, section 5) makes that a
   property of the tree: no gate, no verifier, no workflow step other than the id jobs refers to the tables.
5. **Ids carry a rule name** (`go-token-diff/1`, `rust-name/1`). A change of the rule is a new generation: ids of different rules
   are never compared.

## 3. The identity

### 3.1 Go (`go-token-diff/1`, from C0)

For a mutant (original file `O`, mutated file `M`):

* tokenize both with `go/scanner` (comments and whitespace are not tokens);
* the change is what remains after removing the longest common token prefix and suffix: `from` and `to` (either may be empty);
* `unit` is the top-level function or method (`Recv.Name`) containing the first differing token of `O`, or a package-level
  declaration (`<var x>`, `<const y>`, `<type T>`); `body` is the sha256 of the unit's token stream;
* `change = sha256(from_text + "\0" + to_text)` (C0 hashed the texts inside the id; hashing them first is equivalent and keeps rows short);
* `at` is the token index of the first difference in `O`; it only orders the mutants of one unit;
* `ordinal` counts the mutants with the same `(file, unit, change)` in order of `at`, from 1;
* `id = sha256(rule \0 file \0 unit \0 change \0 ordinal)[:16]`.

One edge C0 did not meet and C1 must handle: Go allows several `func init()` per package and file, so `unit = init` is not
unique. The unit of an `init` or `_` function is `init#k`, `k` its declaration index in the file. Until that is built the tool
refuses (check K2) rather than guess; `go/` has none today (no `func init` or `func _` in the seven non-test files; C1a re-checks).

### 3.2 Rust (`rust-name/1`)

From `cargo mutants --list --json` entries (`name`, `file`, `function.function_name`, `function.span`, `genre`, `span`):

* `change = sha256(what)`, `what` = the name without the `file:line:col: ` prefix (it already contains the operator or
  replacement and the function: `replace > with >= in scan_words`);
* `unit = function_name` (`Grammar::version`);
* `at = (span.start.line, span.start.column)` of the mutation, for ordering only;
* `ordinal` as above; `id = sha256(rule \0 file \0 unit \0 change \0 ordinal)[:16]`. This is the same derivation as the
  `keys()` of `shard_plan.py` (`file: what#ordinal`), which stays as it is; the id is its hash plus the unit;
* `body` = sha256 of the source text of `function.span`, every run of whitespace collapsed to one space. **Comments, doc comments
  and attributes are inside the span and therefore inside the digest** (checked on three mutants: the span starts at the first
  doc comment or attribute line). A comment edit changes the digest: conservative, never unsound. There is no Rust tokenizer in
  the toolchain-free tools; adding one is not C.

Measured on the 467 mutants of `main`: 72 functions, each with exactly one span; the five genres are `FnValue`,
`BinaryOperator`, `UnaryOperator`, `MatchArm`, `MatchArmGuard`. Not verified: whether two methods with the same
`function_name` in different `impl` blocks of one file occur (the key would then share a unit; uniqueness is still given by the
ordinal, but check K2 reports it as a warning class, section 5).

## 4. Data: the files C produces

### 4.1 Sidecar rows (per shard, new artifacts `go-ids-N`, `rust-ids-N`)

TSV, one row per mutant **the shard owns**, UTF-8, `\t`/`\n` in previews replaced by a space, no header:

```
legacy  file  unit  at  change  body  preview
```

`legacy` is the existing identifier (Go: the ledger id; Rust: the cargo-mutants name). `at` is an integer (Go) or `line:col` (Rust).
`change` and `body` are 64 hex. `preview` is at most 80 characters, for people, not part of any hash. Because the ordinal needs
the whole set of mutants, **a row has no ordinal and no id**; both are assigned in assembly (4.2), only when the set is complete.

### 4.2 The table (`mutant-ids/1`, artifacts `mutant-ids-go`, `mutant-ids-rust`)

One JSON document per language in the canonical form of `test-evidence` (sorted members, no insignificant whitespace; the
RFC 8785 subset of `tools/test-evidence/canon.py`), so that the table has **a digest** and two derivations can be compared by
bytes:

```
{ "schema": "mutant-ids/1", "language": "go", "rule": "go-token-diff/1",
  "mutants": [ { "id": "16 hex", "legacy": "…", "file": "parse.go", "unit": "walker.mapping", "ordinal": 1,
                 "change": "64 hex", "body": "64 hex", "verdict": "killed", "shard": 2, "preview": "…" } ] }
```

* sorted by `id`; no timestamp, commit or host inside, so the same source gives the same bytes;
* `verdict` is joined from the measurement: Go from the ledger (`killed`, `survived`, `uncompilable`; the `offshard` rows are not
  verdicts), Rust from `outcomes.json` (`CaughtMutant` -> `killed`, `MissedMutant` -> `survived`, `Timeout` -> `timeout`,
  `Unviable` -> `unviable`; the baseline `Success` row is dropped). Go cannot tell a test timeout from a kill: the executor
  discards the output and `go test` exits 1 for both. C does not change that; the table says `killed`;
* size, estimated from the rows of C0: about 270 KB for Go (824 mutants), 140 KB for Rust (467), well under 1 MB per run.

The commit the table belongs to is not inside it: it is the commit of the run that uploaded it (artifact of that run), and later
the evidence record's `target.commit`.

## 5. The checks, and what each is shown to fail on

`tools/mutant_ids.py` (Python, stdlib, imports `quality_gate` for the verdict cross-check). Subcommands: `rows`
(Rust rows from a list), `assemble`, `check` (C1c), `compare` (C2). The Go row derivation is a small Go program
(`tools/mutantid/`, the production form of the C0 prototype `tools/c0/mutid`, its own module like the prototype; `go/` and its
gates are untouched).

| # | Check | Fails when | Seeded defect / probe that must turn a test red |
|---|---|---|---|
| K1 | **Format.** Rows have 7 fields, hex lengths, integer `at`; the table matches the schema | a row is short, long, non hex, has a stray tab | a 6-field row; a 65-char `change`; a header line; an empty file |
| K2 | **Uniqueness.** `id` unique; `legacy` unique; id <-> legacy is a bijection; no unit `<none>`; no `init` without index | two mutants share an id, or a legacy id maps to two ids | duplicate a row; make the ordinal constant; drop `init#k`; hash only `unit`; unattributed unit |
| K3 | **Completeness against the measurement.** Go: the legacy ids equal the ids of the six ledgers. Rust: the legacy names equal the listed names and the names in the outcomes | a mutant has a verdict and no id, or an id and no verdict | remove a row; add a row; a ledger id with no row; one shard's sidecar missing |
| K4 | **Determinism across two derivations.** The table assembled from the shards' rows is byte equal to the one derived by the independent enumeration (Go) / the `quality` job's list (Rust) | any byte differs | change one `body` in one shard; sort order not canonical; a row in a different ordinal; enumeration at another commit |
| K5 | **The table does not contradict FULL.** `killed` and `killed + survived` per language equal what `quality_gate.go_ledgers` / `rust_mutants` return for the same files | the counts differ | flip one verdict in the table; count `uncompilable` as killed |
| G1 | **No consumer.** `quality_gate.py`, `coverage_gate.py`, `test-evidence/*.py`, and every workflow step other than the id jobs, do not name `mutant_ids`, `mutant-ids` or `mutantid` | a decision path starts to read the tables | add the import to the gate in a scratch copy |
| E1 | **Executor unchanged.** With the id tool missing, failing or printing garbage, `mutation_shard_exec.sh` writes a byte identical ledger and returns the same exit status | the id code can change a verdict, a ledger line or the exit code | make the tool exit 1; delete the binary; fill the sidecar path with a directory; put `set -e` into the id subshell |
| S1 | **Stability** (C2, section 6): unrelated edits keep every id and digest | an id gone or new in a unit the edit did not touch | the C0 edit set against a rule that uses the line number or the file digest |

Each row of "seeded defect" is a test in `tools/test_mutant_ids.py` (`unittest`, stdlib, same style as `test_shard_plan.py`:
unbroken copy passes, each textual defect makes a test fail, `STALE` entries fail the run) and runs in job `quality-gate-selftest`
(0.1 min today). K3 and K4 additionally run on the real artifacts in `mutant-ids`.

## 6. C2: shadow validation

A job `mutant-ids-shadow` in `p1` (pull requests and pushes to `main`), **independent of the FULL jobs** (no `needs`, nothing
needs it, it reads no verdict). It derives both tables itself, at the base and at the head, by the enumeration only:

1. check out the head and the base (`pull_request`: the merge base; `push` to `main`: the previous `main` commit);
2. enumerate Go (`go-mutesting` with the recording executor `tools/c0/record_exec.sh` form, moved to `tools/mutantid/`) and list
   Rust (`cargo mutants --list --json`) at both commits, derive the tables (no ordinal check against a ledger; verdict fields are absent);
3. compare, and attribute every difference to the diff: `git diff -U0 base head` hunks are mapped to units (Go: `go/ast` unit
   ranges; Rust: `function.span`); an id is **explained** if its unit overlaps a hunk;
4. run the **control experiments** of C0 on a scratch copy of the head (comment lines at the top, an unrelated function at the
   top / middle / end, an edit in another file, an indentation change) for both languages, each expected to keep every id and
   digest of the other units: this tests the rule on every run, not only on the commits that happen to arrive.

Written as `mutant-id-shadow.json` (schema `mutant-id-shadow/1`: base and head commit, per language the counts, the divergence
list, the controls) and as the job summary. **Divergence classes**, each counted and listed with the mutant:

| class | meaning |
|---|---|
| V1 unexplained change | an id gone or new, or a body digest changed, in a unit no hunk touches: the rule or the tooling is wrong, or the toolchain differs |
| V2 cosmetic change | a hunk touches a unit but its token stream (Go) is equal: ids and digest must be equal; a difference is a bug |
| V3 duplicate / gap / non-determinism | K2, K3 or K4 fail on a table of the shadow job (two enumerations of the same commit are compared) |
| V4 control failure | a control experiment changed an id it must not change |
| V5 count drift | the number of mutants changed by more than the units that the hunks touch can account for |
| ordinal shift | not a failure: reported with a count, because it explains a gone/new pair in a changed unit |

Never: the shadow job does not choose, skip or replace anything. FULL keeps running all mutants and deciding the gate; the
table of C1 and the tables of C2 are artifacts of a run, read by no gate.

## 7. How `quality_gate.py` stays fail closed

* **The ledger format does not change.** `go_ledgers` still insists on exactly 4 tab separated fields, numeric shard and shard
  count, a verdict from `{killed, survived, uncompilable, offshard}`, one owner per mutant and the same mutants in every
  shard; `build.py` reads the same lines. The sidecar is a different file in a different artifact (`go-ids-N`, not `go-ledger-N`),
  so a sidecar cannot be mistaken for a ledger and a ledger cannot grow a column unnoticed.
* **A probe for it already exists in kind** ("a go ledger line is garbage", "an empty go ledger"); C1a adds one selftest fault
  to `quality_gate.py selftest`: *a ledger line with a fifth field is REPORT_FORMAT*, so the property "the gate rejects a changed
  ledger shape" is measured and not assumed.
* The gate gains no input and no flag. `quality` stays `if: always()`, does not `need` the id jobs, and does not read their artifacts.
* The id work in the executor (C1a) is the only change to code that sits inside the measurement; its isolation is check E1.

## 8. Where the ids are emitted

| Where | What | PR |
|---|---|---|
| `tools/mutation_shard_exec.sh` | after the verdict is known and **after** the ledger line is written, for an **owned** mutant only: `( "$MUTANTID" row "$MUTATE_ORIGINAL.tmp" "$MUTATE_CHANGED" "$id" >> "$CIC_MUTATION_IDS" ) 2>/dev/null \|\| true`. The id code runs in a subshell whose status and output cannot reach the verdict or the exit code; `CIC_MUTATION_IDS` unset means nothing is done. The executor already moved the original aside (`.tmp`), which is the pristine original | C1a |
| `quality-mutation-go` job | builds `tools/mutantid` (stdlib only, ~2 s), sets `MUTANTID` and `CIC_MUTATION_IDS`, uploads `go-ids-N` next to `go-ledger-N` | C1a |
| `quality-mutation-rust` job | `python3 tools/mutant_ids.py rows` over the shard's `names.json` (it already holds `all.json`, the list, from the balanced-shard step) plus the shard's `outcomes.json`; uploads `rust-ids-N` | C1b |
| `mutant-ids` job (new) | downloads `go-ids-*`, `rust-ids-*`, ledgers, outcomes, `quality-listed`; enumerates Go with the recording executor (the independent derivation); runs `assemble` and `check`; uploads `mutant-ids-go`, `mutant-ids-rust`; writes the job summary | C1c |
| `mutant-ids-shadow` job (new) | section 6 | C2 |

Only owned mutants are derived in the Go shards (about 137 of 824 per shard), so the cost per shard is the owned share.

## 9. CI cost, and the critical path

The critical path of FULL is the slowest mutation shard plus `quality` (about 12 minutes now, as reported by the coordinator after
step S; the post-S figure was not re-measured for this plan). C adds nothing to it by construction, to be shown by measurement in
each PR:

| Addition | Where | Expected | On the critical path? |
|---|---|---|---|
| id row per owned Go mutant | Go shards (3-6 min each) | ~137 process starts of a ~1 ms Go binary plus a parse of one file: well under 5 s per shard | adds <=5 s to a Go shard, which is not the longest job |
| build of `tools/mutantid` | Go shards | ~2 s, no dependencies | same |
| rows for Rust | Rust shards (~6.5 min each) | Python, <1 s | no |
| `mutant-ids` | after the shards, parallel with `quality` (3-5 min) | download + enumeration 9-14 s + assembly + checks: about 1.5-2 min | no, as long as it ends before `quality` |
| `mutant-ids-shadow` | from the start, parallel to everything | two checkouts, 2 x (Go enumeration ~10 s + Rust list), 6 control experiments per language in parallel: about 2-3 min | no |

If a PR shows the workflow's wall time rising beyond the noise (the measured spread of FULL wall time over ten runs was 22-27 minutes
before S), the PR does not merge; the id jobs may be made scheduled instead of per push. The numbers above are estimates from the C0
measurements (enumeration 8.9 s, derivation 0.4 s) and not yet measured in `p1`.

## 10. Rollout: separate small PRs, and what each must show

| PR | Content | Merge requires |
|---|---|---|
| **C1a** Go id emission | `tools/mutantid/` (row derivation; `tools/c0/mutid` moved with `git mv`, its experiments stay in `tools/c0`), the guarded call in `mutation_shard_exec.sh`, `go-ids-N` upload, `tools/test_mutant_ids.py` (K1, E1 and the Go half of K2), one selftest fault in `quality_gate.py`. | Ledger of the PR run equals, id for id and verdict for verdict, the ledger of the last `main` run (Go code is identical); E1 shown to fail on its defects; before/after wall time of the Go shards and of the workflow |
| **C1b** Rust key | `tools/mutant_ids.py rows` (and the Rust half of K1/K2), `rust-ids-N` upload. `shard_plan.py` is not changed. | Rows of the six shards together equal the list of mutants (467); the open question about equal `function_name`s answered on `main`; before/after timing |
| **C1c** checks and artifact | `assemble`, `check` (K3, K4, K5), G1, job `mutant-ids`, artifacts `mutant-ids-go/-rust`, `docs/QUALITY-SHARDING.md` section, and the sentence in `docs/TEST-EVIDENCE.md` that `record.mutants` may later reference the table (no format change, no builder change) | every seeded defect of section 5 caught; the job green on a real run for two consecutive commits with a code change in between; timing |
| **C2** shadow | `compare`, the controls, job `mutant-ids-shadow`, `mutant-id-shadow/1` artifact, an aggregator `mutant_ids.py report` (downloads the artifacts of the last N runs with `gh`, prints the section 10 numbers) | the controls pass on `main`; divergence classes demonstrably detected by seeded defects (V1-V5 each have one); timing |

C1a and C1b touch different files but both edit `p1.yml`: they are merged one after the other, the second rebased on `main`
first (the usual rule). Each PR is mergeable on its own and changes no result.

### Exit criteria for G (measurement and decision)

G decides, on numbers, whether the ids are good enough to be a basis for D and E. Proposed, for the owner to adjust:

1. **Window.** At least 30 FULL runs on `main` over at least 10 distinct days, including at least 8 commits that changed Go or Rust
   source, and the shadow job on at least 30 pull request runs.
2. **Zero defects of the tooling.** K1-K5 and G1 never failed in the window; no V1, V3, V4, V5 divergence is unexplained; every V2 is
   a bug that was fixed, and the fix has its own seeded defect.
3. **Complete attribution.** No unit `<none>` in any table; no `init`-style ambiguity left; the share of mutants whose id and body
   digest are unchanged between consecutive `main` commits is reported per language and per kind of commit (Go source only, Rust
   source only, tests or corpus only, docs only). That number is the ceiling of what DELTA could ever reuse; if it is small
   the decision is "ids are fine, DELTA is not worth it".
4. **Controls.** 100% of control experiments keep their ids, in every run of the window.
5. **Cost.** The wall time of `p1` did not rise by more than the noise (median over the window within the spread measured before
   C1a); the id jobs' own time is reported.
6. **Outputs for the owner:** per language "stable id viable: yes / no", the ceiling of criterion 3, the cost of criterion 5, and the
   list of what is still unbound (section 11). **G does not reuse a verdict.** Whether D/E may start is the owner's decision.

## 11. The interface C leaves for D and E (and what is not built)

C produces, for every FULL run and per mutant: `id`, `legacy`, `file`, `unit`, `ordinal`, `change`, `body`, `verdict`, `shard`. D
(coverage map) and E (selector) will need, and C **does not** produce:

* a digest of the **call closure** of the unit (C0, section 4.1) and of the **package-level declarations** it reaches (4.2);
* the **tests and test data** that bear on the verdict: a digest of all of them, or the coverage map of D (4.3), including the
  shared `corpus/` and the pinned upstream the Go tests read;
* **toolchain and measurement parameters** in the validity key (4.4): Go version, `go.mod`/`go.sum`, the `go-mutesting` version
  and the `MUTATE_TIMEOUT` of the executor, which the Go ledger does not record.

The table has room for them without a new generation: later members (`closure`, `deps`, `tests`) are additional optional fields of
`mutant-ids/1`, absent now. A reader that finds them absent must treat every mutant as not reusable. **The id and the body digest
are never, in any step, sufficient for reuse.**

The evidence record needs no change in C. `record.mutants` (nullable artifact reference) is where a later, separate PR may put the
digest and size of the table; B1's builder sets it to `null` now and keeps doing so. If it is ever filled, the table's digest enters
the content hash like any record member, and the verifier would check the artifact against its digest (`ARTIFACT_MISSING`,
`ARTIFACT_DIGEST`) as for every other artifact. That is decided in G, not here.

## 12. Risks, and what is not verified

* **The executor is measurement code.** C1a is the one change inside it. The isolation (subshell, `|| true`, after the ledger
  write, unset variable = no-op) is shown by E1 and by the equal-ledger condition of the PR; it is not proven for every failure of
  the host (a full disk while the sidecar is written could still make the subshell slow; `timeout` around it is an option if the
  first measurement shows any effect).
* **Estimated, not measured in CI:** all timings of section 9. The enumeration figure is from a developer machine (8.9 s; 12-14 s
  when rerun on a busier one).
* **Go ordinals depend on the complete set.** A row is meaningful only after assembly; the table is only produced when K3 holds.
  An ordinal shift inside a changed unit is expected and reported, not a defect.
* **Token-diff placement in repeated token runs** is deterministic but not always "the node that was mutated" (C0, section 3). It
  does not matter for identity, and matters for any future attempt to explain a mutant to a person.
* **Rust `body` includes comments and attributes.** Conservative; it makes the Rust ceiling of criterion 3 lower than a token digest
  would. A tokenizer is out of scope.
* **Go timeouts are indistinguishable from kills** in the ledger; the table says `killed`. Harmless in C, a gap for any step that wants
  the plan's four verdicts for Go.
* **Unverified:** the equal `function_name` question in Rust; whether `go-mutesting` or `cargo-mutants` change their set of mutants
  between pinned versions in a way the rule name does not capture (the tool version is in the validity key, the rule name is
  ours); behaviour on generics beyond the receiver forms the prototype handles; any package other than `go/`; the real cost of the
  jobs of sections 8 and 9.
* **Scope creep.** The temptation is to add the closure digest "while the tool is open". It is a separate step with its own
  negative probes (a changed callee, a changed test, a changed constant each forcing a re-run) and is not in C.
* **What this plan does not decide:** that DELTA will exist. C1 and C2 are worth having only for the numbers they produce for G.
