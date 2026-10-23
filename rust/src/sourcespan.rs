//! `source_span` (P3b, step E2; ADR 0012 and `docs/P3B-SOURCE-PRESERVING-EDIT.md`): where, in the original text, the scalar of one position is.
//! Read only: it parses the text, finds the position by its logical path, and returns the half-open byte span of the scalar and its
//! style. It changes nothing, and `Node` and every P1-P3 result are as they were. The Go peer is `go/sourcespan.go`.
//!
//! The extent of a scalar is found by a small scanner over the supported subset and **verified by reading it back**: the span, parsed
//! alone as a document, must give the value the parser gave for that node. A scanner defect therefore cannot silently point at the wrong
//! bytes; at worst it refuses.
//!
//! Not in this step: `validate` of the document (P1 `validate` does not read an expanded document, and the edit of step E3 validates its result),
//! and the edit itself.

use crate::error::{Error, Location, Phase, Violation};
use crate::expand::entry;
use crate::listitem::{identify, Elements};
use crate::node::{Node, Pos, Value};
use crate::parse::parse;
use crate::pathtext::{parse_path, resolve_path, PathError, PathSeg};
use crate::recognize::{classify, Class};
use crate::validate::{Shape, ShapeType};

/// The style of a scalar's text: the ones this unit can replace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SpanStyle {
    Plain,
    SingleQuoted,
    DoubleQuoted,
}

impl SpanStyle {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            SpanStyle::Plain => "plain",
            SpanStyle::SingleQuoted => "single_quoted",
            SpanStyle::DoubleQuoted => "double_quoted",
        }
    }
}

/// The scalar's own text: bytes `[start, end)` of the original UTF-8 input, a leading BOM included (the offset of ADR 0007).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SourceSpan {
    pub start: usize,
    pub end: usize,
    pub style: SpanStyle,
}

fn failure(code: &'static str, at: Option<Pos>, path: &str) -> Error {
    Error::Contract(vec![Violation {
        code,
        phase: Phase::Edit,
        location: at.map_or(Location::Unavailable, Location::At),
        logical_path: Some(path.to_string()),
        path_offset: None,
    }])
}

fn path_failure(e: PathError) -> Error {
    // (outside the macro: the branch tool does not enumerate an `if` inside a macro invocation)
    let path_offset = if e.phase == Phase::PathParse {
        Some(e.offset)
    } else {
        None
    };
    Error::Contract(vec![Violation {
        code: e.code,
        phase: e.phase,
        location: Location::NotApplicable,
        logical_path: e.logical_path,
        path_offset,
    }])
}

/// The node whose value is meant: through an expanded Access wrapper to its `value`, otherwise the node itself.
fn through<'a>(n: &'a Node, a: &[String]) -> (Option<&'a Node>, bool) {
    match classify(n, a) {
        Ok(Class::Expanded) => (entry(n, "value"), true),
        _ => (Some(n), false),
    }
}

/// What the walk along the path found: the value node of the position (if the document spells one), the node whose position an "absent"
/// error is anchored at (the enclosing structure), and the mapping or sequence that directly holds the scalar (to tell a flow context).
struct Found<'n> {
    value: Option<&'n Node>,
    parent: &'n Node,
    container: &'n Node,
}

fn locate<'n>(doc: &'n Node, root: &Shape, segs: &[PathSeg], a: &[String]) -> Found<'n> {
    let mut fields: &[(String, Shape)] = match &root.shape_type {
        ShapeType::Composite(f) => f,
        _ => &[],
    };
    let mut elements: Option<&Elements> = None;
    let mut parent = doc;
    let mut container = doc;
    let mut value = Some(doc);
    for seg in segs {
        let here = match value {
            Some(n) => n,
            None => break,
        };
        parent = here;
        match seg {
            PathSeg::Field(name) => {
                let shape = fields.iter().find(|(n, _)| n == name).map(|(_, s)| s);
                container = here;
                let position = entry(here, name);
                let (v, wrapped) = match position {
                    Some(p) => through(p, a),
                    None => (None, false),
                };
                if wrapped {
                    container = position.unwrap_or(here);
                }
                value = v;
                if let Some(sh) = shape {
                    match &sh.shape_type {
                        ShapeType::Composite(f) => fields = f,
                        ShapeType::DeclaredList { elements: e, .. } => elements = Some(e),
                        _ => {}
                    }
                }
            }
            PathSeg::Item(comps) => {
                let found = match (&here.value, elements) {
                    (Value::Seq(items), Some(els)) => items.iter().find_map(|rec| {
                        identify(rec, els, a)
                            .ok()
                            .filter(|(_, id)| id == comps)
                            .map(|(case, _)| (rec, case))
                    }),
                    _ => None,
                };
                container = here;
                match found {
                    Some((rec, case)) => {
                        value = Some(rec);
                        fields = &case.fields;
                    }
                    None => value = None,
                }
            }
        }
    }
    Found {
        value,
        parent,
        container,
    }
}

/// Scans the text of the scalar that starts at `start`; `Err` is the code of the refusal.
fn scan(text: &str, start: usize) -> Result<(usize, SpanStyle), &'static str> {
    let bytes = text.as_bytes();
    let at = |i: usize| bytes.get(i).copied();
    match at(start) {
        Some(b'|' | b'>') => Err("EDIT_BLOCK_SCALAR"),
        Some(b'\'') => {
            let mut i = start + 1;
            loop {
                match at(i) {
                    Some(b'\'') if at(i + 1) == Some(b'\'') => i += 2,
                    Some(b'\'') => return Ok((i + 1, SpanStyle::SingleQuoted)),
                    Some(b'\n' | b'\r') | None => return Err("EDIT_MULTILINE_SCALAR"),
                    Some(_) => i += 1,
                }
            }
        }
        Some(b'"') => {
            let mut i = start + 1;
            loop {
                match at(i) {
                    // a backslash escapes the next byte; one at the very end leaves nothing, and the next turn finds the end of the text
                    Some(b'\\') if matches!(at(i + 1), Some(b'\n' | b'\r')) => {
                        return Err("EDIT_MULTILINE_SCALAR")
                    }
                    Some(b'\\') => i += 2,
                    Some(b'"') => return Ok((i + 1, SpanStyle::DoubleQuoted)),
                    Some(b'\n' | b'\r') | None => return Err("EDIT_MULTILINE_SCALAR"),
                    Some(_) => i += 1,
                }
            }
        }
        _ => {
            // a plain scalar ends at the end of its line, before a comment (a `#` that follows a space; the parser refuses a tab anywhere) and
            // before the spaces in front of it
            let line = text[start..].split(['\n', '\r']).next().unwrap_or_default();
            let cut = line.find(" #").unwrap_or(line.len());
            Ok((
                start + line[..cut].trim_end_matches(' ').len(),
                SpanStyle::Plain,
            ))
        }
    }
}

/// The read-back: the span, parsed alone as a document, must be the value the parser gave for the node. `Err` is the code of the refusal: a plain
/// scalar whose value is longer than its first line is multi-line, anything else that disagrees is a defect of the scanner.
fn read_back(token: &str, node: &Node, style: SpanStyle) -> Result<(), &'static str> {
    if parse(token).is_ok_and(|parsed| parsed.same(node)) {
        return Ok(());
    }
    match (&node.value, style) {
        (Value::Str(s), SpanStyle::Plain)
            if s.strip_prefix(token).is_some_and(|rest| !rest.is_empty()) =>
        {
            Err("EDIT_MULTILINE_SCALAR")
        }
        _ => Err("EDIT_SPAN_MISMATCH"),
    }
}

/// The span of the scalar that holds the value of the position `path` in `text`, read against `root` (a composite).
///
/// # Errors
/// The refusal of an earlier phase as it is (`parse`, `path_parse`, `path_resolve`), or a [`Violation`] of phase `edit` with one of
/// `EDIT_NOT_A_SCALAR`, `EDIT_NO_SOURCE_VALUE`, `EDIT_BLOCK_SCALAR`, `EDIT_FLOW_CONTEXT`, `EDIT_MULTILINE_SCALAR`, or `EDIT_SPAN_MISMATCH` (an
/// internal assertion that failed: a defect, reported and not repaired).
pub fn source_span(
    text: &str,
    root: &Shape,
    path: &str,
    a: &[String],
) -> Result<SourceSpan, Error> {
    let doc = parse(text)?;
    let segs = parse_path(path).map_err(path_failure)?;
    let resolved = resolve_path(root, &segs).map_err(path_failure)?;
    // the path text is strict (ADR 0006): what parsed is canonical, and it is the logical path of the position
    let canonical = path;
    let found = locate(&doc, root, &segs, a);
    let anchor_parent = found.parent.pos;
    if resolved.target != "scalar" {
        return Err(failure(
            "EDIT_NOT_A_SCALAR",
            found.value.and_then(|v| v.pos).or(anchor_parent),
            canonical,
        ));
    }
    let node = match found.value {
        Some(n) if n.pos.is_some() => n,
        _ => return Err(failure("EDIT_NO_SOURCE_VALUE", anchor_parent, canonical)),
    };
    if matches!(node.value, Value::Map(_) | Value::Seq(_)) {
        return Err(failure("EDIT_NOT_A_SCALAR", node.pos, canonical));
    }
    let bytes = text.as_bytes();
    let start = node.pos.map_or(0, |p| p.offset);
    if found
        .container
        .pos
        .is_some_and(|p| matches!(bytes.get(p.offset), Some(b'{' | b'[')))
    {
        return Err(failure("EDIT_FLOW_CONTEXT", node.pos, canonical));
    }
    let (end, style) = scan(text, start).map_err(|code| failure(code, node.pos, canonical))?;
    let token = text
        .get(start..end)
        .ok_or_else(|| failure("EDIT_SPAN_MISMATCH", node.pos, canonical))?;
    read_back(token, node, style).map_err(|code| failure(code, node.pos, canonical))?;
    Ok(SourceSpan { start, end, style })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(text: &str) -> Node {
        parse(text).unwrap()
    }

    #[test]
    fn a_span_that_does_not_read_back_to_the_value_is_a_defect_not_a_guess() {
        // the scanner took the wrong bytes: the read-back refuses, whatever the style
        assert_eq!(
            read_back("'alpha'", &node("alpha!"), SpanStyle::SingleQuoted),
            Err("EDIT_SPAN_MISMATCH")
        );
        assert_eq!(
            read_back("\"alpha\"", &node("x"), SpanStyle::DoubleQuoted),
            Err("EDIT_SPAN_MISMATCH")
        );
        assert_eq!(
            read_back("gamma", &node("alpha"), SpanStyle::Plain),
            Err("EDIT_SPAN_MISMATCH")
        );
        // a quoted token that is a prefix of a longer value is still a mismatch: only a plain scalar can be multi-line
        assert_eq!(
            read_back("'alpha'", &node("alpha beta"), SpanStyle::SingleQuoted),
            Err("EDIT_SPAN_MISMATCH")
        );
        // a plain scalar whose value is longer than its first line is multi-line
        assert_eq!(
            read_back("alpha", &node("alpha beta"), SpanStyle::Plain),
            Err("EDIT_MULTILINE_SCALAR")
        );
        // it is not when the value does not start with the token, or is not longer than it
        assert_eq!(
            read_back("alpha", &node("beta alpha"), SpanStyle::Plain),
            Err("EDIT_SPAN_MISMATCH")
        );
        assert_eq!(
            read_back("alpha", &node("alph"), SpanStyle::Plain),
            Err("EDIT_SPAN_MISMATCH")
        );
        // and a token that is the value reads back, also when the value is not a string
        assert_eq!(read_back("alpha", &node("alpha"), SpanStyle::Plain), Ok(()));
        assert_eq!(
            read_back("'a'", &node("a"), SpanStyle::SingleQuoted),
            Ok(())
        );
        assert_eq!(read_back("3", &node("3"), SpanStyle::Plain), Ok(()));
        assert_eq!(read_back("True", &node("true"), SpanStyle::Plain), Ok(()));
    }

    #[test]
    fn the_scanner_stops_where_the_rules_say() {
        let s = |t: &str, start: usize| scan(t, start);
        assert_eq!(s("name: alpha\n", 6), Ok((11, SpanStyle::Plain)));
        assert_eq!(s("name: alpha   # c\n", 6), Ok((11, SpanStyle::Plain)));
        assert_eq!(
            s("name: a#b\n", 6),
            Ok((9, SpanStyle::Plain)),
            "a # not after a space is part of the scalar"
        );
        assert_eq!(
            s("name: alpha", 6),
            Ok((11, SpanStyle::Plain)),
            "no final newline"
        );
        assert_eq!(s("name: 'it''s'\n", 6), Ok((13, SpanStyle::SingleQuoted)));
        assert_eq!(
            s("name: \"a\\\"b\"\n", 6),
            Ok((12, SpanStyle::DoubleQuoted))
        );
        assert_eq!(s("name: |\n", 6), Err("EDIT_BLOCK_SCALAR"));
        assert_eq!(s("name: >\n", 6), Err("EDIT_BLOCK_SCALAR"));
        assert_eq!(s("name: 'a\nb'\n", 6), Err("EDIT_MULTILINE_SCALAR"));
        assert_eq!(s("name: 'a", 6), Err("EDIT_MULTILINE_SCALAR"));
        assert_eq!(s("name: \"a\nb\"\n", 6), Err("EDIT_MULTILINE_SCALAR"));
        assert_eq!(
            s("name: \"a\\\nb\"\n", 6),
            Err("EDIT_MULTILINE_SCALAR"),
            "an escaped line break"
        );
        assert_eq!(s("name: \"a\\", 6), Err("EDIT_MULTILINE_SCALAR"));
        assert_eq!(s("name: \"a", 6), Err("EDIT_MULTILINE_SCALAR"));
        assert_eq!(
            s("a: b\r\nc: d\r\n", 3),
            Ok((4, SpanStyle::Plain)),
            "the span stops before the CR"
        );
    }
}
