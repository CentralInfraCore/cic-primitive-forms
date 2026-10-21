# Setting up test evidence publishing (owner actions)

These are repository-admin actions. Nothing in the repository does them on its own.

## 0. Protect `main` first (a precondition, not an option)

The environment `evidence` only lets `refs/heads/main` use the App's credentials, but **any workflow that gets onto `main` can
use them**, and the ruleset gives the App a bypass. So the App's write access is exactly as safe as the review of what
reaches `main`: require a pull request (and a review) for `main`, and a review by a code owner for `.github/workflows/**`
(`CODEOWNERS`). `apply.sh` reads `repos/<repo>/rules/branches/main` and refuses to go on without a `pull_request` rule;
`ALLOW_UNPROTECTED_MAIN=1` overrides it, on your decision and with the consequence above.

## 1. Create the GitHub App

Settings > Developer settings > GitHub Apps > New:

- name `cic-evidence-writer`, no webhook, no callback;
- permissions: **Repository > Contents: Read & write** and nothing else;
- install it on **this repository only**;
- generate a private key (`.pem`) and note the **Client ID**.

## 2. Apply the settings

```
CLIENT_ID=<client id> KEY_FILE=<key.pem> tools/test-evidence/setup/apply.sh            # prints what it would do
CLIENT_ID=<client id> KEY_FILE=<key.pem> tools/test-evidence/setup/apply.sh --apply
```

It creates the environment `evidence` (deployment branch policy: `main` only), sets the variable `EVIDENCE_APP_CLIENT_ID` and the
secret `EVIDENCE_APP_PRIVATE_KEY` in it, and creates the ruleset of `tools/test-evidence/setup/ruleset-ci-evidence.json` (the
branches `ci-evidence` and `ci-evidence-probe`: no creation, update, deletion or non-fast-forward, except for the App). With
`--apply --pin-actions` it also turns on `sha_pinning_required`. It never sets `EVIDENCE_ENABLED`.

## 3. Run the probes (they do not need `EVIDENCE_ENABLED`)

The probes run before the evidence is switched on - they are the condition for it - and a test keeps them from being gated by
the variable. Run the workflow `evidence-probes` by hand on `main`. Both jobs have to be green:

- `branch`: the `GITHUB_TOKEN` is refused when it creates, updates, force-pushes or deletes the throwaway branch
  `ci-evidence-probe`; the App can create it; the App cleans it up;
- `attestation`: a decoy attested by the probes workflow does **not** verify as `evidence.yml`, but verifies as `evidence-probes.yml`.

Then the isolation probe, which is what the two jobs above do not show (they prove the `GITHUB_TOKEN` cannot write, not that
another workflow cannot obtain the App's credentials):

```
tools/test-evidence/setup/probe-isolation.sh
```

It pushes a throwaway branch with a workflow that asks for the environment `evidence` and requires that the job does **not**
run (`PROBE PASSED`); it deletes the branch afterwards. A job on `main` that is not `evidence.yml` is *not* stopped by anything
but step 0; that is a review matter, not a probe.

## 4. Switch it on

```
gh variable set EVIDENCE_ENABLED --repo CentralInfraCore/cic-primitive-forms --body true
```

Then dispatch `evidence` once by hand and read the `record` job through to "read back".

## 5. When an evidence run fails (the first one or any later one)

Fails means any of: a step of the `record` job ends red (collect, build, attest, publish), the read-back
(`verify-published`) is red, the attestation does not verify, or `watch` reports a slot without a record.

### 5.1 Switch it off at once

```
gh variable set EVIDENCE_ENABLED --repo CentralInfraCore/cic-primitive-forms --body false
```

This is the whole switch: both jobs of `evidence.yml` start only if `vars.EVIDENCE_ENABLED == 'true'`, so no new
`record` or `watch` job starts after it. It does **not** stop a job that is already running (the condition is read when the
job is queued); cancelling it is a separate owner decision, and the states in 5.2 are what a cancelled run can leave.
Nothing else is changed by switching off: the branch, the ruleset, the environment and the secrets stay as they are.

### 5.2 What may be left behind

| Where the run stopped | Left behind | Effect on readers |
|---|---|---|
| before the attestation (collect, build) | nothing | none; there is no record for that run |
| after the attestation, before or during the publish | an attestation of a record digest that is **not** on `ci-evidence` | none: readers start from the index of `ci-evidence`, never from the attestation list, and a record is checked as bytes whose file name is their hash |
| the publish was refused (`PUSH`) or lost the race five times (`RACE`) | the same: an attestation, no record | none; `publish.py` pushes without force and writes the record file and its index entry in **one commit**, so the branch holds both or neither |
| the publish succeeded, the read-back is red | a record **on the branch and in the index** that the verifier does not accept (`UNVERIFIED`, `MISSING_RECORD`, `KIND`) or that is not the one just published (`NOT_PUBLISHED`) | every later read of the last days fails on it, see 5.4 |
| the FULL run itself failed (a gate was red) | a **valid** record with outcome `fail`, filed under `evidence/audit/`, in the index | not an infrastructure failure: the read-back accepts it, and the chain rule treats it as a failure (5.4) |

An attestation cannot be taken back by this tooling, and none of the tooling lists attestations. A record file that is on
the branch but in **no** index is not found by `verify-published`, `watch` or the chain rule, which all go through
`index/<date>.json`.

### 5.3 How to check, read-only

Nothing here writes to GitHub. You need `git`, `python3`, a full clone of the repository on `main` (the verifier asks git
about old commits), and a `gh` that has `gh attestation verify` (the `gh` 2.45 of an old development machine does not, and a
`gh` that does not know the command makes **every** record refuse with `ATTESTATION`, which is the intended fail-closed behaviour,
not a finding about the record).

```
git clone --depth 1 --branch ci-evidence https://github.com/CentralInfraCore/cic-primitive-forms.git "$TMP/evidence-branch"
cd <full clone on main>
export GITHUB_SHA=$(git rev-parse HEAD) GH_TOKEN=<a read token>
python3 tools/test-evidence/evidence.py watch --evidence "$TMP/evidence-branch" --root .
python3 tools/test-evidence/evidence.py verify-published --evidence "$TMP/evidence-branch" --root . --record <hash>.json
```

What they do (`pipeline.py`, `verify.py`):

- `verify-published` reads `index/<date>.json` for today and the three days before, and for **every** `(hash, kind)` an index lists it
  fetches `evidence/full/<hash>.json` or `evidence/audit/<hash>.json` and runs `verify_record`: canonical JSON, the file name is the
  hash of the bytes, the schema and its consistency rules, the tree the record names is the tree of its commit, a merge
  candidate is the merge of its base and head, and the attestation (`gh attestation verify` for this repository, signer workflow
  `.github/workflows/evidence.yml`, source ref `refs/heads/main`, the record's commit). It also requires `full` to hold
  exactly the passing records and `audit` the failing ones, and that `--record` is among the verified ones. The first record that
  fails stops it (`MISSING_RECORD`, `UNVERIFIED` with the code of the verifier, `KIND`, `NOT_PUBLISHED`), and it exits 3. It does not
  check the validity key, the ancestry or the age: those belong to choosing a baseline, not to a record being genuine.
- `watch` runs the same verification of the last four days (`pipeline.watch`), then answers whether a record of `main` carries
  the latest due slot (the day whose 06:00 UTC has passed). A record with outcome `fail` also fills a slot. Exit codes
  (`evidence.py`, `cmd_watch`):
  - **0** - a verified record for the latest due slot; it prints `slot YYYY-MM-DD: has a record`.
  - **3** - no record for it; it prints `slot YYYY-MM-DD: has no record`. This is the only answer that asks for a run.
  - **4** - undecided: the verification raised a `PipelineError` (a listed record is missing, does not verify, is filed under
    the wrong kind, ...). The message goes to standard error and no `slot ...` line is printed.

  The `watch` job of `evidence.yml` acts on exit 3 only, and only when the output has a line exactly `slot YYYY-MM-DD: has no
  record` with a non-empty date: then it runs `gh workflow run evidence.yml --ref main -f slot=<that date>`. On exit 3 without such
  a line, on 4 and on any other failure it fails the job (`::error::`) and asks for nothing: a record that is there and does not
  verify is looked at, not answered with another run. This is why 5.1 comes first.
  One detail, stated as it is: a `PublishError` raised inside `watch` (a malformed `index/<date>.json`, a file that cannot be read)
  is not caught by `cmd_watch`; the generic handler of `evidence.py` prints it to standard error and exits 3. The job still stops
  safely, because no `slot ... has no record` line was printed and exit 3 without that line fails the job. Mapping it to 4 as well
  would be a later maintenance item, not a change of what the dispatch does.

### 5.4 What is allowed, and recovery

Allowed by the design:

- Publishing **adds**; it never forces. A record is never edited. `publish.py` refuses a file of the same name with other
  content (`CONFLICT`) and an identical publish is a no-op.
- The ruleset on `ci-evidence` refuses creation, update, deletion and non-fast-forward for everyone except the App; nobody
  deletes a record or an index entry, and a failure is never hidden by removing it.

What the chain rule says (`docs/TEST-EVIDENCE.md`, "Which record may serve"; `verify.choose_baseline` and `load_and_choose`):

- A baseline needs a record for the latest due slot, a **newest** record that is a passing anchor, a passing anchor that no failed
  record follows, at most three days old, and an ancestor of the target.
- After a failure, a missed run, or a listed record that is missing or does not verify, **nothing is usable**: the answer is a
  FULL run, never an older success, and the state is *uncertain* rather than repaired by skipping the record.
- The documents define no other way out. A failed record is *followed* by a later passing anchor (later `measured_at`, within
  three days, an ancestor of the target); that record, not an edit, is what makes the baseline usable again.

So, in practice:

1. **A failed FULL (outcome `fail`)**: leave the record. Fix the cause on `main` through a pull request; the next green FULL
   (the next daily run, or a manual dispatch) is the record that follows it.
2. **A run that stopped before or during the publish**: there is nothing to repair on the branch. Dispatch the run again once the
   cause is fixed. The orphan attestation stays and is harmless.
3. **A bad record that *is* listed** (read-back `UNVERIFIED`, `MISSING_RECORD`, `KIND`): the code has no operation for it and this
   document does not invent one. The index of the day lists it, and `verify-published` reads the indexes of the last four
   days, so each later `record` job's read-back stays red until the day has aged out, and DELTA must not be used meanwhile
   (it is not built yet, so nothing consumes the chain today). Treat it as an incident: find out why it does not verify
   (the verifier names the code), fix that cause, and decide with the owners; do not delete or rewrite anything on `ci-evidence`
   to make the check pass. Any action on the branch beyond adding records is an owner decision outside this tooling, and is
   not claimed safe here.

### 5.5 When it is safe to switch on again

Not before the cause is understood and fixed on `main`, and then the same checks as the first switch-on, in this order:

1. The probes are green on the current `main`: both jobs of `evidence-probes` and `probe-isolation.sh` (step 3), and `main`
   is still protected (step 0). If `evidence.yml`, `publish.py` or the setup changed, run them again.
2. `watch` and `verify-published` (5.3) are green on the current branch, or the bad record of 5.4 point 3 has aged out of the last
   four days of indexes.
3. `gh variable set EVIDENCE_ENABLED ... --body true`, then **one** manual dispatch of `evidence`, and its `record` job is green through
   "read back".
4. Only then leave it on for the schedule. If that dispatch fails, go back to 5.1.

## Residual risks (known, not fixed here)

- Any workflow **on `main`** can use the environment `evidence`. Protect `main` and require review of `.github/workflows/`
  (CODEOWNERS) - `main` is unprotected today.
- The App bypasses the ruleset "always", so a force push by the App is prevented by the code of `publish.py`, not by GitHub.
- The reviewer's closing condition for B - token issuance isolation and the attestation bound to the workflow identity - is what
  step 3 checks against the real service (isolation from other *branches*), and step 0 covers (isolation from other workflows
  on `main`, which no probe can show).
- The completeness of the index rests on the ruleset; the authenticity of a record rests on the attestation.
