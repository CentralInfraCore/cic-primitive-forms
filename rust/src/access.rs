//! Reading the Access and PolicySurface values of `access-policy/1` (cic-primitives D-018).
//!
//! The normative source is the pinned `proposals/atom-grammar/P0-4-ACCESS-GRAMMAR.md` and the pinned
//! `schemas/atomic/access.yaml` (see `corpus/pins.yaml`). This module *reads*: it checks a value and
//! returns it in a typed form. It resolves nothing (P3), decides no permission, applies no default and
//! implements no `conformance` basis.
//!
//! # Grammar version binding
//!
//! The version of the `inherit` spelling is **not** read from a document and has **no default**. It is
//! a property of the processing context and of the pinned schema (`spec.access_grammar.version`), the
//! two must agree, and one [`Grammar`] is bound for a whole document ([`bind_grammar`]). The version
//! of the pinned schema is derived from the schema's own bytes after their SHA-256 has been checked
//! against the pin ([`pinned_grammar_version`]). A missing version, a value that is not one of the
//! integers in [`SUPPORTED_GRAMMARS`] and a disagreement are three different failures. There is no
//! fallback and no conversion between versions: [`read_mode`] accepts the spelling of the bound
//! version only.
//!
//! # Codes and their source
//!
//! `INVALID_INHERIT`, `INVALID_ACCESS_VALUE` and `INVALID_CERT_PATTERN` are in the corpus registry.
//! `INHERIT_ENUM_IN_GRAMMAR_1`, `LEGACY_INHERIT_IN_GRAMMAR_2`, `GRAMMAR_VERSION_MISSING`,
//! `UNKNOWN_GRAMMAR_VERSION` and `GRAMMAR_VERSION_CONFLICT` are the upstream contract's own codes
//! (P0-4-ACCESS-GRAMMAR.md, "Spelling and versions" and "Grammar version binding"); they are **not**
//! in `corpus/codes.yaml`, and no vector uses them yet. They are emitted unchanged, not renamed.
//! `GRAMMAR_DIGEST_MISMATCH` (ADR 0002) reports a pinned schema whose bytes differ from the pin.

use sha2::{Digest, Sha256};

use crate::error::{Error, Location, Phase, Violation};
use crate::node::{Node, Value};
use crate::parse::parse;

/// The grammar versions the pinned contract defines.
pub const SUPPORTED_GRAMMARS: [i64; 2] = [1, 2];

/// The `access`/`modify` symbol that means "no field-level rule".
pub const INHERIT_SYMBOL: &str = "inherit";

/// A grammar version, bound once for a whole document.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Grammar(i64);

impl Grammar {
    /// A grammar version a pinned profile defines (the schema reader's use); a document binds its
    /// own through [`bind_grammar`].
    pub(crate) fn pinned(version: i64) -> Self {
        Grammar(version)
    }

    #[must_use]
    pub fn version(self) -> i64 {
        self.0
    }
}

fn binding(code: &'static str) -> Error {
    Error::Binding(code)
}

fn is_missing(v: Option<&Value>) -> bool {
    match v {
        None => true,
        Some(Value::Null) => true,
        Some(_) => false,
    }
}

/// A version is an integer that is one of [`SUPPORTED_GRAMMARS`]. `None` and `null` are missing.
fn checked(v: Option<&Value>) -> Result<i64, Error> {
    match v {
        None | Some(Value::Null) => Err(binding("GRAMMAR_VERSION_MISSING")),
        Some(Value::Int(n)) if SUPPORTED_GRAMMARS.contains(n) => Ok(*n),
        Some(_) => Err(binding("UNKNOWN_GRAMMAR_VERSION")),
    }
}

/// Binds the version of the processing context against the version of the pinned schema.
///
/// Both are required (no default to 1). A missing one is `GRAMMAR_VERSION_MISSING`, a value that is not
/// one of the supported integers (`"1"`, `true`, `3`) is `UNKNOWN_GRAMMAR_VERSION`, a difference is
/// `GRAMMAR_VERSION_CONFLICT`. Missing is checked before unknown, unknown before conflict.
///
/// # Errors
/// [`Error::Binding`] with one of those codes.
pub fn bind_grammar(context: Option<&Value>, pinned: Option<&Value>) -> Result<Grammar, Error> {
    if is_missing(context) || is_missing(pinned) {
        return Err(binding("GRAMMAR_VERSION_MISSING"));
    }
    let c = checked(context)?;
    let p = checked(pinned)?;
    if c != p {
        return Err(binding("GRAMMAR_VERSION_CONFLICT"));
    }
    Ok(Grammar(c))
}

/// The lower-case hexadecimal SHA-256 of `bytes`.
#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The `spec.access_grammar.version` of the pinned `access.yaml`, **after** its bytes have been
/// verified against `expected_sha256`. `None` when the verified schema declares none.
///
/// # Errors
/// `GRAMMAR_DIGEST_MISMATCH` ([`Error::Binding`]) when the bytes are not the pinned ones; the errors of
/// [`parse`] when the verified bytes are outside the supported YAML subset.
pub fn pinned_grammar_version(
    schema: &[u8],
    expected_sha256: &str,
) -> Result<Option<Value>, Error> {
    if sha256_hex(schema) != expected_sha256 {
        return Err(binding("GRAMMAR_DIGEST_MISMATCH"));
    }
    let text = std::str::from_utf8(schema).map_err(|e| Error::Syntax(e.to_string()))?;
    let doc = parse(text)?;
    let mut at = &doc;
    for key in ["spec", "access_grammar", "version"] {
        let next = match &at.value {
            Value::Map(entries) => entries.iter().find(|e| e.key == key).map(|e| &e.value),
            _ => None,
        };
        match next {
            Some(n) => at = n,
            None => return Ok(None),
        }
    }
    Ok(Some(at.value.clone()))
}

/// `InheritanceMode`, one concept at two scopes (`Access.inherit`, `PolicySurface.inherit_policy`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Inherit,
    NoInherit,
    Reset,
}

fn invalid(code: &'static str, node: &Node) -> Violation {
    Violation {
        code,
        phase: Phase::Validate,
        location: node.pos.map_or(Location::Unavailable, Location::At),
        logical_path: None,
        path_offset: None,
    }
}

const MODE_NAMES: [&str; 3] = ["inherit", "no-inherit", "reset"];

/// Reads a spelling of `InheritanceMode` under the bound grammar. Exactly one spelling set is valid per
/// version, the two are never mixed and never coerced.
///
/// * grammar 1: boolean `true` is `inherit`, boolean `false` is `no-inherit`, integer `0` is `reset`;
///   told apart by the type of the scalar, so `false` and `0` are never equal. An enum name is
///   `INHERIT_ENUM_IN_GRAMMAR_1`; anything else (`1`, `"true"`, `"0"`, `null`, a list) is
///   `INVALID_INHERIT`.
/// * grammar 2: the strings `inherit`, `no-inherit`, `reset`. A legacy spelling (a boolean or integer
///   `0`) is `LEGACY_INHERIT_IN_GRAMMAR_2`; anything else is `INVALID_INHERIT`.
///
/// # Errors
/// A [`Violation`] with the codes above, anchored at the value.
pub fn read_mode(node: &Node, grammar: Grammar) -> Result<Mode, Violation> {
    let boolean =
        std::mem::discriminant(&node.value) == std::mem::discriminant(&Value::Bool(false));
    let zero = if let Value::Int(n) = node.value {
        n == 0
    } else {
        false
    };
    let legacy = boolean || zero;
    let enum_name = match &node.value {
        Value::Str(s) => MODE_NAMES.iter().position(|m| m == s),
        _ => None,
    };
    if grammar.0 == 1 {
        return match (&node.value, enum_name) {
            (Value::Bool(true), _) => Ok(Mode::Inherit),
            (Value::Bool(false), _) => Ok(Mode::NoInherit),
            (Value::Int(0), _) => Ok(Mode::Reset),
            (_, Some(_)) => Err(invalid("INHERIT_ENUM_IN_GRAMMAR_1", node)),
            _ => Err(invalid("INVALID_INHERIT", node)),
        };
    }
    match enum_name {
        Some(0) => Ok(Mode::Inherit),
        Some(1) => Ok(Mode::NoInherit),
        Some(_) => Ok(Mode::Reset),
        None if legacy => Err(invalid("LEGACY_INHERIT_IN_GRAMMAR_2", node)),
        None => Err(invalid("INVALID_INHERIT", node)),
    }
}

/// The inverse of [`read_mode`]: a bijection per grammar version, so a round trip keeps the spelling.
#[must_use]
pub fn write_mode(mode: Mode, grammar: Grammar) -> Node {
    let value = if grammar.0 == 1 {
        match mode {
            Mode::Inherit => Value::Bool(true),
            Mode::NoInherit => Value::Bool(false),
            Mode::Reset => Value::Int(0),
        }
    } else {
        Value::Str(
            match mode {
                Mode::Inherit => "inherit",
                Mode::NoInherit => "no-inherit",
                Mode::Reset => "reset",
            }
            .to_string(),
        )
    };
    Node::new(value)
}

/// The value of `access` or `modify`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rule {
    /// The symbol `inherit`: no field-level rule.
    Symbol,
    /// A list of CertPatterns; the empty list is an explicit "nobody", never the symbol.
    List(Vec<String>),
}

/// Whether `c` ends the first character of a pattern value: the upstream pattern uses `\s`, which in
/// Python is the Unicode White_Space property plus U+001C to U+001F.
fn pattern_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn is_attr(a: &str) -> bool {
    let mut chars = a.chars();
    let first_ok = chars.next().is_some_and(|c| c.is_ascii_alphabetic());
    first_ok && chars.all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '-')
}

/// A CertPattern: `attr=value` pairs joined by `,` with nothing between them. `attr` is
/// `[A-Za-z][A-Za-z0-9.-]*`; a value is non-empty, does not start with `=`, `,` or a space, and holds
/// no `=` or `,`. A bare `inherit` is not a pattern, and neither is `OU=a, O=b`.
#[must_use]
pub fn is_cert_pattern(p: &str) -> bool {
    p.split(',').all(|pair| match pair.split_once('=') {
        Some((attr, value)) => {
            is_attr(attr)
                && !value.contains('=')
                && value.chars().next().is_some_and(|c| !pattern_space(c))
        }
        None => false,
    })
}

/// Reads an `access` / `modify` value: a list of CertPatterns or the symbol `inherit`.
///
/// `[]` is an explicit empty set (nobody), `[inherit]` is a list holding an invalid CertPattern
/// (`INVALID_CERT_PATTERN`), and `null`, a bare pattern string, a boolean or a mapping is
/// `INVALID_ACCESS_VALUE`.
///
/// # Errors
/// A [`Violation`] anchored at the value.
pub fn read_rule(node: &Node) -> Result<Rule, Violation> {
    match &node.value {
        Value::Seq(items) => {
            let mut patterns = Vec::with_capacity(items.len());
            for item in items {
                match &item.value {
                    Value::Str(s) if is_cert_pattern(s) => patterns.push(s.clone()),
                    _ => return Err(invalid("INVALID_CERT_PATTERN", node)),
                }
            }
            Ok(Rule::List(patterns))
        }
        Value::Str(s) if s == INHERIT_SYMBOL => Ok(Rule::Symbol),
        _ => Err(invalid("INVALID_ACCESS_VALUE", node)),
    }
}
