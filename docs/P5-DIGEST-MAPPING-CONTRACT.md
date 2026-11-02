# P5-3: release record to receipt digest mapping, contract (condition 8)

Status: **contract text and yardstick**, 2026-11-02. It turns the plan of `docs/P5-DIGEST-MAPPING-PLAN.md` and the five owner decisions D1 to D5 into normative text, a language-neutral vector file and a reference. It adds **no Rust or Go implementation** (on hold until the yardstick is reviewed), no production receipt API, no release, no signature check, and it chooses no trust anchor. The record side of the digests is the contract of `cic-primitives` (`docs/en/RELEASE-RECORD-CONTRACT.md`); this document is the receipt side.

The key words MUST, MUST NOT and MAY are used as in RFC 2119.

## 1. Decisions recorded

Accepted by the owner on 2026-11-02, after the review of the plan (#148):

| # | Decision | Rule |
|---|---|---|
| D1 | primary source | The two grammar digests come from the `artifacts.files` entry of a fixed path. `provenance.grammar_*` and the Forms pin are mandatory cross-checks. |
| D2 | absent cross-check | Fail closed. A missing `provenance.grammar_*` is a refusal, not a skipped check. |
| D3 | envelope | Only the integer `3`, in both `release.envelope` and `provenance.envelope`. There is no fallback to an earlier envelope. |
| D4 | where the text lives | The receipt-side mapping contract is in this repository. The one-line correction of the record contract (`build_hash` is still base64) is a separate change in `cic-primitives`. |
| D5 | the engine's own identity | Not part of this unit. Needs its own consistency rule and vectors in the Engine #63 line. |

## 2. What the function is

`map_release(record, pin)` is a pure function. `record` is a `cic-primitives` release record that **the verifying party has already authenticated**; `pin` is `{path: 64 lowercase hex}`, the digests of the manifest of `corpus/pins.yaml` without the `sha256:` prefix. It returns the three receipt digests or refuses with exactly one reason and the name of the field it concerns.

It MUST NOT verify a signature, read a certificate, a trust anchor or a clock, open a file, or run a command. It MUST NOT return a value together with a warning. It MUST NOT choose between two sources of one value: every value has one primary source and every other place is a check (section 4).

## 3. The conversion of a base64 digest

Input: the value **as a string, exactly as parsed**. Nothing is trimmed, case-folded, normalized or coerced. Output: the receipt form `sha256:` followed by 64 lowercase hexadecimal digits (71 characters).

1. **Type.** The value MUST be a string. A number, boolean, null, list or mapping is refused as `field_type`.
2. **Shape, before any decoder runs.** The **whole** string MUST match `[A-Za-z0-9+/]{43}=`: 44 characters, the standard alphabet, one padding character and it is the last. Whitespace (including a final newline or carriage return), the URL-safe characters `-` and `_`, a missing or doubled padding, a NUL, a non-ASCII character or any other length is `base64_shape`. The match is a full match: an end anchor that lets a final newline through (`$` in Python) is not one.
3. **Decode** with the standard alphabet. The result MUST be exactly 32 bytes, else `base64_length` (step 2 already implies it; the check guards a decoder that returns another length).
4. **Canonical spelling.** Encoding the 32 bytes again MUST give the input string, else `base64_not_canonical`. Equivalently the character in front of the padding is one of `AEIMQUYcgkosw048`. One digest has exactly one accepted spelling.
5. **Output** `"sha256:" + lowercase hex of the 32 bytes`. The prefix is not read from the value: the algorithm is a fact of the record contract (`build_hash` is the SHA-256 of the canonical bundle), which is why the function accepts envelope 3 only.

An `artifacts.files[].digest` is already in the receipt form: it MUST match `sha256:[0-9a-f]{64}` as a **full** string and is then used as it is, otherwise `digest_form`. The reverse conversion is not defined.

**Why steps 2 and 4 are stated and not left to a decoder.** Measured on 2026-11-02 on the real value and two mutations of it: Python `b64decode(validate=True)` and Go `StdEncoding` both accept a non-canonical spelling of the same 32 bytes, and Go accepts a trailing newline even with `Strict()` (Python rejects the newline). An implementation MUST therefore check the shape and the canonical spelling itself, in every language, before or after decoding. The Rust decoder was not measured in this session; the Rust implementation proves its behaviour against the vectors, it does not assume it.

## 4. Sources and checks

| Receipt field | Primary source | Mandatory cross-checks |
|---|---|---|
| `primitive_build_hash` | `release.build_hash`, converted (section 3) | none: it is the only place that holds it |
| `grammar_sha256` | the one `artifacts.files` entry whose `path` equals `proposals/atom-grammar/check_grammar.py` (exact string equality), used as it is | `provenance.grammar_sha256`, converted, equals it; the pin's entry for the same path, prefixed with `sha256:`, equals it |
| `grammar_schema_sha256` | the one entry for `proposals/atom-grammar/instance-grammar.schema.yaml` | `provenance.grammar_schema_sha256`, and the pin's entry, as above |

The comparison is a plain string comparison of the receipt forms. A disagreement is a refusal that names the field. Nothing is corrected, no side is preferred, and no result is returned with a warning.

## 5. Order of the checks, and the refusals

The record is judged from top to bottom and the first failure is the answer, so a case has one expected reason:

1. `release.envelope`, then `provenance.envelope`: each MUST be the integer `3` (not `3.0`, `"3"`, `true`, absent). Else `envelope_not_3`, field: that member.
2. `release.build_hash`: absent or null is `field_missing`, not a string is `field_type`, then the conversion (`base64_shape`, `base64_length`, `base64_not_canonical`). Field: `release.build_hash`.
3. `artifacts.files`: absent or null is `field_missing`, not a list is `field_type`.
4. For `grammar_sha256` and then `grammar_schema_sha256`: (a) the entry is absent (`artifact_absent`) or listed more than once, whatever its digests (`artifact_duplicate`), field: the path; (b) its digest is not a string (`field_type`) or not of the form of section 3 (`digest_form`), field: `artifacts.files[<path>].digest`; (c) the `provenance` member is absent or null (`field_missing`), not a string (`field_type`), or fails the conversion; field: `provenance.<member>`; (d) the converted value differs from the primary one (`provenance_artifacts_disagree`), field: the receipt field; (e) the pin has no string entry for the path (`field_missing`, field `pin.<path>`); (f) the pin differs (`record_pin_disagree`), field: the receipt field.

| Reason | Meaning |
|---|---|
| `envelope_not_3` | one of the two envelopes is not the integer 3 |
| `field_missing` | a member the mapping reads is absent or null |
| `field_type` | a member is not of the type the mapping needs |
| `base64_shape` | not 43 standard-alphabet characters and one padding character, as a whole string |
| `base64_length` | decodes to other than 32 bytes (defensive; unreachable after the shape check) |
| `base64_not_canonical` | the same 32 bytes in another spelling |
| `digest_form` | an artifact digest is not `sha256:` and 64 lowercase hex, as a whole string |
| `artifact_absent` / `artifact_duplicate` | no entry, or more than one entry, for the path |
| `provenance_artifacts_disagree` | the two records of the same digest differ |
| `record_pin_disagree` | the record and the pin differ |

## 6. The vectors

`testdata/p5/digest-mapping.json` holds 83 cases, written by `tools/p5/digest_mapping_cases.py` (`--write`; without the flag it checks that the committed file is the generated one). A **conversion** case is `{input, expect | refuse}`. A **map** case is `{record, pin, expect | refuse, field}` where `record` holds only the members the mapping reads. An implementation MUST give, for every case, the expected receipt digest or the expected reason, and for a map case the expected field.

The cases are: six positive conversions (the three digests of the record of `cic-primitives` `v0.2.1`, 32 zero bytes, 32 bytes of `0xff`, a value that uses `+` and `/`) and the refused spellings (non-canonical, trailing and embedded whitespace, URL-safe characters, missing and doubled padding, 31, 33 and 64 bytes, empty, hex, the receipt form, a non-ASCII character, a NUL, non-strings); the record of `v0.2.1` and a synthetic one that map; every refusal of section 5 one defect at a time; the order of the checks; and a record with a forged signature that maps all the same (section 7).

The expected values are not computed by the reference. Synthetic ones are written from raw bytes; the real ones are the values of the `v0.2.1` record, recomputed in the tests with `openssl base64 -d | od`, and the two grammar digests are compared with `corpus/pins.yaml`. `tools/p5/digest_mapping.py` is the reference (Python); `tools/p5/test_digest_mapping.py` runs the cases against it, recomputes the values independently, checks a property around the canonical form with seeded random values, and plants 13 defects in copies of the reference, each of which has to turn the cases red; two further changes cannot alter a result and are listed with the reason, and have to survive.

## 7. What a successful mapping proves, and what it does not

It proves one thing: **the three receipt fields are the faithful, single-spelling re-encoding of values on which the record, its own provenance and the pin agree.**

It proves nothing about the release. It does not show that the record is authentic, that a signature verifies, who the signer is or may be, that the countersignature authorizes anything, that a trust anchor was applied, that the certificates are valid on the day of the check or that the release is fit to publish. A record with a forged or missing signature maps, and the vector `R4` says so on purpose. The function takes a record that the verifying party has already authenticated (ADR 0010); it adds no trust, and a green result of it is never read as a statement of origin. Conditions 6 and 7 of the release-origin addendum are the subject of the trust model plan, `docs/P5-TRUST-MODEL-PLAN.md` (#149).

## 8. Not claimed, and what comes next

No implementation in Rust or Go exists or is started. `authentic-release-origin` stays `not_proved`; the production receipt API stays on hold (question L); `cic-primitive-engine#63` and this repository's #138 stay open. After the yardstick is reviewed: the Rust and Go functions meeting the vectors; differential and fuzz around the shapes of section 3; planted defects in each implementation. None of these is done here.
