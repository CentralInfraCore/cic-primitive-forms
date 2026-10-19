# Test evidence: the contract of step A

Status: **implemented, not wired to any evidence source.** This is step A of `docs/FULL-DELTA-PLAN.md`: the record
format, the canonical JSON it is written in, the validity key, the verifier and the rule that picks a baseline, with
the probes that break each trust boundary. Nothing here produces, publishes or signs a record; that is step B, a
separate review (the `ci-evidence` branch, the GitHub App, the ruleset, the attestation workflow, the daily FULL).

Code: `tools/test-evidence/`. Run it: `python3 tools/test-evidence/evidence.py selftest` (unit tests and probes, and the
Go implementation on the vectors), `python3 tools/test-evidence/seeded.py` (the probes against deliberately wrong
verifiers). Job `evidence-selftest` of workflow `p1` runs both.

## Canonical form (`canon.py`, `canon-go/`)

One declared byte format: RFC 8785 restricted to an I-JSON subset. UTF-8 without a byte order mark; no insignificant
white space; member names sorted by UTF-16 code unit (not by code point: `U+1F600` sorts before `U+FFFF`); strings
escaped as RFC 8785 says (`\u00xx` in lower case for the other control characters, everything else as is, `/` not
escaped); **integers only**, in +-(2^53-1), with no exponent, fraction, leading zero or `-0`; no `NaN` or infinity; no
lone surrogate; no duplicate member name; at most 64 containers deep.

The reader is strict. `parse_strict` takes any document of that subset; `read_canonical` takes only a document that is
byte for byte its own canonical form (a valid document with another order, a space or an escaped `A` is `NOT_CANONICAL`).
Every refusal has a class: `EMPTY BOM INVALID_UTF8 DUPLICATE_KEY LONE_SURROGATE CONTROL_CHAR ESCAPE TRAILING SYNTAX
NUMBER DEPTH`.

**Two independent implementations** (`canon.py` and `canon-go/main.go`; they share no code and no parser) read the same
63 vectors in `vectors/canon.json` and must give the same canonical bytes and the same class of refusal for each. The
vectors are typed by hand (`make_vectors.py`), not computed by either implementation. A record written by the Python
tool is also fed to the Go program and must come back byte for byte.

## The record (`record.py`)

Schema `test-evidence/1`, as section 5.1 of the plan, with these settled in the implementation:

* `validity_inputs` carries the named digests the key was made of, next to `validity_key`.
* `run` also names `commit`, the commit of the producing run: the attestation is checked against it (`--source-digest`).
* Every object has exactly its members, at every level: a missing or an extra member is refused (`MISSING`, `EXTRA`).
* The verdict is **worked out from the counts**, not taken on the record's word. A measurement passes if it is strictly
  above its threshold in integers (`covered * 100 > total * threshold`, with `total > 0`): exactly 90%, below 90% and a
  measurement of nothing are not a pass. Mutation is `killed / (killed + survived)`; unviable and uncompilable mutants
  count for nothing. This is the semantics of `quality_gate.py` and `coverage_gate.py`. `gate_verdict` must be `PASS`
  exactly when all six measurements pass and the shards are `complete`, in both directions.
* Consistency: `outcome` is `pass` only if the gate said `PASS` and **every** required check concluded `success` (not
  `skipped`, not `cancelled`); `anchor` is true exactly when the outcome is `pass`; a pass is measured against thresholds
  of at least 90; only a record of `main` has a slot; a record is made by the run on the commit it names (the merge
  commit, for a merge candidate); a merge candidate names its base and head; coverage never exceeds the total; no check
  and no artifact twice.
* The environment holds **resolved versions** (`1.99.0`, `go1.27.2`, a 40-hex commit for each action), never a label:
  `stable`, `ubuntu-latest` and `v4` are refused.
* The checks of the record are exactly those of `required-checks.json` (given to `validate`): one more, one fewer or
  a renamed one is `CHECKS`.

(`required-checks.json` and `key-inputs.json` are JSON, not YAML, so that the verifier needs no YAML parser.)

## The validity key (`key.py`, `key-inputs.json`)

The digest of: every file selected by `key-inputs.json` (lockfiles, `coverage/**`, `tools/**`, `corpus/**`,
`testdata/**`, `.github/workflows/**`, `.github/actions/**`, minus generated files), the resolved environment, the
pattern list itself, the selector version, the schema. Product code is not in it. The list is part of the key, so a
pattern added to it invalidates every older record. It fails closed: a pattern that selects nothing (`NO_MATCH`), a
symlink or other non-regular file that is selected, **a symlink to a directory in a selected tree or above a selected
file** (the walk lists it among the directories and does not enter it, so without a check it would be skipped in
silence and what is under it would not be in the key), an unreadable file, an environment with a label (`ENVIRONMENT`)
is an error, not a smaller key (`NOT_A_REGULAR_FILE`). A link that nothing selected can be under, or one in an excluded
place such as `rust/target`, is no concern of the key.

## Verification (`verify.py`)

`verify_record` (a genuine record) and `verify_baseline` (fit for a target) refuse with a code, in this order:

| Step | Code |
|---|---|
| bytes are canonical JSON | `NOT_JSON`, `NOT_CANONICAL` |
| the file name is the hash of the bytes | `NAME_MISMATCH` |
| the schema and its consistency rules | `SCHEMA` |
| the tree the record names is the tree of its commit, in this repository (`git`, asked for a full commit id only) | `TREE_MISMATCH`, `TREE_UNKNOWN`, `TREE_UNCHECKED` |
| a merge candidate: the commit is the merge of exactly the base (first parent) and the head (second parent) it names | `MERGE_BINDING` |
| the attestation, by `gh attestation verify` with `--repo`, `--signer-workflow`, `--source-ref`, `--source-digest` | `ATTESTATION` |
| a baseline: outcome `pass`, anchor, a record of `main` | `NOT_A_BASELINE` |
| the key written in the record is the digest of its inputs | `KEY_INCONSISTENT` |
| the key equals the key of the target now | `KEY_MISMATCH` (names what changed), `KEY_UNAVAILABLE` |
| the commit is the target or an ancestor of it | `NOT_ANCESTOR` |
| not older than 3 days, not from the future | `EXPIRED` |
| the artifacts exist and match digest and size | `ARTIFACT_MISSING`, `ARTIFACT_DIGEST` |

`GitRepo` answers the git questions (`tree_of`, `parents_of`, `is_ancestor`) by running git, and passes nothing to it but
a 40-hex commit id that is a commit: `HEAD`, a branch, an option, an abbreviation or a tree id is not an answer. Without
a way to ask git the verifier has no answer (`TREE_UNCHECKED`).

The attestation is a boundary behind an interface (`Attestation`); `GhAttestation` is the one that runs `gh`. Anything
but a clean exit is a refusal, including a missing `gh`, a timeout and a `gh` that does not know the command; the
interface must answer exactly `True`. The flags are not taken on trust: job `evidence-selftest` runs `evidence.py
check-gh`, which asks `gh attestation verify --help` and fails if one of the four flags is not listed. (The `gh` of
the development machine is too old to have the command, which is why this is checked in CI and the unit tests use a
`gh` that insists on the exact argument list.)

## Which record may serve (`choose_baseline`, `load_and_choose`)

Section 5.7 of the plan. A baseline needs a record for the latest due slot (06:00 UTC), a newest record that is a
passing anchor, a passing anchor that no failure follows, at most 3 days old, an ancestor of the target. After a
failure, a missed run or a record that does not verify, nothing is usable (`NEWEST_NOT_PASS`, `SLOT_MISSED`,
`UNCERTAIN`): the answer is a FULL run, never an older success. Every record the index lists must be present and genuine,
or the state is uncertain.

## The probes

`test_evidence.py`, 43 tests. Each trust boundary has a probe that breaks it and requires the refusal: a changed byte, a
non-canonical document, a duplicate member, a wrong name; an attestation that is false, truthy-but-not-`True`, for
another repository, workflow, ref or commit; every class of the key's inputs changed (lockfile, tool pin, harness file,
workflow, corpus, testdata, rustc, runner image, action revision, tool version, the selector version, the pattern list);
a key that does not match its inputs; a commit that is not an ancestor; a record of exactly 3 days (allowed), older, and
from the future; a missing artifact, a wrong digest, a wrong size; the thresholds in integers (above, exactly 90, below, nothing measured,
90.0001 and 89.9999 percent, for each of the six measurements, a verdict that the numbers do not give, in both
directions); the tree binding against a real git repository (a tree of another commit, a tree id given as a commit, a
commit that does not exist, swapped or wrong parents of a merge candidate, `HEAD` and `--help` as an argument); a symlink
to a directory in each selected tree; and the chain with seeded histories (a pass then a
failure, a missed slot, a pass after a failure that is not an ancestor, records of the same second that disagree, a
listed record that is missing, tampered with or not attested).

`seeded.py` applies 55 deliberate defects to a copy of the verifier (a check removed, a comparison weakened, an input
dropped from the key) and requires the tests to fail for each. One defect it found at the first run was not caught:
the filter that keeps a pass from before a failure out of the candidates looked redundant because the newest record
must pass anyway; the probe `test_a_pass_before_a_failure_is_not_a_fallback` was added for the case where it is not.

## What is not here

* Producing, signing, publishing a record, the `ci-evidence` branch, the App and the ruleset, pinning the actions by
  SHA, the daily workflow: step B. The trust expected of an attestation (`Trust`: repository, signer workflow path
  `.github/workflows/evidence.yml`, ref `refs/heads/main`) is a default that B has to make true; the isolation of the
  App token and the binding of the attestation to the workflow are B's closing conditions.
* The index (`index/<date>.json`) is read as a list of what to look at; its completeness rests on the protection of the
  branch and, in B, on attesting it. The verifier cannot see a record that was left out of it.
* The per-mutant verdicts, their ids and the probe "a duplicate or unknown mutant id": step C. Here `mutants` and
  `coverage_map` are references (digest, size, artifact) whose artifact is checked, nothing more.
* Any DELTA selection: steps D to F.
