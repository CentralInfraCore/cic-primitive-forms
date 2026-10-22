# FULL and DELTA test evidence: the plan

Status: **proposal, revised after review (PR #27: accepted with changes); amended 2026-10-22**: the owner replaced the DELTA model (no shadow mode, a diff gate on pull requests, a nightly FULL), see `docs/DELTA-DIFF-GATE.md`, which wins where the two differ. Nothing here is implemented. It re-opens no decision
of the accepted ADRs and does not change a quality threshold or switch off a gate of workflow `p1`. It is the plan that was agreed
to be written after the parser parity work (PR #25, merged as `ace0740`) and the sharded `quality` job
(`docs/QUALITY-SHARDING.md`).

## 1. What this is for, and what it is not

The quality gate (line coverage and mutation score strictly above 90%, both languages) is a **FULL** measurement: every mutant,
every test. Sharded, it takes 10 to 29 minutes of wall time (one Rust shard took 21 minutes in a run, because mutants that time
out cluster in it); before sharding it took about an hour. Two things are wanted:

1. **A durable, checkable record that a FULL measurement passed on a commit** (FULL evidence), so that "the last good FULL" is a
   fact that can be verified, not a green mark on a web page.
2. **A faster answer for a change that touches little** (DELTA), which says what a FULL run would say, without running all of it.

It is **not** a way to lower a threshold, to skip a gate, or to trust an older green. The full PR gates and the FULL gate on
`main` stay exactly as they are while DELTA is in shadow mode (section 8), and for as long afterwards as the owner decides.

**DELTA has to earn its place.** The sharding already made FULL much faster. If the impact analysis and the mutant identity cost
more than a FULL run, the right engineering decision is to keep FULL; steps A and B (section 9) stand on their own and the
verified evidence they create stays valuable. Section 6.6 says how that is measured.

## 2. Decisions that are made

These come from the owner and are not up for redesign here.

* ~~DELTA starts as a **shadow mode** next to the current full PR and `main` gates. It reports; it does not decide.~~ **Replaced on 2026-10-22** by the diff gate of `docs/DELTA-DIFF-GATE.md`: it is the PR gate, and the nightly FULL on `main` is the safety net.
* `main` gets a **FULL measurement every day**. This is mandatory, not best effort. **Amended on 2026-10-22**: every night at 02:00 (Europe/Budapest), if `main` had development in the last 48 hours; on failure a pull request carries the failed run's identifier (`docs/DELTA-DIFF-GATE.md`, section 4).
* **No fallback to an older success** (section 5.7 says what that means for a failed or missed run).
* The tooling lives under `tools/test-evidence/` of this repository.
* FULL evidence is a **canonical, content-hashed JSON** record kept on a protected branch `ci-evidence`, with a **GitHub
  (Sigstore) attestation bound to the identity of the workflow** that produced it. The logs stay as CI artifacts. There is **no
  Vault Transit prerequisite**.
* A FULL baseline used for a DELTA must be an **ancestor of the target** commit.
* The impact of a change is taken **cumulatively** over `baseline..target`, not commit by commit.
* The validity of a record depends on the **lockfiles, the toolchains, the harness and the version of the selector** (and, since
  the review, on the resolved execution environment, section 5.3).
* **Maximum age of a baseline: 3 days.** A failed, missed or unverifiable mandatory FULL excludes older baselines at once.
* **`ci-evidence` is written by a dedicated GitHub App identity** with limited permissions, a protected branch and a verified
  publishing workflow.
* **Attestation: GitHub artifact attestations (Sigstore)**, actions pinned to immutable revisions, exact workflow, repository, ref
  and commit checked when verifying.
* **The coverage map** is kept as a separate artifact; the record holds its digest, size and object reference. If it is missing or
  its digest is wrong, there is no DELTA.
* **Go mutant identity**: first a feasibility and cost study; if it is too expensive or not provably stable, Go mutation stays on
  FULL.
* **Order of the speed-ups**: caching and shard balancing come before step C, in a separate PR.
* **The per-mutant timeout is not reduced to go faster.** The Rust gate counts a timed-out mutant as killed, so a shorter timeout
  raises the speed and can raise the score without the tests catching one more fault. It changes what the score means and is a
  separate semantic decision, not an optimisation.
* The order of work: the parser differential first (done), then this plan, then its implementation, each reviewable on its own.

## 3. Terms

| Term | Meaning |
|---|---|
| **Required checks** | The list of checks a change must pass to be merged, kept in `tools/test-evidence/required-checks.yaml` (section 5.1). |
| **Quality FULL** | The four measurements decided by `tools/quality_gate.py`: line and statement coverage, mutation score, both languages, all mutants, all tests. |
| **FULL anchor** | A commit for which the quality FULL **and** every required check passed, and the record says so. Only an anchor can be a baseline. |
| **Target** | The code state a result is wanted for. For a pull request it is the **merge candidate** (the head merged into the current base), not the head of the feature branch (section 5.8). |
| **Baseline** | A FULL anchor with a verified record that is an ancestor of the target and is not excluded by section 5.7. |
| **Evidence record** | The JSON document that says: on commit C, in environment E, these checks gave these results. |
| **Validity key** | A digest of everything that, if it changed, would make an older measurement say nothing about the target (5.3). |
| **DELTA** | A measurement of the target that runs only the mutants the change can affect and takes the rest from the baseline's record. |
| **Selector** | The code that decides which mutants a DELTA runs. |

## 4. What exists today

* A sharded quality FULL (`quality-lines`, `quality-mutation-rust` x4, `quality-mutation-go` x6, the deciding `quality`) with a
  completeness check: a shard that did not run, a mutant tested twice or by none, is `INCOMPLETE_SHARDS` and red.
* A gate that fails closed (`tools/quality_gate.py`, 35 self-test faults) and a parity gate that does too (`tools/parity`, 25 breaks).
* The other required checks of workflow `p1` and of `corpus`: corpus, parser parity, property tests (in `rust` and `go`), fuzz,
  branch coverage, the gate and parity self-tests, the seeded defects.
* No `ci-evidence` branch and no repository ruleset at the time of writing: both are to be built in step B, not assumed.
* Per-run results exist only inside the CI run: a verdict, and reports as artifacts that expire.

What is missing, and is the subject of this plan: a record that outlives the run and can be verified later; a rule for which
record may serve as a baseline; a way to say which mutants a change can affect; proof that the way of saying so misses none.

## 5. FULL evidence

### 5.1 The record

One JSON document per mandatory FULL run, schema `test-evidence/1`. Fields (all required unless said):

```
schema            "test-evidence/1"
kind              "full"
outcome           "pass" or "fail"      (a run that did not finish writes no record: it is a missed run, 5.7)
slot              the UTC day of the scheduled run, "2026-10-20", or null for a run caused by a push to main
target            { kind: "main" | "merge_candidate", commit, tree, base_commit (for merge candidates), head_commit }
validity_key      sha256 hex (5.3), and the inputs it was made of, each named with its own digest
environment       the resolved execution environment (5.3)
run               { repository, workflow_path, ref, run_id, run_attempt }
measured_at       RFC 3339 UTC, whole seconds
quality_full      {
                    rust_lines {covered,total}, go_statements {covered,total},
                    rust_branches {covered,total}, go_branches {covered,total},
                    rust_mutation {killed,survived,unviable}, go_mutation {killed,survived,uncompilable},
                    thresholds { "line": 90, "mutation": 90, "branch": 90 }   (as the gate used them),
                    gate_verdict "PASS" | "FAIL", shards { rust: N, go: N }, completeness "complete"
                  }
required_checks   [ { name, conclusion, job_url, run_id, artifact_digest or null } ]  one entry per entry of
                  required-checks.yaml at that commit, nothing more, nothing less
anchor            true only if quality_full.gate_verdict is PASS and every required check concluded "success"
                  (not "skipped", not "cancelled", not "neutral"); false otherwise
mutants           the per-mutant verdicts (5.4): digest, size and the artifact they are in
coverage_map      { digest, size, artifact }  or null (6.3); null means no DELTA from this record
artifacts         [ { name, sha256, size } ]  the CI artifacts that hold the logs and raw reports
```

`required-checks.yaml` lists every check that is required to merge: corpus, parser parity, the property tests, fuzz, branch
coverage, the quality FULL, the self-tests of the gates and of the parity comparator, the seeded defects. It is part of the
validity key, so a check added to it invalidates older records. A daily FULL is a trustworthy anchor for the whole of the
verification, not only for the four quality measurements, **only when `anchor` is true**; a record with `quality_full` passing
and one required check failing is an audit record, not a baseline.

A record with `outcome: "fail"` is kept (it is evidence that the gate ran and what it said), under `evidence/audit/` and not
under `evidence/full/`. It can never be a baseline, and it breaks the chain after it (5.7).

### 5.2 Canonical form and the content hash

The record is serialised as **one declared byte format**, so that the hash cannot depend on who wrote it:

* RFC 8785 (JSON Canonicalization Scheme) restricted to an I-JSON subset: UTF-8, no byte order mark; object member names sorted by
  UTF-16 code unit as RFC 8785 says; no insignificant whitespace; strings escaped as RFC 8785 says; **integers only, in the range
  +-(2^53-1)**, written without exponent, leading zeros or `-0`; no floating point (percentages are derived from counts, never
  stored), no `NaN`, no infinities; a string may not hold a lone surrogate.
* The reader is **strict**: duplicate member names, a number outside the subset, a lone surrogate, a byte order mark, trailing
  bytes, invalid UTF-8, a non-canonical document (it must equal its own canonical form byte for byte) are refused.
* `content_hash = sha256(canonical bytes)`; the file name is that hash. The record does not contain its own hash.
* The format is held by **test vectors** committed with the code (`tools/test-evidence/vectors/`): the order of keys with
  non-BMP characters, every escape, the number forms that are and are not allowed, a duplicate key, a lone surrogate, a document
  that parses but is not canonical. The vectors are read by **two independent implementations** (the Python tool and a small Go
  program) that must agree on every byte and every refusal; the canonical form is a contract, not a few lines of a language.

### 5.3 The validity key and the environment

A FULL measurement says something about a target only if what produced it is the same. The key is the digest of the sorted list
`name = sha256` of:

* the lockfiles (`rust/Cargo.lock`, `go/go.sum`, `rust/fuzz/Cargo.lock`) and `go/go.mod`, `rust/Cargo.toml`;
* the pinned tools and thresholds: `coverage/tools.yaml`, `coverage/scope.yaml`;
* the **whole harness scope**: every file the checks run or read that is not product code: `tools/**`, `.github/workflows/**`,
  `.github/actions/**`, `corpus/**`, `testdata/**`, `coverage/**`, `required-checks.yaml`, and the pinned upstream
  (`corpus/pins.yaml`, with the commit it names);
* the version of the selector (a constant in `tools/test-evidence/`) and the schema version;
* **the resolved environment**, which the record also carries in full in `environment`:
  * the versions that were actually resolved, not the label asked for: `rustc -vV`, `cargo -V`, `go version`, the Python version,
    and the versions of `cargo-llvm-cov`, `cargo-mutants`, `go-mutesting`, `cargo-fuzz` that were installed, with the revision
    of `go-mutesting`;
  * the runner image, as the runner reports it (`ImageOS`, `ImageVersion`), `RUNNER_OS`, `RUNNER_ARCH`;
  * the revision of every action a workflow uses. **Workflows pin actions by full commit SHA**, not by a tag (a change of
    the workflows in step B), and the resolved list is part of the record. A tag such as `v4` or a label such as `stable` or
    `ubuntu-latest` is not an identity.

The list itself is part of the key, so adding a file to the list invalidates every older record. **Any change to anything in the
list means: no DELTA from an older record; a FULL.** This is deliberately blunt, and it has a price that is accepted: a new
runner image, which GitHub publishes regularly, invalidates every baseline until the next FULL has run on it.

### 5.4 Per-mutant verdicts

A DELTA can only reuse what FULL recorded, so FULL records, for each mutant, `{ id, verdict }` where `verdict` is `killed`,
`survived`, `unviable` or `timeout` (counted as killed, as the gate does today).

The id must be **stable across commits**, which today's names are not: a `cargo-mutants` name carries a line and a column
(`src/parse.rs:443:16: replace && with || in check_line`), and the `go-mutesting` digest used by the shard script is the digest of
the whole mutated file, so both change when anything above them changes. Proposed id:

```
file, enclosing function (qualified), operator or replacement text, ordinal of that pair within the function,
digest of the function body
```

The body digest is what lets a DELTA know that a function is unchanged. The ordinal is stable while the function is. Two mutants
that cannot be told apart by this id make the record invalid (the same fail-closed rule as the completeness check). `cargo-mutants`
already reports the function; for Go the mutation hunk has to be mapped to its enclosing function. **That is a feasibility and
cost study first (step C0), not a given**: if the Go id is too expensive or cannot be shown stable, Go mutation stays on FULL
and DELTA covers Rust only, or nothing.

### 5.5 Storage, attestation, verification

* **Where.** Branch `ci-evidence`. Layout: `evidence/full/<content_hash>.json` (anchors), `evidence/audit/<content_hash>.json`
  (failed runs), and `index/<date>.json` (per UTC day: the content hashes of that day's records, so that a verifier can see every
  outcome of a day, not only the successes). Logs, raw reports and the coverage map are not on the branch: they are artifacts of
  the run and the record names them by digest.
* **Who writes it.** A dedicated GitHub App identity with the least permission that lets it push to that one branch, an
  organisation or repository ruleset on `ci-evidence` that allows only that App to push, forbids force pushes and deletion, and
  a publishing workflow that is the only place the App's credentials are used. **At the time of writing neither the branch nor
  a ruleset exists**; step B builds them. Their effect is proved by **negative probes** (7.4), not by their configuration.
* **Attestation.** The publishing workflow attests the record with GitHub artifact attestations (Sigstore), which bind the digest
  to the identity of the workflow. The repository is public, so this needs no further service; it needs `id-token: write` and
  `attestations: write` in that workflow only, and every action in it pinned by SHA.
* **Verification** (done by the code that wants to use a record, in this order, any failure is "no valid record"):
  1. the file's name equals the hash of its canonical bytes, and the bytes are canonical (5.2);
  2. the attestation verifies for that digest with `gh attestation verify` and **the exact expectations**: repository, signer
     workflow path (the evidence workflow, on the protected ref), the source ref, and the source commit that the record names as
     the producing run;
  3. `schema` is known; for a baseline `outcome` is `pass` and `anchor` is true;
  4. `validity_key` equals the key computed now for the target, and the environment block hashes to what the key was made of;
  5. the baseline `target.commit` is an ancestor of the target (`git merge-base --is-ancestor`), and was reachable from `main`;
  6. `measured_at` is not older than **3 days**;
  7. the rule of 5.7 holds;
  8. the artifacts the use needs (the per-mutant verdicts, the coverage map) exist and have the recorded digests.
* **No Vault Transit.** Nothing here depends on the Vault signing chain of the CIC release flow; the attestation is GitHub's.

### 5.6 The mandatory FULL

A scheduled workflow (`schedule` plus `workflow_dispatch`) runs the FULL on `main`'s head, evaluates the required checks for that
commit, writes the record (a `fail` record when anything failed) and publishes it. A `push` to `main` writes a record too, so that
the freshest baseline is the last merge.

**A cron entry alone is not an anchor.** GitHub's documentation says scheduled workflows can be delayed, and some scheduled runs
can be dropped, in periods of high load. So:

* the schedule is a way to ask for the daily run, and the verifier does not trust that it was answered: section 5.7 defines a
  **due slot** per UTC day, and a slot with no record is a **missed** run, which excludes baselines the same way a failed one does;
* a watchdog (a verification run on every pull request and on its own later schedule) that finds the latest due slot without a
  record starts the FULL with `workflow_dispatch` and reports "no baseline: slot missed";
* a FULL that passed but could not be published is a missed run too.

### 5.7 Which record may serve: failed and missed runs break the chain

The three days are an upper bound on age, not a licence to go back to a record that was good before the last mandatory run went
wrong. That would be the fallback to an older success that is ruled out.

Let `S` be the latest **due slot**: the UTC day whose due time, 06:00 UTC of that day, is not after now. Let `R` be the records of
`main` that verify (5.5), pass and fail, ordered by `measured_at`. A record `B` is usable as a baseline only if all of these hold:

1. there is a record for slot `S` (a missed run has none, and that is the reason it is refused);
2. the **newest** record in `R`, whatever its outcome and whether it came from the schedule or from a push, is a passing anchor;
3. `B` is that record or an older **passing anchor that no failed record follows** (a pass, then a fail, then nothing newer, makes
   every record unusable until a new pass);
4. `B` is not older than 3 days and satisfies 5.5.

So after a Monday failure, Sunday's success is not a baseline on Tuesday; the next passing mandatory FULL restores the chain. If
anything about the state is uncertain (the index cannot be read, the verification of a record in `R` fails, two records disagree),
the state is "uncertain" and nothing is usable. **When no baseline is usable, the answer is a FULL run**, never a DELTA from what
there is.

### 5.8 The target of a pull request

The target is the **merge candidate**: the pull request's head merged into the current base, the commit that GitHub's
`pull_request` workflows check out (`refs/pull/N/merge`). The record of a FULL run for a pull request, and the DELTA and the
comparison of section 8, all name `target.commit`, `target.tree`, `base_commit` and `head_commit` and must name the same tree.
A FULL and a DELTA that were computed on different trees are not compared. If the base moves, the merge candidate is another
tree and an earlier result does not apply to it.

## 6. DELTA

> **Superseded on 2026-10-22** by `docs/DELTA-DIFF-GATE.md` (the diff gate). The text below is the baseline-record model that was not chosen; it is kept as the record of the alternative and is not built.

### 6.1 Principle

For a target T and a baseline B (5.7, an ancestor of T), DELTA runs only the mutants that the change `B..T` can have affected,
takes the verdict of all others from B's record, and computes the same score the gate computes over **the mutants of T**. It is a
prediction of what FULL would say, and it is **sound** only if every mutant whose verdict could differ between B and T is in the
selected set.

### 6.2 The selection, conservative by construction

The target's mutants are **enumerated afresh**, on T, by the mutation tools themselves, independently of B's record. That list is
the denominator of the score and the completeness base: carried and run mutants together must be exactly that list, each once.
A mutant of B that is not in the list (deleted, moved to another function, its operator gone) is dropped and counted in the
report; a mutant in the list that B does not have is selected. A baseline's record never decides what T's mutants are.

Input: the cumulative diff `B..T` (one diff), B's record, B's coverage map (6.3). A mutant is re-run if **any** of these holds:

1. it is not in B's record (a new function, a new operator, a moved mutant);
2. the body digest of its function differs from B's;
3. a test that covers its function in B's coverage map executes a function whose body digest differs.

**Full mutation fallback**, which selects every mutant of T, instead, when **any** of these holds:

4. a test file changed, was added, removed or moved (a changed test can change the verdict of any mutant);
5. a file the tests or the build read changed: `corpus/**`, `testdata/**`, the pinned upstream, build scripts, generated files,
   fixtures and shared test helpers, features and profiles in the build configuration;
6. a change that is **not wholly inside the body of a function**: a constant, a static, an initialiser, a type or trait or
   generic definition, a macro, a conditional-compilation attribute, a signature, an import that changes name resolution, a
   global or configuration value, generated code, a file the selector cannot parse or map;
7. a change to anything in the validity key's list (lockfiles, toolchains, the harness scope, workflows, thresholds, scope,
   the selector). Here the baseline is not used at all (section 5.3).

There is **no middle step** such as "every mutant of that file" for a change the selector cannot place: the effect of a global or
a generated value on a function it never names is exactly what a function boundary cannot show. The per-function optimisation
(rules 1 to 3) is used **only inside proven dependency and coverage boundaries**: a change that is wholly inside function
bodies, for functions that B's coverage map covers. Anything else is a full mutation fallback. Unknown is never "not affected".

### 6.3 What the coverage map costs

Rule 3 needs, from the FULL run, which tests execute which functions. The coverage tools already run in the FULL
(`cargo-llvm-cov`, `go test -coverprofile`); recording per-test coverage means running tests one by one (or with the per-test
profile support of the tools), and storing the map as a separate artifact named in the record by digest, size and object
reference. **If the map is missing, or its digest does not match, there is no DELTA.** The cost in time and size is measured
first (step D), before the map is relied on anywhere. If it cannot be had cheaply for a language, that language's DELTA uses the
full mutation fallback for every change.

### 6.4 The result

DELTA reports: the target's mutant list and its size, the selected mutants, the carried ones (with the reason each was safe to
carry), the dropped baseline mutants, the merged counts, the score the gate would compute, the baseline used, the validity key.
The score is computed by the same function as the gate (`tools/quality_gate.py` gains the merge; it does not get a second copy of
the arithmetic). Line and branch coverage are not carried: they are cheap and run in full in a DELTA as well.

### 6.5 What DELTA never does

* It never produces a PASS from a baseline that failed verification (5.5), is not usable (5.7) or is not an ancestor.
* It never carries a verdict across a change of the validity key.
* It never lowers a threshold or applies one of its own; it feeds the existing gate.
* It never runs on `main` as a substitute for the mandatory FULL.

### 6.6 Whether it is worth it

Section 1 says DELTA has to earn its place. In step F the shadow report records, for every run, the time of the whole DELTA
(finding and verifying the baseline, enumerating, selecting, running, merging) next to the time of the FULL of the same run, and
the share of runs that ended in the full mutation fallback. Step G is a decision made on those numbers. If the DELTA, all costs
counted, is not clearly cheaper, or falls back to full mutation in most runs, **FULL stays the gate**, and what is kept is the
verified evidence of A and B.

## 7. Proving the selector and the evidence: self-tests

The reason for this part is the lesson of the gates built so far: a check that cannot be shown to fail proves nothing, and the
blind spot is often shared between the check and what it checks. Every probe has the form "break something, require red".

**7.1 Ground truth for the selector.** For a battery of seeded changes to this repository's own code and tests, run FULL on the
baseline and on the changed tree, compute the set of mutants whose verdict **differs** (including mutants that appear and
disappear), and require `selected` to contain that whole set, or the selector to have taken the full fallback. This is the only
test that can show the selector wrong. It is expensive, so it runs on a schedule and when the selector or the harness changes. It
covers at least:

* a changed line in a function; a new function; a deleted function; a function moved within and between files;
* a changed callee (rule 3) with an unchanged caller whose mutants are killed through it;
* a changed constant, static, initialiser, macro, conditional-compilation attribute, generic or type definition, generated file,
  fixture or test helper, with a function elsewhere whose mutant's verdict depends on it: the fallback must be taken (6.2, 6);
* a changed test; a new test; a deleted test; a changed `testdata` or `corpus` file;
* a change that does not change behaviour (a comment, formatting): the selected set may be smaller, never wrong;
* a lockfile, toolchain, harness, workflow or threshold change: must select everything and use no baseline.

**7.2 The enumeration.** The target's mutant list must be independent of the baseline: a probe removes, adds and moves a mutant
and requires the denominator, the dropped list and the selection to follow the target, not the record.

**7.3 The evidence.** Negative probes that each must end in "no valid record" and therefore a FULL: a record with one byte
changed; a document that is valid JSON but not canonical; a wrong file name; a valid record attested by another workflow, another
ref or another commit than the one it names; a baseline that is not an ancestor; another validity key (one probe per class of
the key's inputs, including the resolved toolchain, the runner image and an action revision); an expired one; a record with
`anchor: false`; a record whose per-mutant verdicts have a duplicate or an unknown id; a missing artifact; a coverage map whose
digest is wrong.

**7.4 The chain and the publishing.** The rule of 5.7 is exercised with seeded histories: a pass, a fail, no newer record; a pass
and a missing due slot; a pass older than 3 days; two disagreeing records; an unreadable index; each must give "no usable
baseline". For the branch and the App (step B) the probes are the real thing: a push to `ci-evidence` by a person's token, by
another workflow, by the same App from another workflow, a force push, a deletion, must each be refused by the repository, and
the probe is part of the workflow that sets this up.

**7.5 The comparison with FULL, in shadow.** See section 8: for each pull request the real FULL runs next to the DELTA and the
two are compared on the same tree. This is not proof, it is the measured agreement over time.

A selector or a verifier that passes all its probes is a candidate; one that has no probe for a case is not trusted for that case.

## 8. Shadow mode

> **Superseded on 2026-10-22**: there is no shadow mode; see `docs/DELTA-DIFF-GATE.md`.

A job `delta-shadow` on pull requests (not required for merge), after the existing FULL, on the merge candidate (5.8):

1. find a usable baseline (5.7); none means "no baseline" with the reason, and the job ends green (this is information, not failure);
2. enumerate T's mutants, compute the selection, run the selected mutants with the existing shard tooling, merge;
3. compare the DELTA's verdict and score with the FULL's of the same run on the same tree, mutant by mutant where both ran;
4. write a report (job summary and an artifact): baseline and why it was usable, the size of the target list, the selected
   count, dropped baseline mutants, whether the full fallback was taken and by which rule, the time of the DELTA (all of it)
   against the FULL, and the agreement.

The job is red on exactly one thing: **an unsafe disagreement**, a mutant that FULL found surviving and that DELTA carried as
killed, or a DELTA PASS where FULL is FAIL. A disagreement in the safe direction (DELTA ran more than needed, or reported a
failure FULL does not) is recorded, not red. Because the job is not required, an unsafe disagreement does not block a pull
request, but it is the signal that stops the work from going on.

The existing FULL gates are untouched.

## 9. Order of work, each its own PR

> **Amended on 2026-10-22**: steps C, D, E and F are not done (the diff gate does not need stable mutant ids, a coverage map or a selector); the new order is in `docs/DELTA-DIFF-GATE.md`, section 7. S, A, B and C0 stand as they were.

| Step | Content | Wiring into CI |
|---|---|---|
| S | **Speed-ups that do not touch evidence or scoring**, before C: balance the Rust shards by estimated cost, cache the installed tools, more Go shards if they pay. **Not** a lower mutation timeout (section 2). | `p1` |
| A | The record format, the canonical JSON contract with its vectors and the two implementations, the validity key computation (with the resolved environment), the verifier including the chain rule of 5.7, the negative probes (7.3, 7.4 without the repository part). No evidence is produced yet. | the self-test in `p1` only |
| B | Pinning all actions by SHA; the `required-checks.yaml`; the mandatory FULL workflow with its due slots and watchdog; the `ci-evidence` branch, the ruleset and the App; the attestation; the real probes of 7.4. **Reviewed on its own, as publishing and signing infrastructure.** | a new scheduled workflow, a ruleset |
| C0 | The Go mutant identity: a feasibility and cost study. Rust identity (5.4) is a small step. The finding decides whether Go mutation stays on FULL. | none |
| C | Stable mutant ids (as far as C0 allows) in `quality_gate.py` and the shard tools; the per-mutant verdicts in the record. The completeness check keeps working. | `p1` writes them |
| D | The per-test coverage map and its measured cost (6.3). | measurement only |
| E | The selector as a library with the ground-truth battery (7.1, 7.2). | the battery, scheduled |
| F | `delta-shadow` (section 8) with the cost report (6.6). | a non-required job |
| G | A measurement window, then a decision by the owner on what DELTA may be used for, if anything, on the numbers of 6.6. Out of scope here. | none |

A and B are useful by themselves (verified evidence of a FULL pass, a daily FULL) and do not depend on DELTA being possible at
all. C0 and C carry the most uncertainty and may show that DELTA costs more than it is worth; the plan says so now so that the
finding is not a surprise. Implementation does not start before this plan is accepted.

## 10. Open items

The six decisions asked for in the first version are made (section 2). What remains for the steps themselves:

1. The App's exact permissions and the ruleset's exact form, to be fixed in step B by what the negative probes show they allow.
2. The due time of a slot (06:00 UTC) and the retention of audit records, to be confirmed in step B against the real behaviour of
   the schedule.
3. The format and size of the coverage map, from step D.
4. The cost of the Go mutant identity, from step C0.

## 11. What does not change

The thresholds (strictly above 90%), the full PR gates and the FULL gate on `main`, the per-mutant timeout, the engine, Relay, the
registry, the ADRs. DELTA in shadow mode decides nothing. Until the owner says otherwise a merge needs a green FULL, as today. **Amended on 2026-10-22**: once the diff gate is switched on (step S of `docs/DELTA-DIFF-GATE.md`), a merge needs a green diff gate, and the FULL gate on `main` is the nightly run.
