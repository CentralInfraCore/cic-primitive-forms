//! The canonical bytes and the digest of a processed value (P5-1; ADR 0014, engine `MATERIALIZATION-SPEC.md` section A, A1-A7).
//!
//! The form is the engine's, not a second one: canonical JSON without whitespace, object keys sorted by raw UTF-8 byte order, arrays in the order given,
//! integers as decimal digits, the string escaping of Go's `json.Marshal` (the five characters `<`, `>`, `&`, U+2028, U+2029 escaped, `\b` `\t` `\n` `\f` `\r`
//! short, any other control character below U+0020 as lowercase `\u00xx`), no Unicode normalization (the named gap of A4). The digest is `sha256:` and the
//! lowercase hex of the SHA-256 of those bytes (A7).
//!
//! The range is what this library holds: null, boolean, signed 64-bit integer, string, sequence, map. A float or an integer beyond 64 bits ([`Value::Unsupported`])
//! is refused, never approximated, and so is a map that repeats a key (the engine's `Map` cannot hold one). The vectors of the engine outside this range are
//! out of range here by name (docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md, decision G). The order of a set is not decided (decision H); a sequence is written as given.
//!
//! The error is a [`CanonicalError`]; it is a refusal of this module, not a code of the registry of ADR 0007. The Go peer is go/canonical.go.

use std::fmt;

use crate::access::sha256_hex;
use crate::node::{Node, Value};

pub const CANONICAL_UNSUPPORTED_VALUE: &str = "unsupported_value";
pub const CANONICAL_DUPLICATE_KEY: &str = "duplicate_key";

/// Why a value has no canonical bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CanonicalError {
    pub reason: &'static str,
}

impl fmt::Display for CanonicalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "no canonical bytes: {}", self.reason)
    }
}

impl std::error::Error for CanonicalError {}

/// The canonical bytes of `node` (A1-A6).
///
/// # Errors
/// `unsupported_value` for a float or an integer beyond 64 bits, `duplicate_key` for a map that repeats a key.
pub fn canonical_bytes(node: &Node) -> Result<Vec<u8>, CanonicalError> {
    let mut out = Vec::new();
    write_value(&mut out, node)?;
    Ok(out)
}

/// `sha256:` and the lowercase hex of the SHA-256 of `bytes` (A7).
#[must_use]
pub fn canonical_digest(bytes: &[u8]) -> String {
    format!("sha256:{}", sha256_hex(bytes))
}

fn write_value(out: &mut Vec<u8>, node: &Node) -> Result<(), CanonicalError> {
    match &node.value {
        Value::Null => out.extend_from_slice(b"null"),
        Value::Bool(b) => out.extend_from_slice(if *b { b"true" } else { b"false" }),
        Value::Int(i) => out.extend_from_slice(i.to_string().as_bytes()),
        Value::Str(s) => write_string(out, s),
        Value::Seq(items) => {
            out.push(b'[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_value(out, item)?;
            }
            out.push(b']');
        }
        Value::Map(entries) => {
            let mut sorted: Vec<_> = entries.iter().collect();
            sorted.sort_by(|a, b| a.key.as_bytes().cmp(b.key.as_bytes()));
            if sorted.windows(2).any(|w| w[0].key == w[1].key) {
                return Err(CanonicalError {
                    reason: CANONICAL_DUPLICATE_KEY,
                });
            }
            out.push(b'{');
            for (i, e) in sorted.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_string(out, &e.key);
                out.push(b':');
                write_value(out, &e.value)?;
            }
            out.push(b'}');
        }
        Value::Unsupported(_) => {
            return Err(CanonicalError {
                reason: CANONICAL_UNSUPPORTED_VALUE,
            })
        }
    }
    Ok(())
}

fn write_string(out: &mut Vec<u8>, s: &str) {
    out.push(b'"');
    for ch in s.chars() {
        match ch {
            '"' => out.extend_from_slice(b"\\\""),
            '\\' => out.extend_from_slice(b"\\\\"),
            '\u{08}' => out.extend_from_slice(b"\\b"),
            '\u{09}' => out.extend_from_slice(b"\\t"),
            '\u{0A}' => out.extend_from_slice(b"\\n"),
            '\u{0C}' => out.extend_from_slice(b"\\f"),
            '\u{0D}' => out.extend_from_slice(b"\\r"),
            '\u{00}'..='\u{1F}' | '<' | '>' | '&' | '\u{2028}' | '\u{2029}' => {
                out.extend_from_slice(format!("\\u{:04x}", ch as u32).as_bytes());
            }
            _ => {
                let mut buf = [0u8; 4];
                out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
            }
        }
    }
    out.push(b'"');
}
