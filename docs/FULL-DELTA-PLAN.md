# FULL and DELTA test evidence: the plan

Status: **proposal for review**. Nothing here is implemented. It re-opens no decision of the accepted ADRs and does not change a
quality threshold or switch off a gate of workflow `p1`. It is the plan that was agreed to be written after the parser parity work
(PR #25, merged as `ace0740`) and the sharded `quality` job (`docs/QUALITY-SHARDING.md`).

## 1. What this is for, and what it is not

The quality gate (line coverage and mutation score strictly above 90%, both languages) is a **FULL** measurement: every mutant,
every test. Sharded, it takes 10 to 29 minutes of wall time (one Rust shard took 21 minutes in the last run, because mutants that
time out cluster in it); before sharding it took about an hour. Two things are wanted:

1. **A durable, checkable record that a FULL measurement passed on a commit** (FULL evidence), so that "the last good FULL" is a
   fact that can be verified, not a green mark on a web page.
2. **A faster answer for a change that touches little** (DELTA), which says what a FULL run would say, without running all of it.

It is **not**: a way to lower a threshold, to skip a gate, or to trust an older green. The full PR gates and the FULL gate on
`main` stay exactly as they are while DELTA is in shadow mode (section 8), and for as long afterwards as the owner decides.

## 2. Decisions that are already made

These come from the owner and are not up for redesign here.

* DELTA starts as a **shadow mode** next to the current full PR and `main` gates. It reports; it does not decide.
* `main` gets a **FULL measurement every day**. This is mandatory, not best effort.
* **No fallback to an older success.** If the evidence needed is missing, invalid or expired, the answer is a new FULL run, never
  "the previous green will do".
* The tooling lives under `tools/` of this repository (proposed: `tools/test-evidence/`).
* FULL evidence is a **canonical, content-hashed JSON** record kept on a protected branch `ci-evidence`, with a **GitHub
  (Sigstore) attestation bound to the identity of the workflow** that produced it. The logs stay as CI artifacts. There is **no
  Vault Transit prerequisite**.
* A FULL baseline used for a DELTA must be an **ancestor of the target** commit.
* The impact of a change is taken **cumulatively** over `baseline..target`, not commit by commit.
* The validity of a record depends on the **lockfiles, the toolchains, the harness and the version of the selector**.
* The order of work: the parser differential first (done), then this plan, then its implementation, each reviewable on its own.

## 3. Terms

| Term | Meaning |
|---|---|
| **FULL** | The existing measurement: all mutants (Rust and Go), all tests, line and statement coverage, branch coverage, decided by `tools/quality_gate.py`. |
| **Target** | The commit a result is wanted for (a PR head, or `main`). |
| **Baseline** | A commit with valid FULL evidence, an ancestor of the target. |
| **Evidence record** | The JSON document that says: on commit C, with environment E, the FULL measurement gave these results. |
| **Validity key** | A digest of everything that, if it changed, would make an older measurement say nothing about the target (section 5.3). |
| **DELTA** | A measurement of the target that runs only the mutants and tests that the change can affect, and takes the rest from the baseline's evidence. |
| **Selector** | The code that decides which mutants a DELTA runs. |

## 4. What exists today

* A sharded FULL (`quality-lines`, `quality-mutation-rust` x4, `quality-mutation-go` x6, the deciding `quality`) with a completeness
  check: a shard that did not run, a mutant tested twice or by none, is `INCOMPLETE_SHARDS` and red.
* A gate that fails closed (`tools/quality_gate.py`, 35 self-test faults) and a parity gate that does too (`tools/parity`, 25 breaks).
* Per-run results exist only inside the CI run: a gate verdict, and the reports as artifacts of that run that expire.

What is missing, and is the subject of this plan: a record that outlives the run and can be verified later; a way to say which
mutants a change can affect; proof that the way of saying so does not miss any.

## 5. FULL evidence

### 5.1 The record

One JSON document per FULL measurement, schema `test-evidence/1`. Fields (all required unless said):

```
schema            "test-evidence/1"
kind              "full"
commit            the 40-hex SHA of the measured commit
tree              the SHA of its tree (to tell a rebased commit from the same content)
validity_key      sha256 hex, section 5.3, and the inputs it was made of (named, each with its own digest)
run               { repository, workflow_path, ref, run_id, run_attempt, runner_os }
measured_at       RFC 3339 UTC
results           {
                    rust_lines {covered,total}, go_statements {covered,total},
                    rust_branches {covered,total}, go_branches {covered,total},
                    rust_mutation {killed,survived,unviable}, go_mutation {killed,survived,uncompilable},
                    thresholds { "line": 90, "mutation": 90, "branch": 90 }   (as the gate used them)
                  }
mutants           the per-mutant verdicts (section 5.4), as a digest plus the artifact it is in
shards            { rust: N, go: N } and the completeness result of the gate (INCOMPLETE_SHARDS absent)
gate_verdict      "PASS" or "FAIL" and the gate's own output digest
artifacts         [ { name, sha256, size } ]  the CI artifacts that hold the logs and raw reports
```

A record of a **failed** FULL is kept too (it is evidence that the gate ran and what it said), but it can never serve as a baseline:
only `gate_verdict == "PASS"` with no waived reason is a baseline.

### 5.2 Canonical form and the content hash

The record is serialised in a canonical form so that the hash does not depend on who wrote it: UTF-8, object keys sorted, no
insignificant whitespace, integers as integers, no floating point (percentages are derived from counts, not stored), no duplicate
keys. `content_hash = sha256(canonical bytes)`; the file name is that hash. The canonicalisation is a few lines of code with test
vectors, not a library choice. The record does not contain its own hash.

### 5.3 The validity key

A FULL measurement says something about a target only if what produced it is the same. The key is the digest of the sorted list
`name = sha256` of:

* the lockfiles (`rust/Cargo.lock`, `go/go.sum`, `rust/fuzz/Cargo.lock`) and `go/go.mod`, `rust/Cargo.toml`;
* the pinned toolchains and tools: `coverage/tools.yaml`, the Rust and Go versions the workflow installs, the pinned versions of
  `cargo-llvm-cov`, `cargo-mutants`, `go-mutesting`, `cargo-fuzz`;
* the harness: `tools/quality_gate.py`, `tools/mutation_shard_exec.sh`, `tools/mutation_reduced_exec.sh`, `tools/coverage_gate.py`,
  `tools/branchcov/**`, `tools/seeded_defects.py`, `tools/test-evidence/**` and the workflow files `.github/workflows/p1.yml` and
  `.github/actions/**`;
* the thresholds and the scope (`coverage/scope.yaml`);
* the pinned upstream (`corpus/pins.yaml`) and the corpus files the tests read (`corpus/**`, `testdata/**`);
* the version of the selector (a constant in `tools/test-evidence/`), the schema version.

The list itself is part of the key, so adding a file to the list invalidates every older record. **Any change to anything in the
list means: no DELTA from an older record; a FULL.** This is deliberately blunt.

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

The body digest is what lets a DELTA know that a function is unchanged. The ordinal is stable while the function is. Two
mutants that cannot be told apart by this id make the record invalid (the same fail-closed rule as the completeness check).
Producing this id for Go needs the mutation hunk's position mapped to the enclosing function; for Rust `cargo-mutants` already
reports the function. This is the largest piece of new code of the plan.

### 5.5 Storage, attestation, verification

* **Where.** Branch `ci-evidence`, protected (no direct pushes by people, no force pushes, no deletion; only the evidence workflow
  can push). Layout: `evidence/full/<content_hash>.json` and `index/<commit>.json` (a pointer file naming the content hash(es) for a
  commit). Logs and raw reports are not on the branch: they are artifacts of the run and the record names them by digest.
* **Attestation.** The workflow that produces the record attests it with GitHub artifact attestations (Sigstore), which bind the
  digest of the record to the identity of the workflow (repository, workflow path, ref, run). The repository is public, so this is
  available without further setup; it needs the `id-token: write` and `attestations: write` permissions in that workflow only.
* **Verification** (done by the code that wants to use a record, in this order, any failure is "no valid record"):
  1. the file's name equals the hash of its canonical bytes;
  2. `gh attestation verify` (or the equivalent API) succeeds for that digest and the signer workflow identity is exactly the
     evidence workflow of this repository on `main`;
  3. `schema` is known; `gate_verdict` is `PASS`;
  4. `validity_key` equals the key computed now for the target;
  5. `commit` is an ancestor of the target (`git merge-base --is-ancestor`), and not the target of a different tree;
  6. `measured_at` is within the validity window (section 10, open decision);
  7. the artifacts named exist and have the recorded digests, when the use needs them.
* **No Vault Transit.** Nothing here depends on the Vault signing chain of the CIC release flow; the attestation is GitHub's.

### 5.6 The daily FULL

A scheduled workflow (`schedule` plus `workflow_dispatch`) runs the existing FULL on `main`'s head, writes the record, attests
it and pushes it to `ci-evidence`. If the daily run is red or does not run, there is no new record, and the old ones expire by
their window: that is the intended pressure. A `push` to `main` does not need one (the existing FULL on the merge commit is the
same measurement); the same job can write the record there, so that the freshest baseline is the last merge.

## 6. DELTA

### 6.1 Principle

For a target T and a valid baseline B (ancestor of T), DELTA runs only the mutants that the change `B..T` can have affected, takes
the verdict of all others from B's record, and computes the same score the gate computes. The result is a prediction of what FULL
would say. It is **sound** only if every mutant whose verdict could differ between B and T is in the selected set.

### 6.2 The selection, conservative by construction

Input: the cumulative diff `B..T` (one diff, not a chain), the record of B, the coverage map of B (function to the tests that execute
it; section 6.3). A mutant is selected, to be run again, if **any** of these hold:

1. it does not exist in B's record (a new function, a new operator);
2. the body digest of its function differs from B's (the function changed);
3. a test that covers its function in B's coverage map executes a function whose body digest differs (a change elsewhere that a
   killing test would see);
4. a test file changed, was added or removed (a changed test can change the verdict of **any** mutant): then **all** mutants are
   selected, which is a FULL for mutation;
5. a data file the tests read changed (`corpus/**`, `testdata/**`, the pinned upstream): all mutants, as in 4.

And, before any of it, the **FULL triggers**: a change to anything in the validity key list (lockfiles, toolchain pins, harness,
workflows, thresholds, scope, the selector) selects everything and the baseline is not used.

Unknown is never "not affected". A file the selector cannot map to a function (a macro expansion, generated code, a rename the diff
cannot follow) selects everything in that file; a diff it cannot parse selects everything.

### 6.3 What the coverage map costs

Rule 3 needs, from the FULL run, which tests execute which functions. The coverage tools already run in the FULL (`cargo-llvm-cov`,
`go test -coverprofile`); recording per-test coverage means running tests one by one (or with the per-test profile support of the
tools), and storing the map as an artifact named in the record. This is measured, not assumed: the first deliverable of that part
is the cost in time and size, before it is wired into anything.

If the map cannot be had cheaply for a language, that language's DELTA falls back to rule 4 for any change outside the function that
changed (that is, it selects every mutant of the file), which is weaker in speed and not in soundness.

### 6.4 The result

DELTA reports: the selected mutants, the carried ones, the merged counts, the score the gate would compute, the baseline used, the
validity key, and for each carried mutant the reason it was safe to carry. The score is computed by the same function as the gate
(`tools/quality_gate.py` gains the merge; it does not get a second copy of the arithmetic). Line and branch coverage are not
carried: they are cheap and run in full in a DELTA as well.

### 6.5 What DELTA never does

* It never produces a PASS from a baseline that failed verification (5.5) or is not an ancestor.
* It never carries a verdict across a change of the validity key.
* It never lowers a threshold or applies one of its own; it feeds the existing gate.
* It never runs on `main` as a substitute for the daily FULL.

## 7. Proving the selector: self-tests

The reason for this part is the lesson of the gates built so far: a check that cannot be shown to fail proves nothing, and the
blind spot is often shared between the check and what it checks. The selector gets three kinds of proof.

**7.1 Ground truth.** For a battery of seeded changes to this repository's own code and tests, run FULL on the baseline and on the
changed tree, compute the set of mutants whose verdict **differs** (including mutants that appear and disappear), and require
`selected` to contain that whole set. This is the soundness test, and it is the only one that can show the selector wrong. It is
expensive, so it runs on a schedule and when the selector or the harness changes, not on every PR. The battery covers at least:

* a changed line in a function; a new function; a deleted function; a function moved within and between files;
* a changed callee (rule 3) with an unchanged caller whose mutants are killed through it;
* a changed test; a new test; a deleted test; a changed `testdata` or `corpus` file;
* a change that does not change behaviour (a comment, formatting): the selected set may be smaller, never wrong;
* a lockfile, toolchain, harness, workflow or threshold change: must select everything.

**7.2 The evidence itself.** Negative probes that each must end in "no valid record" and therefore a FULL: a record with one byte
changed; a wrong file name; a valid record signed by another workflow identity; a baseline that is not an ancestor; one with another
validity key (one probe per class of the key's inputs); an expired one; a failed gate verdict; a record whose per-mutant
verdicts have a duplicate or an unknown id; a missing artifact. The harness breaks a real record in each way and requires the
verdict, as `tools/parity/selftest.py` does.

**7.3 The comparison with FULL, in shadow.** See section 8: for each PR the real FULL runs next to the DELTA and the two are
compared. This is not proof, it is the measured agreement over time.

Every probe has the form "break something, require red". A selector or a verifier that passes all its probes is a candidate; one
that has no probe for a case is not trusted for that case.

## 8. Shadow mode

A job `delta-shadow` on pull requests (not required for merge), after the existing FULL:

1. find the newest record on `ci-evidence` whose commit is an ancestor of the PR head and that verifies (5.5); none means "no
   baseline", the job reports that and ends green (this is information, not failure);
2. compute the selection; run the selected mutants with the existing shard tooling; merge;
3. compare the DELTA's verdict and score with the FULL's of the same run, mutant by mutant where both ran;
4. write a report (job summary and an artifact): baseline, selected count out of total, time it took against the FULL, agreement.

The job is red on exactly one thing: **an unsafe disagreement**, a mutant that FULL found surviving and that DELTA carried as
killed, or a DELTA PASS where FULL is FAIL. A disagreement in the safe direction (DELTA ran more than needed, or reported a failure
FULL does not) is recorded, not red. Because the job is not required, an unsafe disagreement does not block a PR, but it is the
signal that stops the work from going on.

The existing FULL gates are untouched.

## 9. Order of work, each its own PR

| Step | Content | Wiring into CI |
|---|---|---|
| A | The record format, canonicalisation, the validity key computation, the verifier, the negative probes (7.2). No evidence is produced yet. | the self-test in `p1` only |
| B | The daily FULL workflow, the `ci-evidence` branch and its protection, the attestation, the record on `main` pushes. | a new scheduled workflow |
| C | Stable mutant ids (5.4) in `quality_gate.py` and the two shard tools; the per-mutant verdicts in the record. The completeness check keeps working. | `p1` writes them |
| D | The per-test coverage map and its measured cost (6.3). | measurement only |
| E | The selector as a library with the ground-truth battery (7.1). No CI wiring beyond the scheduled battery. | scheduled |
| F | `delta-shadow` (section 8). | a non-required job |
| G | A measurement window, then a decision by the owner on what DELTA may be used for, if anything. Out of scope here. | none |

A and B are useful by themselves (verified evidence of a FULL pass, a daily FULL) and do not depend on DELTA being possible at
all. C is the step with the most uncertainty and may show that stable ids for Go cost more than the speed is worth; the plan
says so now so that the finding is not a surprise.

## 10. Open decisions for the owner

Each has a proposed default, which is what the plan assumes until answered.

1. **Validity window** of a record. Proposed: 3 days for use as a DELTA baseline (the daily FULL then covers a weekend plus a
   missed run), with the record kept for 90 days as history.
2. **Who can push `ci-evidence`.** Proposed: only the evidence workflow, through a branch ruleset that requires no pull request but
   allows only that workflow's identity; humans read it.
3. **Attestation tooling.** Proposed: GitHub artifact attestations (`actions/attest-build-provenance`, verification with
   `gh attestation verify`), pinned by version like the other tools.
4. **Whether to store the coverage map in the record or only a digest** and the map as an artifact. Proposed: digest and artifact
   (the map can be large).
5. **Mutant identity for Go** (5.4): whether to accept a heavier id or to leave Go on FULL for mutation if the id proves too costly
   (see step C).
6. **Where the speed is worth the machinery.** After sharding the FULL is 10 to 29 minutes, and the 29 is a balance problem
   (mutants that time out cluster in one Rust shard). Cheaper than DELTA, and independent of it: shard by estimated cost or lower
   the per-mutant timeout, cache the installed tools (about 3 minutes per job), more Go shards. They do not touch the evidence
   chain and could be done first; the owner decides whether they come before step C.

## 11. What does not change

The thresholds (strictly above 90%), the full PR gates and the FULL gate on `main`, the engine, Relay, the registry, the ADRs. DELTA
in shadow mode decides nothing. Until the owner says otherwise a merge needs a green FULL, as today.
