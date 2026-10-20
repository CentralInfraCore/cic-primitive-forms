# Setting up test evidence publishing (owner actions)

These are repository-admin actions. Nothing in the repository does them on its own.

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

## 3. Run the probes

Run the workflow `evidence-probes` by hand on `main`. Both jobs have to be green:

- `branch`: the `GITHUB_TOKEN` is refused when it creates, updates, force-pushes or deletes the throwaway branch
  `ci-evidence-probe`; the App can create it; the App cleans it up;
- `attestation`: a decoy attested by the probes workflow does **not** verify as `evidence.yml`, but verifies as `evidence-probes.yml`.

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
  step 3 checks against the real service. Also check by hand once that a workflow on another branch cannot read the environment's
  secrets.
- The completeness of the index rests on the ruleset; the authenticity of a record rests on the attestation.
