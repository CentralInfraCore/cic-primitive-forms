# P5-3: release-origin addendum, `cic-primitives` v0.2.1 (question L)

Status: addendum to `docs/P5-RELEASE-ORIGIN-PRECHECK.md`, 2026-11-02. It reads and measures; it changes no code, creates no release, adds no production receipt API and chooses no trust anchor. The pre-check of 2026-11-01 is **not edited**: it is the measurement of that day, and it was correct then.

**What changed.** On 2026-11-02 `cic-primitives` released `primitives/@v0.2.1`, cut from the pinned commit `73ae469` (the owner's decision of 2026-11-01: a new release from the pin, not a move of the pin). The pre-check's answer, "no release contains the pinned grammar", is therefore **no longer true for the pin `cic-primitives-73ae469-6a188078`**. The last link of the chain, release to Forms pin, **holds, as measured below**. That is a statement about byte identity and lineage. It is not a statement that a receipt may now call a release authentic: the gates in section 4 stay open.

## 1. What was examined, and how to repeat it

Read-only, against `CentralInfraCore/cic-primitives` (a fresh clone with all branches and tags) and the pin in `corpus/pins.yaml` of this repository at `main` `22f0852`:

```
git rev-parse primitives/@v0.2.1 ; git rev-parse 'primitives/@v0.2.1^{commit}'
git merge-base --is-ancestor 73ae469 <source_commit> ; git tag --contains 73ae469
git show <source_commit>:<path> | sha256sum        # for each of the 12 pinned files, compared with corpus/pins.yaml
python3 tools/compiler.py verify-release release/cic-primitives-v0.2.1.yaml
python3 tools/release_record_conformance.py release/cic-primitives-v0.2.1.yaml --repo . --require record,blobs,crypto
openssl x509 -noout -subject -enddate              # for the three certificates in the record
gh release list -R CentralInfraCore/cic-primitives ; gh api repos/.../commits/<release commit> --jq .commit.verification
```

## 2. Findings

| # | Finding | Measured |
|---|---|---|
| 1 | The release is the annotated tag `primitives/@v0.2.1` (tag object `d6615231604edc6c70dbaf784ad4d28761f51fc2`) naming the release commit `5e8c3219b52399e3e764b0b0bb27de710f5445a4`, whose parent is the source commit `106965b8cea7cce2081beb77008753af21e68b46` | `git rev-parse`; the record's `provenance.source_commit` is that source commit |
| 2 | The pin `73ae469` is an ancestor of the source commit and of the release commit; `primitives/@v0.2.1` is the **only** tag that contains it | `merge-base --is-ancestor`; `git tag --contains` |
| 3 | All 12 pinned files at the source commit equal the pin: 12 of 12 by SHA-256 of the blob bytes, against **this repository's** `corpus/pins.yaml` (not a copy) | recomputation |
| 4 | The record is `kind: PrimitiveRelease`, **envelope 3**. It carries its own digest of 30 declared release artifacts in `artifacts.files` as `sha256:<64 lowercase hex>` of the raw blob bytes; the 12 pinned files are among them and equal the pin: 12 of 12 | the record; recomputation. The record layer, the blobs layer and the two signatures of the same record pass the reference verifier of the record contract (`--require record,blobs,crypto`, exit 0), read from a fresh clone of the remote tag |
| 5 | `provenance.grammar_sha256` and `grammar_schema_sha256` are still **base64** SHA-256, and equal the pinned `check_grammar.py` and `instance-grammar.schema.yaml` after the re-encoding to hex. `release.build_hash` is also base64 (44 characters) | recomputation |
| 6 | The signatures verify: schema valid, `build_hash` recomputed and equal, the release signature, the pledge signature and the CIC Source CA countersignature over `build_hash`, and the authority certificate chains to the root **carried in the record** | upstream `verify-release`, run **without** an external anchor |
| 7 | The same verifier states what it does not prove: no external trust anchor was supplied, so every certificate is self-asserted; the developer pledge travels separately; the artifact list is checked by the reference verifier, not by `verify-release` | its "NOT proven" lines |
| 8 | The release signature and the countersignature verify under distinct public keys: the release issuer certificate (the developer) and the CIC Source CA certificate. Their operational separation into different Vault instances is not established by the release record alone | the record: two different certificates; nothing in the record names the Vault that produced a signature |
| 9 | The three certificates in the record still expire on **2026-12-31** (issuer 13:22:59, authority 13:22:39, root 23:59:59 GMT); the maintenance commitment runs to 2027-05-25 | `openssl x509 -enddate` |
| 10 | Neither the git tag nor the release commit carries a git signature (no PGP or SSH block); GitHub reports the release commit as `unsigned`. The record is what carries signatures. The commit message holds a signing-metadata block of the committer's Vault, which is not a git signature and is not read as one | `git cat-file`; `gh api` verification `unsigned` |
| 11 | The repository still has **no GitHub Release** objects: a release is a git tag plus the record file | `gh release list` is empty |

## 3. The chain, link by link

`release -> source commit -> grammar file digests -> Forms pin`

| Link | Status | Why |
|---|---|---|
| release record -> its signatures | **holds, internally** | finding 6; trusted only if an external anchor is supplied |
| release record -> `source_commit` | **holds, as a signed claim** | `provenance` is inside the signed `build_hash` envelope; the source commit exists and is the parent of the release commit |
| release -> grammar digests | **holds for all 12 pinned files** | findings 4 and 5: the record digests the 12 files in `artifacts.files`, and the two grammar files also in `provenance` |
| grammar digests -> the Forms pin | **holds** | findings 2 and 3: the pin commit is an ancestor of the source commit and the 12 files are byte-identical to the pin |

So the Forms pin **is** released, by `primitives/@v0.2.1`. `v0.2.0` is still not the release of the pin and is not offered as one.

## 4. The conditions of section 6 of the pre-check, today

An authentic receipt needs all of them. Measured state:

| # | Condition | Today |
|---|---|---|
| 1 | a release record is supplied | an input of the producer; the record exists |
| 2 | matches `release.schema.yaml` | holds (finding 6) |
| 3 | `build_hash` recomputed equals the recorded one | holds |
| 4 | `release.sign` verifies | holds |
| 5 | `cic_countersign` present, signs `build_hash`, verifies | holds |
| 6 | the authority chains to a root whose fingerprint equals a trust anchor **pinned outside the record** | **open**: no anchor is chosen; the record carries its own root, which is "unanchored" |
| 7 | every certificate and the commitment window valid at the time of the check | **open as a policy**: valid at the check of 2026-11-02, all three certificates expire on 2026-12-31, so the same check fails after that day unless the chain is renewed or the rule is decided |
| 8 | the record's version, source commit and digests equal the receipt's, after the one defined re-encoding | **open**: the record writes `sha256:<hex>` for the artifacts but base64 for `build_hash` and `provenance.grammar_*`; the receipt takes `sha256:` and hex for `primitive_build_hash`, `grammar_sha256` and `grammar_schema_sha256`. The base64 to hex mapping is a lossless re-encoding, but **no document defines it as a contract**, and which record field a receipt field reads (the `provenance` value or the `artifacts` value) is not decided |
| 9 | the Forms pin is covered by the release | **holds** (findings 2, 3, 4): the pin commit is in the source commit's lineage, and every file of the pin's manifest is identical to a file the release digests |
| 10 | the record is not the fixture | holds: `v0.2.1` is not `fixture/@v0.0.0` |
| 11 | a git tag or commit signature is not accepted in place of the above | unchanged: finding 10, neither is signed |

Conditions 2 to 5, 9 and 10 hold. 6, 7 and 8 are open and are decisions or units of their own.

## 5. What stays open

* A CIC root pinned **outside** the record, and the rotation of the chain that expires on 2026-12-31 (condition 6 and 7). Who may sign, and whether the countersignature alone authorizes a release, is the owner's decision; the record cannot show it.
* The contract for the base64 to `sha256:` hex mapping of the three receipt digests, with golden vectors in both languages, and the choice of the record field each receipt field reads (condition 8). A unit of its own.
* The production receipt function taking a verified release record: HOLD (question L). `cic-primitive-engine#63` and this repository's #138 stay open.
* The closeout entry `authentic-release-origin` stays `not_proved`: a signed release that contains the pin now exists, but it is not verified against an external trust anchor and no receipt names it.

## 6. Not claimed

No release was created, signed or edited by this document, no trust anchor is chosen, and no production receipt API is added. A green result of the checks above is a statement about lineage, byte identity and internal signature consistency; it is not a statement that the release is genuine or fit to publish. The measurement was run once on 2026-11-02 and is not a CI gate; a later state of `cic-primitives` can change it, and the immutable identifiers are the commit ids and the record's SHA-256 (`e5d8268aede0d24f02e60a151099a2fdc7615172ed3105b0a05b6b5d19addf63`), not the tag.
