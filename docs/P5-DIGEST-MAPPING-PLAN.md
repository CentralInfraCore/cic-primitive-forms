# P5-3: release record to receipt digest mapping, contract plan (condition 8)

Status: **plan**, 2026-11-02. Design only. It adds no code, no vector file, no implementation and no production receipt API; it creates, signs and edits no release and chooses no trust anchor. Implementation, merge of any implementation and the production receipt stay on hold (question L). It follows `docs/P5-RELEASE-ORIGIN-ADDENDUM.md`, condition 8 of its section 4, which it narrows to one question.

**Question.** A receipt carries `primitive_build_hash`, `grammar_sha256` and `grammar_schema_sha256` as `sha256:` and 64 lowercase hex digits. A `cic-primitives` envelope 3 record writes the same information in different places and different spellings. Which record field does each receipt field come from, how exactly is it converted, and what must agree with what, so that a producer never **chooses** between two possible sources and never decides, by converting, whether the release is genuine?

The answer proposed here is below. Its five decisions are marked D1 to D5 in section 8 and are the owner's; until they are taken, nothing here is a contract.

## 1. What the record holds, and what the receipt takes (measured)

Measured on the record of `primitives/@v0.2.1` (release commit `5e8c3219b52399e3e764b0b0bb27de710f5445a4`, record SHA-256 `e5d8268aede0d24f02e60a151099a2fdc7615172ed3105b0a05b6b5d19addf63`) and on this repository's `corpus/pins.yaml`:

| Receipt field | Record place | Spelling in the record | Value in `v0.2.1` |
|---|---|---|---|
| `primitive_build_hash` | `release.build_hash` | **base64**, 44 characters | `HVazYxdg737P2bMy2Enkj5ej1VfF+TQ4pDub86G3p2s=` = hex `1d56b3631760ef7ecfd9b332d849e48f97a3d557c5f93438a43b9bf3a1b7a76b` |
| `grammar_sha256` | `artifacts.files[path = proposals/atom-grammar/check_grammar.py].digest` | `sha256:` + 64 lowercase hex | `sha256:ef2d6a6e0f692c0e8a92cb813021193a8904cb41a7abc0ea838730b106726375` |
| `grammar_sha256` | `provenance.grammar_sha256` | **base64**, 44 characters | `7y1qbg9pLA6KksuBMCEZOokEy0Gnq8Dqg4cwsQZyY3U=` = the hex above |
| `grammar_schema_sha256` | `artifacts.files[path = proposals/atom-grammar/instance-grammar.schema.yaml].digest` | `sha256:` + 64 lowercase hex | `sha256:3af267cd6e7f0b9317e62bfcb8f0d1d20ef7f9b274d70a3c87538dff64fda657` |
| `grammar_schema_sha256` | `provenance.grammar_schema_sha256` | **base64**, 44 characters | `OvJnzW5/C5MX5iv8uPDR0g73+bJ01wo8h1ON/2T9plc=` = the hex above |
| both grammar digests | the pin: `corpus/pins.yaml`, `cic-primitives-73ae469-6a188078` | 64 lowercase hex, no prefix | equal to the two values above |

The conversions were computed twice, with Python `base64` and with `openssl base64 -d | xxd -p`, and agree. The grammar digests are present **three times** (artifacts, provenance, the pin), the build hash once.

One statement of the record contract needs a correction, which this plan does not make: `docs/en/RELEASE-RECORD-CONTRACT.md` (section on the digest form) says the `sha256:` hex form is "the form of the receipt fields (`primitive_build_hash`, `grammar_sha256`), so a consumer needs no re-encoding for the new fields". That holds for the new `artifacts` digests. It does **not** hold for `primitive_build_hash`: `release.build_hash` is an existing field, still base64 (`get_sha256_b64` of the compiler), so a re-encoding is needed there. A one-line clarification in `cic-primitives` is a separate change.

## 2. Proposed source of each receipt field (D1, D2, D3)

The receipt never takes "either of two": every field has **one primary source**, and every other place that holds the same value is a **mandatory consistency check**.

| Receipt field | Primary source | Mandatory consistency checks | Refusal if |
|---|---|---|---|
| `primitive_build_hash` | `release.build_hash`, converted (section 3) | none: the only place that holds it; the verifying party recomputes it (condition 3), this function does not | the field is missing, not a string, or not of the form of section 3 |
| `grammar_sha256` | `artifacts.files` entry for `proposals/atom-grammar/check_grammar.py` (already the receipt form, copied unchanged after a form check) | **C1** `provenance.grammar_sha256`, converted, equals it; **C2** the pin's manifest entry for the same path equals it | missing or duplicated entry; wrong form; C1 or C2 disagrees |
| `grammar_schema_sha256` | `artifacts.files` entry for `proposals/atom-grammar/instance-grammar.schema.yaml`, as above | **C1** and **C2** against `provenance.grammar_schema_sha256` and the pin | as above |

Why `artifacts.files` is the primary source for the two grammar digests (D1, a recommendation): it needs **no conversion** (fewer places where two languages can differ); it is where the record contract puts its own per-file digest of the pin; it is inside what `build_hash` covers, like `provenance`; and only an envelope 3 record can cover the 12 pinned files at all. `provenance.grammar_*` stays a mandatory cross-check because the engine's own identity is spelled in that base64 form (`docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md`, D1), so a disagreement between the two places is exactly what a producer must refuse, not repair.

* **D2, strict presence (recommendation).** For an envelope 3 record the compiler always writes `provenance.grammar_*`; its absence is a **refusal**, not a skipped check. "Check where both exist" would let a record with the cross-check removed through.
* **D3, one envelope.** Only a record with `release.envelope` and `provenance.envelope` equal to the integer `3` is accepted by this function; an earlier record has no `artifacts` and cannot cover the pin (12 of 12 is impossible for it), so its refusal is `no_primary_source`, not a fallback to `provenance` alone.

## 3. The conversion, byte by byte (language-neutral)

Input: the YAML scalar **as a string**, exactly as parsed; nothing is trimmed, case-folded, normalized or coerced. Output: the receipt form, 71 characters.

1. **Type.** The value is a string. A number, null, list, mapping or boolean is refused.
2. **Shape, checked before any decoder runs.** The whole string matches `[A-Za-z0-9+/]{43}=` and nothing else: 44 characters, the standard alphabet, exactly one `=` and it is the last character. No whitespace, newline, carriage return, URL-safe `-` or `_`, no missing or doubled padding, no NUL. (The check is a *full* match: an anchored pattern with `$` accepts a trailing newline in Python, which is the defect found in the reference verifier on 2026-11-02.)
3. **Decode** with the standard alphabet. The result is exactly 32 bytes (the shape of step 2 already implies it; a decoder that returns another length is a refusal, not a surprise).
4. **Canonical.** Re-encoding the 32 bytes gives back the input string. Equivalently the character before `=` is one of `AEIMQUYcgkosw048` (its two low bits are zero). A string that decodes to the same 32 bytes in another spelling is refused, so one digest has exactly one accepted spelling.
5. **Output** `sha256:` followed by the lowercase hex of the 32 bytes (64 characters). The prefix is not read from the value: the algorithm is a fact of the record contract (`build_hash` is the SHA-256 of the canonical bundle), which is why D3 restricts the function to envelope 3.

An `artifacts` digest needs no conversion, only the form check: it matches `sha256:[0-9a-f]{64}` as a full string. The reverse conversion (hex to base64) is not defined and not needed.

**Why step 2 and 4 are not left to the decoders.** Measured on 2026-11-02 on the real value and two mutations of it:

| Input | Python `b64decode(validate=True)` | Go `StdEncoding` | Go `StdEncoding.Strict()` |
|---|---|---|---|
| canonical `...G3p2s=` | accepted | accepted | accepted |
| non-canonical `...G3p2t=` (same 32 bytes) | **accepted** | **accepted** | rejected |
| canonical + trailing `\n` | rejected | **accepted** | **accepted** |

The Rust decoder was **not measured** in this session (no toolchain); it is a case of the vector unit, not an assumption. The three libraries already disagree with each other on what a valid base64 string is, so the contract states the shape and canonicality itself and the decoders only decode.

## 4. The consistency rule

After step 5 every candidate for one field is in the receipt form, and the rule is a plain string comparison of those forms: the primary value, the converted `provenance` value and the pin's value (`sha256:` plus its hex) must be **equal**. Anything else is a refusal that names the disagreeing pair (`provenance_artifacts_disagree`, `record_pin_disagree`); nothing is corrected, no side is preferred, and no warning is accepted in place of a refusal.

## 5. Refusals

One named reason each, never a guess, never a fixture fallback (decision L):

`envelope_not_3`, `field_missing` (with the field), `field_type`, `base64_shape`, `base64_not_canonical`, `base64_length`, `digest_form`, `artifact_absent`, `artifact_duplicate`, `provenance_artifacts_disagree`, `record_pin_disagree`.

## 6. Golden vectors (to be a language-neutral file shared by Rust and Go in the vector unit; the values below are computed)

**Positive (input to output).**

| # | Input | Output |
|---|---|---|
| P1 | `release.build_hash` of `v0.2.1`: `HVazYxdg737P2bMy2Enkj5ej1VfF+TQ4pDub86G3p2s=` | `sha256:1d56b3631760ef7ecfd9b332d849e48f97a3d557c5f93438a43b9bf3a1b7a76b` |
| P2 | `provenance.grammar_sha256` of `v0.2.1`: `7y1qbg9pLA6KksuBMCEZOokEy0Gnq8Dqg4cwsQZyY3U=` | `sha256:ef2d6a6e0f692c0e8a92cb813021193a8904cb41a7abc0ea838730b106726375` |
| P3 | `provenance.grammar_schema_sha256` of `v0.2.1`: `OvJnzW5/C5MX5iv8uPDR0g73+bJ01wo8h1ON/2T9plc=` | `sha256:3af267cd6e7f0b9317e62bfcb8f0d1d20ef7f9b274d70a3c87538dff64fda657` |
| P4 | 32 zero bytes: `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=` | `sha256:` and 64 `0` |
| P5 | 32 bytes `0xff`: `//////////////////////////////////////////8=` | `sha256:` and 64 `f` |
| P6 | a value that uses both `+` and `/` (the bytes `fb ff bf` ten times, then `fb ff`): `+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/+/8=` | `sha256:fbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbffbff` |
| P7 | the whole record of `v0.2.1` with the pin `cic-primitives-73ae469-6a188078` | the three receipt fields of P1 to P3, all consistency checks passing |

**Negative (each is one defect, one expected refusal).**

| # | Input | Refusal |
|---|---|---|
| N1 | P1 with the last data character changed to `t` (`...G3p2t=`): the same 32 bytes in another spelling | `base64_not_canonical` |
| N2 | P1 with a trailing newline | `base64_shape` |
| N3 | P1 with a leading space; with an embedded newline; with a carriage return | `base64_shape` |
| N4 | P1 with `+` replaced by `-` (URL-safe alphabet) | `base64_shape` |
| N5 | P1 without the padding `=` (43 characters); with `==` | `base64_shape` |
| N6 | 31 bytes (`AAAA...A==`, 44 characters with two pads); 33 bytes (`AAAA...A`, 44 characters, no pad); 64 bytes (88 characters) | `base64_shape` (the shape check of step 2 stops them before a decoder runs; `base64_length` is the defensive check of step 3 and is reachable only by a decoder that returns another length than 32) |
| N7 | `release.build_hash` is an integer, null, a list | `field_type` |
| N8 | `release.build_hash` missing; `provenance.grammar_sha256` missing; the `artifacts` entry for `check_grammar.py` missing | `field_missing` / `artifact_absent` |
| N9 | the `artifacts` entry listed twice, both with the right digest | `artifact_duplicate` |
| N10 | the artifacts digest in uppercase hex; with `sha512:` and 64 hex; with 63 or 65 hex; without the prefix; with a trailing newline | `digest_form` |
| N11 | `provenance.grammar_sha256` differs from the artifacts digest by one bit (`7y1qbg9pLA6KksuBMCEZOokEy0Gnq8Dqg4cwsQZyY3Q=`) | `provenance_artifacts_disagree` |
| N12 | the artifacts digest differs from the pin by one bit, `provenance` equal to the artifacts | `record_pin_disagree` |
| N13 | `release.envelope` 2 or 1, `3.0`, `"3"`, `true`; `provenance.envelope` different from `release.envelope` | `envelope_not_3` |
| N14 | a record that is internally consistent in all of the above but has a **forged** or **unsigned** signature, or a certificate from no trusted root | **no refusal from this function**: the mapping does not look at signatures (section 7). The vector is there to show that the function accepts it and that the gates of conditions 2 to 7 are what must refuse it |

Each vector gets an independent reference value: the digests above are computed with `openssl`/`xxd` and plain `sha256sum` of the pinned blobs, not with the code under test.

## 7. What the mapping proves, and what it does not

A successful mapping proves one thing: **the three receipt fields are the faithful, single-spelling re-encoding of values that the record, its own provenance and the pin all agree on.**

It proves nothing about the release. It does not show that the record is authentic, that a signature verifies, who the signer is, that the signer may sign, that the countersignature authorizes anything, that a trust anchor was applied, that the certificates are valid on the day of the check, or that the release is fit to publish. N14 exists to keep that visible. The function takes a record that the verifying party has already authenticated (ADR 0010: the library does not verify a signature); it adds no trust, and a green result of it is never read as a statement of origin. Conditions 6 and 7 of the addendum stay open and are a separate plan.

## 8. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | The primary source of the two grammar digests | `artifacts.files` (no conversion), `provenance.grammar_*` as mandatory cross-check, the pin as a second mandatory check |
| D2 | Presence of the cross-checks | strict: an absent `provenance.grammar_*` is a refusal |
| D3 | Accepted envelope | only `3`; no fallback to an earlier record |
| D4 | Where the normative text lives | in this repository (the receipt side), citing the record contract; the one-line correction of the record contract (section 1) as a separate change in `cic-primitives` |
| D5 | The relation to the engine's own identity of the two grammar digests (`dependency.yaml`, base64) | out of this unit; recorded as a vector case once the engine side (`cic-primitive-engine#63`) is decided |

## 9. Units, all after the decisions

A. the normative contract text and the language-neutral vector file, with independently computed reference values; B. the Rust and Go implementations, one function each, no signature code; C. differential and fuzz around the shapes of section 3 (generated strings near the canonical form); D. planted defects, so that each check is shown to be able to fail; then the production receipt, which also needs conditions 6 and 7. None of A to D starts before the owner has decided D1 to D5.

## 10. Not claimed

No release was created, signed or edited. No implementation exists; the Rust decoder behaviour is not measured. The mapping does not decide authenticity, does not close any open gate of the addendum, and `authentic-release-origin` stays `not_proved`. `cic-primitive-engine#63` and this repository's #138 stay open.
