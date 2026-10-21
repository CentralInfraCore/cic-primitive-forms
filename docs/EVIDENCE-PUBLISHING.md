# Publishing test evidence: step B2

Status: **implemented, switched off.** `evidence.yml` does nothing until the owner has done `docs/EVIDENCE-SETUP.md` and set the repository
variable `EVIDENCE_ENABLED=true`. B2 is the second of two pull requests for step B of `docs/FULL-DELTA-PLAN.md`; B1
(`docs/EVIDENCE-PRODUCTION.md`) builds a record from a run, B2 collects the run, attests the record, publishes it and watches
that it keeps being published. The record, the verifier and the chain rule are step A (`docs/TEST-EVIDENCE.md`).

Code: `tools/test-evidence/` (`collect.py`, `publish.py`, `pipeline.py`, subcommands of `evidence.py`), the workflow
`.github/workflows/evidence.yml`, the owner-run `evidence-probes.yml` and `tools/test-evidence/setup/`.

## The flow (`evidence.yml`, job `record`)

1. **collect** - find the `p1` and `corpus` runs for the commit (a push to `main` already started them; the daily run
   dispatches them), wait for them, take their jobs and artifacts. A run of another commit, a missing run, an expired or a
   duplicated artifact, a timeout: each is its own error and no record is made.
2. **build** - the B1 builder makes `test-evidence/1` from those, with the target, run and slot from `pipeline.make_inputs`.
3. **attest** - `actions/attest-build-provenance` over the record file, in this workflow, with the job's own OIDC identity.
4. **publish** - only now the GitHub App token is created (`create-github-app-token`) and only the publish step holds it.
   The record goes to `evidence/full|audit/<hash>.json` and the day's `index/<date>.json` on the branch `ci-evidence`.
5. **read back** - the branch is checked out again and every record of the last days is verified with the verifier of step A,
   attestation included, as a reader would.

Job `watch` (07:47 UTC, after the slot was due at 06:00) dispatches `evidence.yml` for the slot if the latest due slot has no
verified record. `evidence.py watch` answers with exit 0 (a record), 3 (no record: the only answer that asks for a run, and only with
the date of the slot) or 4 (undecided: the branch cannot be read, or a listed record does not verify). Exit 4, or any other failure,
fails the job and asks for nothing: a record that is there and does not verify is looked at, not answered with another run.

## Slots and triggers

| Event | Slot | Trigger in the record |
|---|---|---|
| `schedule` (03:17 UTC) | today's UTC day | `dispatch` |
| `workflow_dispatch` | the validated `slot` input, or none | `dispatch` |
| `push` to `main` | none; a slot with a push is refused | `push` |

A slot is a real UTC day, not in the future, not older than 3 days. Only `refs/heads/main` makes evidence, and the workflow ref
has to be this repository's `.github/workflows/...`.

## Publishing never forces anything

- the token reaches git only as `GIT_CONFIG_COUNT/KEY_0/VALUE_0` (`http.extraheader`) in the environment, never in an argument;
- no force push; a push rejected because the branch moved is retried from a fresh checkout (5 times, then `RACE`); any other
  refusal is `PUSH`;
- the same record twice is a no-op; a file of the same name with other content is `CONFLICT`.

## What is proven and what is not

Proven by tests (`test_collect`, `test_publish`, `test_pipeline`, `test_workflows`; 93 seeded defects in `seeded.py`):
the rules above, and the structure of the workflows - who can start them, their permissions, that the App's key is referenced
once, that only the publish step holds the token, that the verifier's expected signer is `.github/workflows/evidence.yml` on
`refs/heads/main`, and that no `run:` interpolates an expression.

Not provable here: that GitHub enforces the ruleset and the environment's branch policy, and that an attestation of another
workflow does not verify as `evidence.yml`. `evidence-probes.yml` tests both against the real service. **Until the owner has run
it, these two are unverified.**

The cryptographic boundary is the read side: the verifier asks `gh attestation verify` for the signer workflow, the source ref and
the digest. The ruleset on `ci-evidence` only keeps the *index* complete, because only the App can write the branch.
