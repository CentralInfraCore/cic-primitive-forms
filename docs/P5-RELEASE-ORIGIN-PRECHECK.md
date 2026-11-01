# P5-3: release-origin pre-check (question L)

Status: pre-check, 2026-11-01. It reads and measures; it changes no code, creates no release and adds no production receipt API. It follows `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 15 (decision L, the two gates) and section 22 (the closeout, entry `authentic-release-origin`).

**Question.** Is there a signed, verifiable `cic-primitives` release that the Forms grammar pin belongs to, so that a receipt can name an *authenticated* release (`primitive_release`, `primitive_source_commit`, `primitive_build_hash`, `grammar_sha256`, `grammar_schema_sha256`)?

**Answer, measured.** A signed and counter-signed release exists (`primitives/@v0.2.0`), but **no release contains the pinned grammar**. The chain release -> source commit -> grammar digests holds inside that release; the last link, to the Forms pin, is **refuted**. No authentic receipt can be produced from the current pin.

## 1. What was examined, and how to repeat it

Read-only, against `CentralInfraCore/cic-primitives` (a clone with all tags, `origin/devel` `d27173f`, `origin/main` `411ce80`) and the pin in `corpus/pins.yaml`:

```
gh release list -R CentralInfraCore/cic-primitives
git tag -l ; git merge-base --is-ancestor <pin> <tag>
git archive primitives/@v0.2.0 | tar -x -C <dir> ; (cd <dir> && python3 tools/compiler.py verify-release release/cic-primitives-v0.2.0.yaml)
git show <ref>:<path> | sha256sum        # for each of the 12 pinned files
```

(`tools/compiler.py` needs the `semver` package only to *make* a release; for `verify-release` an empty stand-in module was used, no network install.)

## 2. Findings

| # | Finding | Measured |
|---|---|---|
| 1 | The pin is `73ae469` (`devel`, 2026-10-18; a GitHub merge commit, whose GitHub signature is `valid`: that is a merge signature, not a release signature) | `corpus/pins.yaml`; `gh api .../commits/73ae469` |
| 2 | The pin is an ancestor of `devel` and of **no tag**; it is not on `main` | `merge-base --is-ancestor` over all 15 tags: 0 hits; `origin/devel` yes, `origin/main` no |
| 3 | The repository has **no GitHub Release** objects | `gh release list` is empty. Releases exist as git tags plus a record file under `release/` |
| 4 | The newest release is the tag `primitives/@v0.2.0` -> `960ee0f` (annotated tag, 2026-09-06). Its **git tag and commit carry no signature** | `git tag -v`, `git log %G?` = `N`, GitHub `unsigned`. The tag is therefore not evidence of origin |
| 5 | The release **record** `release/cic-primitives-v0.2.0.yaml` (`kind: PrimitiveRelease`, envelope 2) carries the signatures: `release.sign` (Vault ECDSA over `build_hash`) and `cic_countersign` (CIC Source CA over `build_hash`) | upstream `verify-release`: schema valid, `build_hash` recomputed and equal, release signature verified, countersignature verified, authority certificate chains to the root *in the record* |
| 6 | The same verifier states what it does **not** prove: no external trust anchor was supplied, so every certificate is self-asserted; the developer pledge travels separately | `verify-release` output, "NOT proven" lines |
| 7 | The release issuer certificate (CIC Developer CA) is **not** chained to anything in the record: only the countersignature's chain (CIC Source CA -> CIC Root CA) is. Release signature and countersignature are both over the same `build_hash` | the verifier code and the record |
| 8 | All three certificates expire on **2026-12-31** (issuer 13:22:59, authority 13:22:39, root 23:59:59 GMT); the maintenance commitment runs to 2027-05-25 | `openssl x509 -dates`. Today is 2026-11-01: an expiry check on the receipt side would flip in two months |
| 9 | `provenance.source_commit` of `v0.2.0` is `9c67ea4` (the parent of the release commit, a merge on the release branch). It **is** an ancestor of `main`; it is **not** an ancestor of `devel` or of the pin | `merge-base --is-ancestor`; consistent with the base-repo release model in which no release branch merges back |
| 10 | `provenance.grammar_sha256` and `grammar_schema_sha256` are base64 SHA-256 of exactly **two files**, `proposals/atom-grammar/check_grammar.py` and `instance-grammar.schema.yaml`, as they are in the release tree (recomputed: both equal; `dependency_lock_sha256` too) | recomputation |
| 11 | The pinned grammar is **twelve** files (hex SHA-256 of blob bytes). Against the `v0.2.0` tree: 3 identical (`LICENSE.md`, `schemas/atomic/role.yaml`, `schemas/atomic/shape.yaml`), **5 differ** (`README.md`, `check_grammar.py`, `instance-grammar.schema.yaml`, `policy-surface.yaml`, `access.yaml`), **4 are absent** (`P0-4-ACCESS-GRAMMAR.md`, `P0-4-MIGRATION.md`, `access-vectors/resolution.yaml`, `access_policy.py`) | blob digests per file; `v0.1.5` is further away |
| 12 | The record writes digests as **base64 (44 characters)**; the receipt layout takes `sha256:` and 64 lowercase hex for `primitive_build_hash`, `grammar_sha256` and `grammar_schema_sha256` | `docs/RECEIPT-SCHEMA.md` of the engine; `rust/src/receipt.rs` form check. The conversion base64 -> hex is a lossless re-encoding of the same 32 bytes, but **no document defines it as the contract** |

## 3. The chain, link by link

`release -> source commit -> grammar file digests -> Forms pin`

| Link | Status | Why |
|---|---|---|
| release record -> its signatures | **holds, internally** | findings 5-7; trusted only if an external anchor is supplied |
| release record -> `source_commit` | **holds, as a signed claim** | `provenance` is inside the `build_hash` envelope v2; `9c67ea4` exists and is the parent of the tag commit |
| release -> grammar digests | **holds for two files** | finding 10 |
| grammar digests -> the Forms pin | **refuted** | findings 2, 9, 11: the pin commit is in no release; the release's source commit is not an ancestor of the pin; the two digested files differ from the pinned ones and 4 of the 12 pinned files are not in the release at all |

So the Forms pin is **not released**: a receipt that names `primitives/@v0.2.0` together with the pinned grammar would be a false statement, and one that names a fabricated release would be invented. Neither is allowed (decision L).

## 4. The minimal authenticated release record

What a receipt producer would need, and where each piece comes from:

| Datum | Source | Who checks it |
|---|---|---|
| `primitive_release` (tag name) | `PrimitiveRelease.version` plus the repository tag naming rule (`primitives/@v<version>`) | the verifying party, against the record |
| `primitive_source_commit` | `provenance.source_commit` | the verifying party; also that the commit exists in the release lineage |
| `primitive_build_hash` | `release.build_hash`, recomputed over the envelope | the verifying party |
| `grammar_sha256`, `grammar_schema_sha256` | `provenance.*`, recomputed from the release tree | the verifying party |
| signatures | `release.sign` and `cic_countersign.sign` over `build_hash`, with the certificates | the verifying party, with an **externally pinned** trust anchor |
| the pin's file digests | `corpus/pins.yaml` | Forms (a table compared in both languages, as decision L says) |

The responsibility is split and stays split (ADR 0010): **the library does not verify a signature.** The verifying party (the caller's side, a tool such as `verify-release --trust-root`) authenticates the record; the library takes the *already verified* record and checks the part it can: that the record's source commit and grammar digests are those of the pin it compiled under. The engine verifier checks neither (section 15.2 of the P5 note).

## 5. Two checks, kept apart

* **Cryptographic origin** (`authentic`): the record's schema, `build_hash` recomputation, release signature, countersignature, certificate chain **to an externally pinned root**, validity at the time of the check. Done by the verifying party. Nothing in the Engine or in Forms does it today.
* **Receipt consistency** (the engine's `verify_receipt` / `receipt.Verify`): the 15 fields are well formed and agree with the object, the input and the evidence. It cannot tell a genuine release from a well-formed invented one. A green verdict is never read as the first check.

A receipt in which the second passes and the first was not done must stay unmistakable as a fixture: today that separation is the name of the function (`receipt_flat_fixture`), because the layout has no field for it.

## 6. Fail-closed conditions

An authentic receipt is produced only if **all** hold; each failure is a refusal that names it, never a warning:

1. a release record is supplied (none supplied is a refusal, not a fixture fallback);
2. it matches `release.schema.yaml`;
3. `build_hash` recomputed over the envelope equals the recorded one;
4. `release.sign` verifies;
5. `cic_countersign` is present, signs `build_hash`, and verifies;
6. the authority certificate chains to a root, and that root's fingerprint equals a trust anchor **pinned outside the record** (a record that merely carries its own root is "unanchored"; the upstream verifier reports it as a warning, which is not enough here);
7. every certificate and the commitment window are valid at the time of the check (finding 8: all expire 2026-12-31);
8. the record's version names the receipt's `primitive_release`; `provenance.source_commit` equals `primitive_source_commit`; the digests equal the receipt's, after the one defined re-encoding (finding 12);
9. **the Forms pin is covered by the release**: the pin's commit is the record's source commit, or every file of the pin's manifest is shown identical to a file the release digests (finding 11 fails today);
10. the record is not the fixture (`fixture/@v0.0.0`, the all-zero commit): a fixture value never passes the production path;
11. a git tag or a commit signature is **not** accepted in place of any of the above (finding 4).

## 7. What stands between the current state and an authentic receipt

Facts, not decisions; the choices are the owner's:

* **Either** a release is cut from (or containing) the pinned commit, in the base-repo release model; **or** the Forms pin moves to a released commit and every `grammar:` reference is migrated on purpose (ADR 0002). Neither is done or proposed here.
* A trust anchor for the CIC root has to be fixed *outside* the record, and its rotation (the current one expires 2026-12-31) decided.
* The base64 -> `sha256:`hex mapping of the three digests has to be written down as a contract (a unit with golden vectors), not left to a caller.
* The verified-record input of the production function (shape, and the table compared with `corpus/pins.yaml` in both languages) is a separate unit after the above.

## 8. Not claimed

No release was created, signed or edited. `v0.2.0` is not offered as the release of the pin. No trust anchor is chosen. No production receipt API, no P5-4 work, and `cic-primitive-engine#63` stays open. The verification above is a read of what exists, run once on 2026-11-01; it is not a CI gate and a later state of `cic-primitives` can change it.

## 9. Proposal for the separate implementation PR (after review)

1. The owner decides: cut a release from the pinned commit, or move the pin (section 7).
2. A tool-only unit that turns the findings into a repeatable check: given a record and a pinned root, report each condition of section 6 with its verdict (no signing, no network beyond the fetch).
3. The base64 -> hex mapping with vectors, in both languages.
4. Only then the production function with a verified-record input; it is not specified here.
