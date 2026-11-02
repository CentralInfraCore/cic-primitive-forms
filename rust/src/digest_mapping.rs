//! The release record to receipt digest mapping (P5-3, condition 8; `docs/P5-DIGEST-MAPPING-CONTRACT.md`). The Go peer is `go/digestmapping.go`; the shared cases are
//! `testdata/p5/digest-mapping.json`, written by `tools/p5/digest_mapping_cases.py`, whose Python reference is `tools/p5/digest_mapping.py`.
//!
//! A pure function over a record that somebody else has already authenticated. It verifies no signature, reads no certificate, trust anchor or clock, opens no
//! file, and says nothing about the release being genuine: a record with a forged signature maps all the same, on purpose (contract section 7). It is not a
//! production receipt function and takes no part in one; that needs a verified release record and stays on hold (question L).
//!
//! Every receipt value has **one** primary source and every other place that holds it is a check; there is no choice between two sources. The primary source of
//! the two grammar digests is the `artifacts.files` entry of a fixed path (decision D1), the cross-checks are `provenance.grammar_*` (decision D2: absent is a
//! refusal, not a skipped check) and the pin, and only an envelope 3 record is read (decision D3).
//!
//! The base64 is **not** left to a decoder: the whole string has to be 43 standard-alphabet characters and one padding character, and the 32 bytes have to
//! encode back to the same string, so one digest has one accepted spelling in every language (the decoders of Python and Go disagree on both). There is no base64
//! crate here: the decoding of a string of that shape is a few lines, and a new dependency is not this unit's decision.
//!
//! What it refuses (a [`MappingError`] with one reason and the field it concerns, never a guess and never a value with a warning): see the `MAPPING_*` constants.
//! The checks run in the order of the contract, section 5, so a case has one expected reason.

use std::collections::BTreeMap;
use std::fmt;

use crate::node::{Node, Value};

pub const MAPPING_ENVELOPE_NOT_3: &str = "envelope_not_3";
pub const MAPPING_FIELD_MISSING: &str = "field_missing";
pub const MAPPING_FIELD_TYPE: &str = "field_type";
pub const MAPPING_BASE64_SHAPE: &str = "base64_shape";
pub const MAPPING_BASE64_NOT_CANONICAL: &str = "base64_not_canonical";
pub const MAPPING_BASE64_LENGTH: &str = "base64_length";
pub const MAPPING_DIGEST_FORM: &str = "digest_form";
pub const MAPPING_ARTIFACT_ABSENT: &str = "artifact_absent";
pub const MAPPING_ARTIFACT_DUPLICATE: &str = "artifact_duplicate";
pub const MAPPING_PROVENANCE_ARTIFACTS_DISAGREE: &str = "provenance_artifacts_disagree";
pub const MAPPING_RECORD_PIN_DISAGREE: &str = "record_pin_disagree";

const CHECK_GRAMMAR: &str = "proposals/atom-grammar/check_grammar.py";
const GRAMMAR_SCHEMA: &str = "proposals/atom-grammar/instance-grammar.schema.yaml";
const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// Why no receipt digest was made: one of the `MAPPING_*` reasons, and the member, the artifact path or the receipt field it is about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MappingError {
    pub reason: &'static str,
    pub field: String,
}

impl fmt::Display for MappingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "no digest mapping: {}", self.reason)?;
        if !self.field.is_empty() {
            write!(f, " ({})", self.field)?;
        }
        Ok(())
    }
}

impl std::error::Error for MappingError {}

fn refused(reason: &'static str, field: &str) -> MappingError {
    MappingError {
        reason,
        field: field.to_string(),
    }
}

/// The three receipt digests, each `sha256:` and 64 lowercase hex.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DigestMapping {
    pub primitive_build_hash: String,
    pub grammar_sha256: String,
    pub grammar_schema_sha256: String,
}

/// The value of a base64 character of the standard alphabet. Only called on a string whose shape has been checked.
fn sextet(c: u8) -> u8 {
    ALPHABET.iter().position(|&a| a == c).map_or(0, |p| p as u8)
}

/// Whether `s` is, as a whole, 43 characters of the standard alphabet and one padding character: the shape of a SHA-256 in base64.
fn base64_shape(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 44 && b[43] == b'=' && b[..43].iter().all(|c| ALPHABET.contains(c))
}

/// The bytes of a string of the shape of [`base64_shape`]: ten groups of four characters make thirty bytes, the last three characters and the padding two.
fn decode_shaped(s: &str) -> Vec<u8> {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(32);
    for quad in b[..40].chunks(4) {
        let n = (u32::from(sextet(quad[0])) << 18)
            | (u32::from(sextet(quad[1])) << 12)
            | (u32::from(sextet(quad[2])) << 6)
            | u32::from(sextet(quad[3]));
        out.extend_from_slice(&[(n >> 16) as u8, (n >> 8) as u8, n as u8]);
    }
    let n = (u32::from(sextet(b[40])) << 18)
        | (u32::from(sextet(b[41])) << 12)
        | (u32::from(sextet(b[42])) << 6);
    out.extend_from_slice(&[(n >> 16) as u8, (n >> 8) as u8]);
    out
}

/// The standard base64 of `raw`, with padding.
fn encode_base64(raw: &[u8]) -> String {
    let mut out = String::with_capacity(raw.len().div_ceil(3) * 4);
    for chunk in raw.chunks(3) {
        let n = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        for shift in [18, 12, 6, 0] {
            out.push(ALPHABET[((n >> shift) & 63) as usize] as char);
        }
    }
    let pad = (3 - raw.len() % 3) % 3;
    out.truncate(out.len() - pad);
    out.push_str(&"=".repeat(pad));
    out
}

/// Judges the bytes a text decoded to: exactly 32 (the shape already implies it; the check guards a decoder that returns another length), and the text is the
/// one canonical spelling of them. A function of its own so that the length guard, which no input can reach through the shape check, is exercised directly.
fn check_digest_bytes(raw: &[u8], text: &str, field: &str) -> Result<(), MappingError> {
    if raw.len() != 32 {
        return Err(refused(MAPPING_BASE64_LENGTH, field));
    }
    if encode_base64(raw) != text {
        return Err(refused(MAPPING_BASE64_NOT_CANONICAL, field));
    }
    Ok(())
}

fn hex_lower(raw: &[u8]) -> String {
    use fmt::Write;
    raw.iter()
        .fold(String::with_capacity(raw.len() * 2), |mut s, b| {
            let _ = write!(s, "{b:02x}");
            s
        })
}

/// The conversion of the contract, section 3: a base64 SHA-256 of exactly 32 bytes, in its one canonical spelling, as `sha256:` and lowercase hex. `field` names the
/// member the value was read from and goes into the refusal. The value is judged exactly as it is: nothing is trimmed, folded or coerced.
pub fn receipt_digest_of(value: &Node, field: &str) -> Result<String, MappingError> {
    let Value::Str(text) = &value.value else {
        return Err(refused(MAPPING_FIELD_TYPE, field));
    };
    if !base64_shape(text) {
        return Err(refused(MAPPING_BASE64_SHAPE, field));
    }
    let raw = decode_shaped(text);
    check_digest_bytes(&raw, text, field)?;
    Ok(format!("sha256:{}", hex_lower(&raw)))
}

/// The value at a dotted path of nested mappings, or `None` when a member on the way is absent or the parent is not a mapping.
fn get<'a>(record: &'a Node, path: &str) -> Option<&'a Node> {
    let mut cur = record;
    for part in path.split('.') {
        let Value::Map(entries) = &cur.value else {
            return None;
        };
        cur = &entries.iter().find(|e| e.key == part)?.value;
    }
    Some(cur)
}

/// Converts the base64 digest at a member of the record: an absent or null member is `field_missing` (the presence is judged first), anything else is left to
/// [`receipt_digest_of`].
fn digest_member(record: &Node, member: &str) -> Result<String, MappingError> {
    match get(record, member) {
        None => Err(refused(MAPPING_FIELD_MISSING, member)),
        Some(Node {
            value: Value::Null, ..
        }) => Err(refused(MAPPING_FIELD_MISSING, member)),
        Some(v) => receipt_digest_of(v, member),
    }
}

/// Whether `s` is, as a whole, `sha256:` and 64 lowercase hex.
fn is_receipt_digest(s: &str) -> bool {
    s.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

/// The digest of the one `artifacts.files` entry of `path`, as it is.
fn artifact_digest(files: &[Node], path: &str) -> Result<String, MappingError> {
    let found: Vec<&Node> = files
        .iter()
        .filter(|f| matches!(get(f, "path"), Some(Node { value: Value::Str(p), .. }) if p == path))
        .collect();
    let [entry] = found.as_slice() else {
        return Err(refused(
            if found.is_empty() {
                MAPPING_ARTIFACT_ABSENT
            } else {
                MAPPING_ARTIFACT_DUPLICATE
            },
            path,
        ));
    };
    let field = format!("artifacts.files[{path}].digest");
    let Some(Node {
        value: Value::Str(digest),
        ..
    }) = get(entry, "digest")
    else {
        return Err(refused(MAPPING_FIELD_TYPE, &field));
    };
    if !is_receipt_digest(digest) {
        return Err(refused(MAPPING_DIGEST_FORM, &field));
    }
    Ok(digest.clone())
}

/// Maps an authenticated envelope 3 release record to the three receipt digests, or refuses with one reason (contract sections 4 and 5). `pin` is
/// `{path: 64 lowercase hex}`, the digests of the manifest of `corpus/pins.yaml` without the `sha256:` prefix.
pub fn map_release(
    record: &Node,
    pin: &BTreeMap<String, String>,
) -> Result<DigestMapping, MappingError> {
    for member in ["release.envelope", "provenance.envelope"] {
        if !matches!(
            get(record, member),
            Some(Node {
                value: Value::Int(3),
                ..
            })
        ) {
            return Err(refused(MAPPING_ENVELOPE_NOT_3, member));
        }
    }

    let primitive_build_hash = digest_member(record, "release.build_hash")?;

    let files = match get(record, "artifacts.files") {
        None
        | Some(Node {
            value: Value::Null, ..
        }) => return Err(refused(MAPPING_FIELD_MISSING, "artifacts.files")),
        Some(Node {
            value: Value::Seq(items),
            ..
        }) => items,
        Some(_) => return Err(refused(MAPPING_FIELD_TYPE, "artifacts.files")),
    };

    let grammar_sha256 = grammar_digest(
        record,
        files,
        pin,
        ("grammar_sha256", CHECK_GRAMMAR, "grammar_sha256"),
    )?;
    let grammar_schema_sha256 = grammar_digest(
        record,
        files,
        pin,
        (
            "grammar_schema_sha256",
            GRAMMAR_SCHEMA,
            "grammar_schema_sha256",
        ),
    )?;
    Ok(DigestMapping {
        primitive_build_hash,
        grammar_sha256,
        grammar_schema_sha256,
    })
}

/// One grammar digest: the artifact entry is the primary source, `provenance.<member>` and the pin are the checks, in the order of the contract.
/// `which` is (receipt field, artifact path, provenance member).
fn grammar_digest(
    record: &Node,
    files: &[Node],
    pin: &BTreeMap<String, String>,
    which: (&str, &str, &str),
) -> Result<String, MappingError> {
    let (receipt_field, path, member) = which;
    let primary = artifact_digest(files, path)?;
    let cross = digest_member(record, &format!("provenance.{member}"))?;
    if cross != primary {
        return Err(refused(
            MAPPING_PROVENANCE_ARTIFACTS_DISAGREE,
            receipt_field,
        ));
    }
    let Some(pinned) = pin.get(path) else {
        return Err(refused(MAPPING_FIELD_MISSING, &format!("pin.{path}")));
    };
    if format!("sha256:{pinned}") != primary {
        return Err(refused(MAPPING_RECORD_PIN_DISAGREE, receipt_field));
    }
    Ok(primary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_length_guard_and_the_canonical_check_are_exercised_directly() {
        for n in [0usize, 31, 33, 64] {
            let e = check_digest_bytes(&vec![0u8; n], "", "f").unwrap_err();
            assert_eq!((e.reason, e.field.as_str()), (MAPPING_BASE64_LENGTH, "f"));
        }
        let zeros = format!("{}=", "A".repeat(43));
        assert!(check_digest_bytes(&[0u8; 32], &zeros, "f").is_ok());
        let other = format!("{}B=", "A".repeat(42));
        assert_eq!(
            check_digest_bytes(&[0u8; 32], &other, "f")
                .unwrap_err()
                .reason,
            MAPPING_BASE64_NOT_CANONICAL
        );
    }

    #[test]
    fn the_hand_written_base64_agrees_with_itself_on_every_prefix_length() {
        // encode then decode is the identity on 32 bytes, and the encoder pads like the standard: 1, 2 and 3 byte tails
        let raw: Vec<u8> = (0..32u8)
            .map(|i| i.wrapping_mul(37).wrapping_add(11))
            .collect();
        assert_eq!(decode_shaped(&encode_base64(&raw)), raw);
        assert_eq!(encode_base64(b"M"), "TQ==");
        assert_eq!(encode_base64(b"Ma"), "TWE=");
        assert_eq!(encode_base64(b"Man"), "TWFu");
        assert_eq!(encode_base64(b""), "");
    }

    #[test]
    fn the_error_text_names_the_reason_and_the_field() {
        let e = refused(MAPPING_DIGEST_FORM, "artifacts.files[x].digest");
        assert_eq!(
            e.to_string(),
            "no digest mapping: digest_form (artifacts.files[x].digest)"
        );
        assert_eq!(
            refused(MAPPING_FIELD_MISSING, "").to_string(),
            "no digest mapping: field_missing"
        );
    }
}
