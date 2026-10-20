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

## Residual risks (known, not fixed here)

- Any workflow **on `main`** can use the environment `evidence`. Protect `main` and require review of `.github/workflows/`
  (CODEOWNERS) - `main` is unprotected today.
- The App bypasses the ruleset "always", so a force push by the App is prevented by the code of `publish.py`, not by GitHub.
- The reviewer's closing condition for B - token issuance isolation and the attestation bound to the workflow identity - is what
  step 3 checks against the real service (isolation from other *branches*), and step 0 covers (isolation from other workflows
  on `main`, which no probe can show).
- The completeness of the index rests on the ruleset; the authenticity of a record rests on the attestation.
