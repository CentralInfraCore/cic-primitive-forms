//! YAML text to a [`Node`] tree with source positions.
//!
//! The supported subset is deliberately narrow and everything outside it is an explicit
//! [`Error::Unsupported`], never a silent reinterpretation: one document; no anchors,
//! aliases, tags or merge keys; mapping keys are scalars (a key is the scalar's text);
//! plain scalars resolve as `null`/`~`/empty, `true`/`false` (any of the three YAML 1.2
//! spellings of case), and decimal integers that fit `i64`; quoted and block scalars are
//! strings. A plain scalar that looks like a float, a hex/octal number, an integer with a
//! leading zero or an integer beyond `i64` is unsupported rather than guessed. The characters
//! U+0085, U+2028 and U+2029 are line breaks to some YAML readers and not to others, so a text
//! holding one is refused rather than read two ways.
//!
//! A duplicate mapping key is the one `parse`-phase error of the contract
//! (`DUPLICATE_KEY`, anchored at the second occurrence); only the first, in source
//! order, is reported.

use saphyr_parser::{Event, Parser, ScalarStyle};

use crate::error::{Error, Location, Phase, Violation};
use crate::node::{Entry, Node, Pos, Value};

/// Resource limits. They are an implementation boundary, not a contract value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub max_bytes: usize,
    pub max_depth: usize,
    pub max_nodes: usize,
}

impl Default for Limits {
    fn default() -> Limits {
        Limits {
            max_bytes: 1 << 20,
            max_depth: 64,
            max_nodes: 100_000,
        }
    }
}

/// Parses with the default [`Limits`].
///
/// # Errors
/// See [`parse_with`].
pub fn parse(text: &str) -> Result<Node, Error> {
    parse_with(text, Limits::default())
}

/// Parses one YAML document.
///
/// # Errors
/// [`Error::Contract`] for a duplicate key, [`Error::Unsupported`] outside the subset,
/// [`Error::Syntax`] when the YAML library rejects the text, [`Error::Limit`] when a limit
/// is exceeded.
pub fn parse_with(text: &str, limits: Limits) -> Result<Node, Error> {
    if text.len() > limits.max_bytes {
        return Err(Error::Limit(format!(
            "input is {} bytes, the limit is {}",
            text.len(),
            limits.max_bytes
        )));
    }
    let bom_len = if text.starts_with('\u{FEFF}') { 3 } else { 0 };
    let body = &text[bom_len..];
    if let Some(c) = body
        .chars()
        .find(|c| ['\u{85}', '\u{2028}', '\u{2029}'].contains(c))
    {
        return Err(Error::Unsupported(format!(
            "line separator U+{:04X} (a line break to some YAML readers only)",
            c as u32
        )));
    }
    let table = Table::new(body, bom_len);

    let mut stack: Vec<Frame> = Vec::new();
    let mut root: Option<Node> = None;
    let mut docs = 0;
    let mut nodes = 0;
    for item in Parser::new_from_str(body) {
        let (event, span) = item.map_err(|e| Error::Syntax(e.info().to_string()))?;
        let at = table.pos(span.start.index());
        match event {
            Event::DocumentStart(_) => {
                docs += 1;
                if docs > 1 {
                    return Err(Error::Unsupported("more than one document".into()));
                }
            }
            Event::Alias(_) => return Err(Error::Unsupported("alias".into())),
            Event::Scalar(value, style, anchor, tag) => {
                if anchor != 0 {
                    return Err(Error::Unsupported("anchor".into()));
                }
                if tag.is_some() {
                    return Err(Error::Unsupported("tag".into()));
                }
                let plain = style == ScalarStyle::Plain;
                if let Some(Frame::Map { entries, key, .. }) = stack.last_mut() {
                    if key.is_none() {
                        if plain && value == "<<" {
                            return Err(Error::Unsupported("merge key".into()));
                        }
                        if entries.iter().any(|e| e.key == value.as_ref()) {
                            return Err(Error::Contract(vec![Violation {
                                code: "DUPLICATE_KEY",
                                phase: Phase::Parse,
                                location: Location::At(at),
                            }]));
                        }
                        *key = Some((value.into_owned(), at));
                        continue;
                    }
                }
                nodes += 1;
                check_nodes(nodes, limits)?;
                let v = if plain {
                    resolve_plain(&value)?
                } else {
                    Value::Str(value.into_owned())
                };
                place(
                    &mut stack,
                    &mut root,
                    Node {
                        value: v,
                        pos: Some(at),
                    },
                );
            }
            Event::SequenceStart(anchor, tag) => {
                open(
                    &mut stack,
                    &mut nodes,
                    limits,
                    (at, anchor, tag.is_some()),
                    false,
                )?;
            }
            Event::MappingStart(anchor, tag) => {
                open(
                    &mut stack,
                    &mut nodes,
                    limits,
                    (at, anchor, tag.is_some()),
                    true,
                )?;
            }
            Event::SequenceEnd | Event::MappingEnd => {
                let done = match stack.pop() {
                    Some(Frame::Seq { items, pos }) => Node {
                        value: Value::Seq(items),
                        pos: Some(pos),
                    },
                    Some(Frame::Map { entries, pos, .. }) => Node {
                        value: Value::Map(entries),
                        pos: Some(pos),
                    },
                    None => continue,
                };
                place(&mut stack, &mut root, done);
            }
            Event::Nothing | Event::StreamStart | Event::StreamEnd | Event::DocumentEnd => {}
        }
    }
    Ok(root.unwrap_or_else(|| Node::new(Value::Null)))
}

/// Starts a sequence or a mapping.
fn open(
    stack: &mut Vec<Frame>,
    nodes: &mut usize,
    limits: Limits,
    (at, anchor, tagged): (Pos, usize, bool),
    is_map: bool,
) -> Result<(), Error> {
    if anchor != 0 {
        return Err(Error::Unsupported("anchor".into()));
    }
    if tagged {
        return Err(Error::Unsupported("tag".into()));
    }
    if let Some(Frame::Map { key: None, .. }) = stack.last() {
        return Err(Error::Unsupported("non-scalar mapping key".into()));
    }
    *nodes += 1;
    check_nodes(*nodes, limits)?;
    if stack.len() >= limits.max_depth {
        return Err(Error::Limit(format!(
            "nesting is deeper than {}",
            limits.max_depth
        )));
    }
    stack.push(if is_map {
        Frame::Map {
            entries: Vec::new(),
            key: None,
            pos: at,
        }
    } else {
        Frame::Seq {
            items: Vec::new(),
            pos: at,
        }
    });
    Ok(())
}

fn check_nodes(count: usize, limits: Limits) -> Result<(), Error> {
    if count > limits.max_nodes {
        Err(Error::Limit(format!(
            "more than {} nodes",
            limits.max_nodes
        )))
    } else {
        Ok(())
    }
}

enum Frame {
    Seq {
        items: Vec<Node>,
        pos: Pos,
    },
    Map {
        entries: Vec<Entry>,
        key: Option<(String, Pos)>,
        pos: Pos,
    },
}

/// Hands a finished node to its parent, or makes it the root.
fn place(stack: &mut [Frame], root: &mut Option<Node>, node: Node) {
    match stack.last_mut() {
        Some(Frame::Seq { items, .. }) => items.push(node),
        Some(Frame::Map { entries, key, .. }) => {
            let (k, key_pos) = key
                .take()
                .expect("a value always follows a key in a mapping");
            entries.push(Entry {
                key: k,
                key_pos: Some(key_pos),
                value: node,
            });
        }
        None => *root = Some(node),
    }
}

/// Line starts and char boundaries of the BOM-free body, to turn the parser's char index
/// into the ADR 0007 coordinates.
struct Table<'a> {
    body: &'a str,
    bom_len: usize,
    char_to_byte: Vec<usize>,
    line_starts: Vec<usize>,
}

impl<'a> Table<'a> {
    fn new(body: &'a str, bom_len: usize) -> Table<'a> {
        let mut char_to_byte: Vec<usize> = body.char_indices().map(|(i, _)| i).collect();
        char_to_byte.push(body.len());
        let bytes = body.as_bytes();
        let mut line_starts = vec![0];
        let mut i = 0;
        while i < bytes.len() {
            match bytes[i] {
                b'\n' => line_starts.push(i + 1),
                b'\r' => {
                    if bytes.get(i + 1) == Some(&b'\n') {
                        i += 1;
                    }
                    line_starts.push(i + 1);
                }
                _ => {}
            }
            i += 1;
        }
        Table {
            body,
            bom_len,
            char_to_byte,
            line_starts,
        }
    }

    fn pos(&self, char_index: usize) -> Pos {
        let last = self.char_to_byte.len() - 1;
        let byte = self.char_to_byte[char_index.min(last)];
        let line = self.line_starts.partition_point(|&s| s <= byte);
        let start = self.line_starts[line - 1];
        Pos {
            line,
            column: self.body[start..byte].chars().count() + 1,
            offset: self.bom_len + byte,
        }
    }
}

fn resolve_plain(s: &str) -> Result<Value, Error> {
    match s {
        "" | "~" | "null" | "Null" | "NULL" => return Ok(Value::Null),
        "true" | "True" | "TRUE" => return Ok(Value::Bool(true)),
        "false" | "False" | "FALSE" => return Ok(Value::Bool(false)),
        _ => {}
    }
    if let Some(digits) = decimal_digits(s) {
        if digits.len() > 1 && digits.starts_with('0') {
            return Err(Error::Unsupported(format!(
                "integer with a leading zero: {s}"
            )));
        }
        return s
            .strip_prefix('+')
            .unwrap_or(s)
            .parse::<i64>()
            .map(Value::Int)
            .map_err(|_| Error::Unsupported(format!("integer beyond i64: {s}")));
    }
    if is_float_like(s) || is_radix_number(s) {
        return Err(Error::Unsupported(format!("number spelling: {s}")));
    }
    Ok(Value::Str(s.to_string()))
}

/// The digits of `[+-]?[0-9]+`, if `s` is exactly that.
fn decimal_digits(s: &str) -> Option<&str> {
    let digits = s.strip_prefix(['+', '-']).unwrap_or(s);
    if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
        Some(digits)
    } else {
        None
    }
}

fn is_radix_number(s: &str) -> bool {
    ["0x", "0o"].iter().any(|p| {
        s.strip_prefix(p)
            .is_some_and(|rest| !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_hexdigit()))
    })
}

fn is_float_like(s: &str) -> bool {
    let t = s.strip_prefix(['+', '-']).unwrap_or(s);
    if [".inf", ".Inf", ".INF"].contains(&t) || [".nan", ".NaN", ".NAN"].contains(&s) {
        return true;
    }
    let (mantissa, exp) = match t.find(['e', 'E']) {
        Some(i) => (&t[..i], Some(&t[i + 1..])),
        None => (t, None),
    };
    if let Some(e) = exp {
        let e = e.strip_prefix(['+', '-']).unwrap_or(e);
        if e.is_empty() || !e.bytes().all(|b| b.is_ascii_digit()) {
            return false;
        }
    }
    let (int, frac) = match mantissa.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (mantissa, None),
    };
    let all_digits = |x: &str| x.bytes().all(|b| b.is_ascii_digit());
    if !all_digits(int) || !frac.is_none_or(all_digits) {
        return false;
    }
    match frac {
        Some(f) => !int.is_empty() || !f.is_empty(),
        None => !int.is_empty() && exp.is_some(),
    }
}
