# Producing test evidence: step B1

Status: **implemented, nothing is published or signed yet.** This is the first of two pull requests for step B of
`docs/FULL-DELTA-PLAN.md`. B1 is everything that can be built and proved without a GitHub App, a ruleset or a
branch to write to: the actions pinned to commits, the raw reports and the environment the measuring jobs leave behind, and
the builder that turns a run into a record. B2 publishes, attests and sets up the permissions, and is reviewed on its own
(section "What B2 is"). The record, the verifier and the chain rule are step A (`docs/TEST-EVIDENCE.md`).

Code: `tools/test-evidence/` (`pins.py`, `envcap.py`, `build.py`). Run it: `python3 tools/test-evidence/evidence.py selftest`
and `python3 tools/test-evidence/seeded.py`; job `evidence-selftest` of workflow `p1` runs both and `evidence.py pins`.

## Pinned actions

A tag such as `v4` is a label its owner can move, and the validity key of a record carries the revision of every action. All
five external actions of the four workflows and of the composite action are now `owner/repo@<40-hex commit>  # <version>`
(`checkout` 4.4.0, `setup-go` 5.6.0, `setup-python` 5.6.0, `upload-artifact` 4.6.2, `download-artifact` 4.3.0; each commit is
the one both the major tag and the exact release tag pointed at when it was resolved). `evidence.py pins` fails on any `uses:`
under `.github/` that is not local or not a full lower case commit id (a tag, a branch, a short or upper case id, an image, no
revision), and on one action at two revisions. The version in the comment is for people and for Dependabot; the commit is the
identity.

Not done here, and for the owner: the repository setting **"Require actions to be pinned to a full-length commit SHA"**
(`sha_pinning_required`, currently `false`) would make GitHub itself refuse an unpinned action, including one added by a pull
request; it is a repository setting, not a file, and it complements the check above.

## What the measuring jobs leave behind

Additive uploads, none of them changes a gate, a threshold or a verdict; the steps that gather the environment run with
`continue-on-error`, so a failure of the evidence plumbing never turns a gate red (it only means there is no record, which the
chain rule counts as a missed run).

| Artifact | From job | Holds |
|---|---|---|
| `lines` | `quality-lines` | `rust-lines.json`, `go-lines.out` (existing) |
| `rust-mutants-N` | `quality-mutation-rust` | `outcomes.json` of the shard (existing) |
| `go-ledger-N` | `quality-mutation-go` | the shard's ledger (existing) |
| `quality-listed` | `quality` | `rust-listed.json`, the mutants the Rust shards are checked against |
| `branch-reports` | `branch-coverage` | `rust-report.json`, `go-report.json`, uploaded also when the gate failed |
| `env-quality-lines`, `env-rust-mutants-N`, `env-go-mutants-N`, `env-fuzz` | the same jobs | `env.json`: the part of the environment that job is responsible for |

## The environment (`envcap.py`)

Each job writes only what it knows, as the tools report it and for the toolchain it runs (`cargo +stable ...`, the way the
job calls it; the default toolchain of a runner is not the one that ran): `rustc -vV`, `cargo -V`, `go version`, the Python
version, the versions of `cargo-llvm-cov`, `cargo-mutants`, `cargo-fuzz`, the module version of the installed `go-mutesting`,
and the runner (`ImageOS`, `ImageVersion`, `RUNNER_OS`, `RUNNER_ARCH`). A value that is not a version (`stable`, `latest`) is
`UNRESOLVED`; a command that is missing or fails is `UNRESOLVED`; nothing is guessed. `merge` puts the parts together and adds
the action revisions of the workflows: a field two jobs report differently is a `CONFLICT` (so a runner image that changed
between two jobs of one run means no record), a field nobody reports is `INCOMPLETE`, an unpinned action is `UNPINNED`.

## From a run to a record (`build.py`)

Inputs: the jobs of the run (name, conclusion, URL, run id), the downloaded artifacts, the checkout of the target, the target
and the run (commit, tree, base and head for a merge candidate), the slot, the time. Output: the record, its canonical
bytes, its file name (the hash) and its kind, `full` for a passing anchor or `audit` for a failed run.

* **Required checks.** One entry for each name of `required-checks.json`, from the job of that name; a check that did not run
  (`MISSING_JOB`), ran twice (`AMBIGUOUS_JOB`) or did not finish (`JOB_NOT_FINISHED`) is an error. `skipped` and `cancelled` are
  conclusions of their own and make the run a failed one.
* **The numbers are worked out again** from the raw reports, with the gate's own functions (`quality_gate.rust_lines`,
  `go_lines`, `rust_mutants` with the list, `go_ledgers`), not read off a log. The branch counts come from the reports of the
  branch-coverage job, which have to be of the same commit, language and metric. Incomplete shards, a mutant tested twice, a
  missing report: `INCOMPLETE_MEASUREMENT`, `UNMEASURED`, `MISSING_ARTIFACT`; there is no record.
* **The verdict is the numbers'**, and the gate has to agree: the `quality` job concluding `success` while its numbers say fail,
  or `failure` while they pass, is `GATE_DISAGREES` (the same for `branch-coverage`) and no record. Nobody knows which is right.
* The record is validated by step A's `record.validate` before it is returned, so a record that cannot pass the verifier is
  never made.

It was checked against the real artifacts of the CI run of `main` at `c9580ef`: Rust lines 1279 of 1296, Go statements 601 of
601, Rust mutation 438 killed and 1 survived, Go mutation 806 killed and 18 survived, four Rust and six Go shards, the numbers
of the gate's own output.

## Proof

`test_pins.py`, `test_envcap.py`, `test_build.py` and the 43 tests of step A: 77 tests; `seeded.py` applies 73 deliberate
defects to a copy of the tools (a check removed, a comparison weakened) and requires the tests to fail for each, among them an
accepted tag, a short id, an image, an action at two revisions, a label for a version, an overwritten conflict, a gate that
disagrees with its numbers and is believed, a branch report of another commit, a job that did not finish counted as a pass,
incomplete shards that still make a record.

## What B2 is

The mandatory FULL workflow `.github/workflows/evidence.yml` (the signer workflow step A's verifier expects), its schedule and
due slots and the watchdog, collecting the run's jobs and artifacts and calling the builder, the attestation, the publishing to
`ci-evidence`, the dedicated GitHub App, the ruleset and the real negative probes (a push by a person's token, by another
workflow, a force push, a deletion, a record attested by another workflow). These need things only the owner can do: create and
install the GitHub App, store its credentials as secrets of an environment restricted to `main`, and create the branch and the
ruleset. B2 will list them exactly and will not create any of them on its own.
