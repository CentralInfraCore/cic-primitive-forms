//! Deterministic block-style YAML text for a [`Node`].
//!
//! Mapping entries keep their order; indentation is two spaces; a string is plain only
//! when reading it back yields the same string, otherwise it is double-quoted with
//! escapes. The output of [`emit`] always parses back (with [`crate::parse`]) to a node
//! with the same value. Comments, anchors, quoting style and source layout are not kept;
//! that is source-preserving editing (P3b).

use std::fmt::Write;

use crate::node::{Node, Value};

/// Writes `node` as a YAML document ending with a newline.
#[must_use]
pub fn emit(node: &Node) -> String {
    let mut out = String::new();
    match &node.value {
        Value::Seq(items) if !items.is_empty() => block_seq(items, 0, &mut out),
        Value::Map(entries) if !entries.is_empty() => block_map(node, 0, &mut out),
        _ => {
            out.push_str(&inline(node));
            out.push('\n');
        }
    }
    out
}

fn indent(out: &mut String, n: usize) {
    for _ in 0..n {
        out.push(' ');
    }
}

/// A node that is written on the rest of the current line.
fn inline(node: &Node) -> String {
    match &node.value {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Int(i) => i.to_string(),
        Value::Str(s) => string(s),
        Value::Seq(_) => "[]".to_string(),
        // (a number the model does not carry is never emitted: it is written as the empty mapping, as the Go peer does)
        Value::Map(_) | Value::Unsupported(_) => "{}".to_string(),
    }
}

fn is_block(node: &Node) -> bool {
    match &node.value {
        Value::Seq(items) => !items.is_empty(),
        Value::Map(entries) => !entries.is_empty(),
        _ => false,
    }
}

fn block_seq(items: &[Node], level: usize, out: &mut String) {
    for item in items {
        indent(out, level);
        out.push('-');
        item_tail(item, level, out);
    }
}

fn block_map(node: &Node, level: usize, out: &mut String) {
    let entries = match &node.value {
        Value::Map(entries) => entries,
        _ => return,
    };
    for e in entries {
        let key = string(&e.key);
        indent(out, level);
        if key.chars().count() > IMPLICIT_KEY_MAX {
            // YAML limits an implicit key (`key: value`) to 1024 characters: a longer one is
            // written in the explicit form, which has no limit.
            out.push_str("? ");
            out.push_str(&key);
            out.push('\n');
            indent(out, level);
        } else {
            out.push_str(&key);
        }
        out.push(':');
        item_tail(&e.value, level, out);
    }
}

/// What follows `-` or `key:` for a value at `level`.
fn item_tail(value: &Node, level: usize, out: &mut String) {
    if is_block(value) {
        out.push('\n');
        match &value.value {
            Value::Seq(items) => block_seq(items, level + 2, out),
            _ => block_map(value, level + 2, out),
        }
    } else {
        out.push(' ');
        out.push_str(&inline(value));
        out.push('\n');
    }
}

/// A YAML string scalar that reads back as the same string.
fn string(s: &str) -> String {
    if plain_safe(s) {
        s.to_string()
    } else {
        quoted(s)
    }
}

/// A key written longer than this many characters uses the explicit `? key` form. The YAML limit
/// is 1024; staying well under it keeps the choice independent of how a reader counts quotes and
/// escapes.
const IMPLICIT_KEY_MAX: usize = 512;

/// Strings that are written quoted although they read back fine as plain text: the spellings
/// YAML 1.1 reads as booleans (so no reader takes them for one) and `<<`, which as a mapping key
/// is the merge key and is refused by `parse`.
const ALWAYS_QUOTED: [&str; 17] = [
    "y", "Y", "yes", "Yes", "YES", "n", "N", "no", "No", "NO", "on", "On", "ON", "off", "Off",
    "OFF", "<<",
];

/// Whether `s` can be written without quotes. It is deliberately defined by the round trip: `s`
/// is plain when reading it back as a document gives the same string. That one test covers a
/// leading indicator, `: `, ` #`, surrounding spaces, anything that reads as another type or as a
/// collection, and an unsupported spelling, so none of those is listed again here. What the
/// round trip cannot see is excluded first: control characters (a tab or a newline reads back
/// fine alone but not in every context) and the strings of `ALWAYS_QUOTED`.
fn plain_safe(s: &str) -> bool {
    if ALWAYS_QUOTED.contains(&s) || s.chars().any(char::is_control) {
        return false;
    }
    match crate::parse::parse(s) {
        Ok(Node {
            value: Value::Str(r),
            ..
        }) => r == s,
        _ => false,
    }
}

fn is_line_or_bom(c: char) -> bool {
    ['\u{2028}', '\u{2029}', '\u{FEFF}'].contains(&c)
}

fn quoted(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c.is_control() || is_line_or_bom(c) => {
                let _ = write!(out, "\\u{:04X}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
