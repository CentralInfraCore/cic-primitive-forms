//! `edit_scalar` (P3b, step E3; ADR 0012 and `docs/P3B-SOURCE-PRESERVING-EDIT.md`): replace one existing, single-line scalar of a document in its
//! original text and leave every other byte alone. The Go peer is `go/editscalar.go`.
//!
//! The refusals of the `edit` phase come in the fixed order of ADR 0012, section 4: the ones of the span (`EDIT_NOT_A_SCALAR` ... `EDIT_MULTILINE_SCALAR`,
//! from [`source_span`](crate::sourcespan::source_span)), then `EDIT_VALUE_NOT_REPRESENTABLE`, `EDIT_TYPE_MISMATCH`, `EDIT_STYLE_CONFLICT`; after the
//! text is built, the guarantees are checked on every call (`EDIT_INVALID_RESULT`, and the internal assertion `EDIT_SPAN_MISMATCH`).

use crate::error::Error;
use crate::node::{Entry, Node, Value};
use crate::parse::parse;
use crate::pathtext::{parse_path, resolve_path};
use crate::recognize::{classify, Class};
use crate::sourcespan::{failure, path_failure, span_of, SourceSpan, SpanStyle};
use crate::validate::{validate, ScalarType, Shape, ShapeType};

/// A successful edit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditResult {
    /// The edited text; for a no-op it is the input.
    pub text: String,
    /// Where the scalar is in `text`.
    pub span: SourceSpan,
    /// False when the position already held the value: nothing is rewritten, not even to the canonical spelling.
    pub changed: bool,
}

/// Replaces the scalar at `path` in `text` by `value` (a scalar [`Node`]: a string, an integer, a boolean or null; its position is not read) and leaves
/// every other byte alone.
///
/// # Errors
/// A refusal of `parse`, `validate` or the path operations as it is, or a [`Violation`](crate::Violation) of phase `edit` in the fixed order of
/// ADR 0012; no text comes with an error.
pub fn edit_scalar(
    text: &str,
    root: &Shape,
    path: &str,
    a: &[String],
    value: &Node,
) -> Result<EditResult, Error> {
    let doc = parse(text)?;
    refuse_invalid(&doc, root, a)?;
    let (span, found) = span_of(text, &doc, root, path, a)?;
    let node = found
        .value
        .ok_or_else(|| failure("EDIT_SPAN_MISMATCH", None, path))?;
    let pos = node.pos;
    if !representable(value) {
        return Err(failure("EDIT_VALUE_NOT_REPRESENTABLE", pos, path));
    }
    if !carries(found.shape, value) {
        return Err(failure("EDIT_TYPE_MISMATCH", pos, path));
    }
    if node.same(value) {
        return Ok(EditResult {
            text: text.to_string(),
            span,
            changed: false,
        });
    }
    let token =
        token_for(span.style, value).ok_or_else(|| failure("EDIT_STYLE_CONFLICT", pos, path))?;
    let out = format!("{}{token}{}", &text[..span.start], &text[span.end..]);
    let want = replaced(&doc, span.start, value);
    if let Some(code) = verify_edit(text, &span, &out, root, a, &want) {
        return Err(failure(code, pos, path));
    }
    let end = span.start + token.len();
    Ok(EditResult {
        text: out,
        span: SourceSpan {
            start: span.start,
            end,
            style: span.style,
        },
        changed: true,
    })
}

/// The check of the guarantees on the text that was built, on every call: `None` when they hold, otherwise the code of the refusal. The bytes
/// outside the span are the original's, the result parses and validates (`EDIT_INVALID_RESULT`), and it is the expected tree (`EDIT_SPAN_MISMATCH`, an
/// internal assertion: no input can make it fail on purpose, its proof is a direct test and the seeded defects of step E4).
fn verify_edit(
    text: &str,
    span: &SourceSpan,
    out: &str,
    root: &Shape,
    a: &[String],
    want: &Node,
) -> Option<&'static str> {
    if !(out.starts_with(&text[..span.start]) && out.ends_with(&text[span.end..])) {
        return Some("EDIT_SPAN_MISMATCH");
    }
    match parse(out) {
        Ok(parsed) if refuse_invalid(&parsed, root, a).is_ok() => {
            (!parsed.same(want)).then_some("EDIT_SPAN_MISMATCH")
        }
        _ => Some("EDIT_INVALID_RESULT"),
    }
}

/// A copy of the tree with the scalar that starts at byte `offset` replaced by `value` (a scalar is the only node that starts there).
fn replaced(n: &Node, offset: usize, value: &Node) -> Node {
    let v = match &n.value {
        Value::Map(entries) => Value::Map(
            entries
                .iter()
                .map(|e| Entry {
                    key: e.key.clone(),
                    key_pos: e.key_pos,
                    value: replaced(&e.value, offset, value),
                })
                .collect(),
        ),
        Value::Seq(items) => Value::Seq(items.iter().map(|i| replaced(i, offset, value)).collect()),
        _ if n.pos.is_some_and(|p| p.offset == offset) => value.value.clone(),
        other => other.clone(),
    };
    Node {
        value: v,
        pos: n.pos,
    }
}

/// What an edit of a document that was supplied as a parsed value is: there is no text to point into (`EDIT_NO_SOURCE_TEXT`), after the document and
/// the path have been accepted. Always an error.
#[must_use]
pub fn edit_parsed(doc: &Node, root: &Shape, path: &str, a: &[String], _value: &Node) -> Error {
    if let Err(e) = refuse_invalid(doc, root, a) {
        return e;
    }
    let resolved = parse_path(path).and_then(|segs| resolve_path(root, &segs));
    match resolved {
        Err(e) => path_failure(e),
        Ok(_) => failure("EDIT_NO_SOURCE_TEXT", None, path),
    }
}

/// The validate phase as an error: the violations, as they are. An expanded Access wrapper is read as its value ([`short_view`]).
fn refuse_invalid(doc: &Node, root: &Shape, a: &[String]) -> Result<(), Error> {
    let v = validate(Some(&short_view(doc, a)), root, a)?;
    if v.violations.is_empty() {
        Ok(())
    } else {
        Err(Error::Contract(v.violations))
    }
}

/// The document as the validator reads it: an expanded Access wrapper is replaced by its value. The other members of a wrapper are the
/// recognition's, and an edit of the value cannot change them. Positions are kept.
fn short_view(n: &Node, a: &[String]) -> Node {
    match &n.value {
        Value::Map(entries) => {
            if let (Ok(Class::Expanded), Some(v)) = (
                classify(n, a),
                entries.iter().find(|e| e.key == "value").map(|e| &e.value),
            ) {
                return short_view(v, a);
            }
            Node {
                value: Value::Map(
                    entries
                        .iter()
                        .map(|e| Entry {
                            key: e.key.clone(),
                            key_pos: e.key_pos,
                            value: short_view(&e.value, a),
                        })
                        .collect(),
                ),
                pos: n.pos,
            }
        }
        Value::Seq(items) => Node {
            value: Value::Seq(items.iter().map(|i| short_view(i, a)).collect()),
            pos: n.pos,
        },
        _ => n.clone(),
    }
}

/// Whether the value can be written at all: a string without a line break or a control character (reading is wider than writing: an existing token
/// with an escaped control character is read and can be replaced, a new value never holds one). Any other kind passes here; a non-scalar one is
/// refused by `carries` as `EDIT_TYPE_MISMATCH`. A `&str` is valid UTF-8 by construction.
fn representable(v: &Node) -> bool {
    match &v.value {
        Value::Str(s) => !s.chars().any(unwritable),
        _ => true,
    }
}

fn unwritable(c: char) -> bool {
    c.is_control() || c == '\u{2028}' || c == '\u{2029}'
}

/// Whether the position can hold the value: its kind is the scalar type of the shape, or null where the shape is nullable.
fn carries(shape: Option<&Shape>, v: &Node) -> bool {
    shape.is_some_and(|shape| match (&v.value, &shape.shape_type) {
        (Value::Null, _) => shape.nullable,
        (Value::Str(_), ShapeType::Scalar(ScalarType::String))
        | (Value::Int(_), ShapeType::Scalar(ScalarType::Integer))
        | (Value::Bool(_), ShapeType::Scalar(ScalarType::Boolean)) => true,
        _ => false,
    })
}

/// The canonical spelling of the value in the style of the scalar; `None` when the style cannot carry it (the edit never changes a style).
fn token_for(style: SpanStyle, v: &Node) -> Option<String> {
    let s = match &v.value {
        Value::Str(s) => Some(s),
        _ => None,
    };
    match style {
        SpanStyle::SingleQuoted => s.map(|s| format!("'{}'", s.replace('\'', "''"))),
        SpanStyle::DoubleQuoted => {
            s.map(|s| format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"")))
        }
        SpanStyle::Plain => match &v.value {
            Value::Int(i) => Some(i.to_string()),
            Value::Bool(b) => Some(b.to_string()),
            Value::Null => Some("null".to_string()),
            _ => s.filter(|s| plain_safe(s)).cloned(),
        },
    }
}

/// Whether the string, written as a plain scalar, reads back as the same string: it does not start with an indicator (the one thing the parser alone
/// would let through, as in `-a`), and the parser reads it back as that very string. The round trip is what rules out the rest of the closed rule of
/// the design note: an empty string, a leading or trailing space, `: ` or ` #` inside, a final colon, and everything the parser reads as another
/// kind (`true`, `123`, `null`, `~`, `1.5`).
fn plain_safe(s: &str) -> bool {
    !s.starts_with(|c| "-?:,[]{}#&*!|>'\"%@`".contains(c))
        && parse(s).is_ok_and(|n| n.same(&Node::new(Value::Str(s.to_string()))))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::validate::Presence;

    fn shape(shape_type: ShapeType) -> Shape {
        Shape {
            shape_type,
            presence: Presence::Optional,
            nullable: false,
            has_default: false,
            default: None,
        }
    }

    fn root() -> Shape {
        shape(ShapeType::Composite(vec![
            ("n".into(), shape(ShapeType::Scalar(ScalarType::String))),
            (
                "count".into(),
                shape(ShapeType::Scalar(ScalarType::Integer)),
            ),
        ]))
    }

    fn a() -> Vec<String> {
        [
            "value",
            "access",
            "modify",
            "inherit",
            "default_injection",
            "conformance",
        ]
        .iter()
        .map(|k| (*k).to_string())
        .collect()
    }

    fn node(text: &str) -> Node {
        parse(text).expect("parses")
    }

    /// The guarantee check of `edit_scalar`, directly: no input of the public function can make it fail on purpose (ADR 0012, `EDIT_SPAN_MISMATCH` is
    /// an internal assertion), so each condition is given a text that breaks only that condition.
    #[test]
    fn verify_edit_names_which_guarantee_failed() {
        let text = "n: abc\ncount: 3\n";
        let span = SourceSpan {
            start: 3,
            end: 6,
            style: SpanStyle::Plain,
        };
        let want = node("n: xyz\ncount: 3\n");
        let check = |out: &str| verify_edit(text, &span, out, &root(), &a(), &want);
        assert_eq!(check("n: xyz\ncount: 3\n"), None);
        assert_eq!(check("m: xyz\ncount: 3\n"), Some("EDIT_SPAN_MISMATCH"));
        assert_eq!(check("n: xyz\ncount: 4\n"), Some("EDIT_SPAN_MISMATCH"));
        assert_eq!(check("n: 'xyz\ncount: 3\n"), Some("EDIT_INVALID_RESULT"));
        assert_eq!(
            check("n: xyz\nbogus: 1\ncount: 3\n"),
            Some("EDIT_INVALID_RESULT")
        );
        assert_eq!(check("n: xyw\ncount: 3\n"), Some("EDIT_SPAN_MISMATCH"));
        // only the suffix is wrong (the prefix is right and the tree is the expected one), and only the prefix is wrong (the suffix is right and the tree
        // is the expected one): each half of the byte condition is read on its own, and the comparison of the trees cannot make up for it
        let short = SourceSpan {
            start: 3,
            end: 6,
            style: SpanStyle::Plain,
        };
        let short_want = node("n: xyz\n");
        let c = |text: &str, out: &str, want: &Node| {
            verify_edit(text, &short, out, &root(), &a(), want)
        };
        assert_eq!(
            c("n: abc\n", "n: xyz", &short_want),
            Some("EDIT_SPAN_MISMATCH")
        );
        assert_eq!(
            c("n: abc\n", "m: xyz\n", &node("m: xyz\n")),
            Some("EDIT_SPAN_MISMATCH")
        );
    }

    #[test]
    fn replaced_keeps_the_tree_and_replaces_one_scalar() {
        let doc = node("n: abc\nlist:\n  - abc\n  - 4\n");
        // the scalar of `n` starts at byte 3; the item `abc` at 17 is the same text and stays
        let got = replaced(&doc, 3, &Node::new(Value::Str("xyz".into())));
        assert!(got.same(&node("n: xyz\nlist:\n  - abc\n  - 4\n")));
        assert!(doc.same(&node("n: abc\nlist:\n  - abc\n  - 4\n")));
        assert!(replaced(&doc, 17, &Node::new(Value::Int(9)))
            .same(&node("n: abc\nlist:\n  - 9\n  - 4\n")));
        assert!(replaced(&doc, 999, &Node::new(Value::Str("zzz".into()))).same(&doc));
    }

    #[test]
    fn carries_and_representable_at_their_edges() {
        let string = shape(ShapeType::Scalar(ScalarType::String));
        assert!(!carries(None, &Node::new(Value::Str(String::new()))));
        assert!(carries(
            Some(&string),
            &Node::new(Value::Str(String::new()))
        ));
        assert!(!carries(Some(&string), &Node::new(Value::Seq(vec![]))));
        for v in [
            Value::Str("ok é".into()),
            Value::Int(1),
            Value::Bool(true),
            Value::Null,
        ] {
            assert!(representable(&Node::new(v)));
        }
        for s in [
            "a\0",
            "a\tb",
            "a\nb",
            "a\u{7f}",
            "a\u{85}",
            "a\u{2028}",
            "a\u{2029}",
        ] {
            assert!(!representable(&Node::new(Value::Str(s.into()))), "{s:?}");
        }
    }
}
