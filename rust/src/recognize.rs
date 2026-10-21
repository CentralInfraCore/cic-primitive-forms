//! Recognition (ADR 0001) and position validation (ADR 0003, ADR 0007).
//!
//! A node at a primitive position is **Short** unless it is a mapping whose key set is
//! exactly the expanded set `A` (**Expanded**); a mapping holding all of `A` plus another
//! key is the one recognition error. `A` is passed in, never hardcoded. Recognition looks
//! at keys only, never at values.
//!
//! A response is phase-exclusive: if recognition finds any error only those are returned,
//! otherwise the validation errors of the Short nodes. Within a phase the errors are
//! ordered by logical path, then code (ADR 0007), and each carries its canonical `logical_path`
//! where one is determinable.
//!
//! Subset: the positions of the synthetic schema model (`scalar`, `opaque`, a structured
//! record of named fields, a list of such records). A shape this slice has no rule for (a
//! scalar where a mapping or a list is declared) is reported as [`Error::Unsupported`], not
//! accepted.

use std::collections::BTreeMap;

use crate::error::{Error, Location, Phase, Violation};
use crate::node::{Node, Value};

/// A primitive position of the synthetic schema model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Position {
    /// A scalar value.
    Scalar,
    /// Any value; the content of an expanded node's `value` is not re-recognized.
    Opaque,
    /// A mapping with these declared fields (the fields are not recognized further).
    Structured(Vec<String>),
    /// A list of records; each record field is a position of its own.
    ListOf(Vec<(String, Position)>),
}

/// The recognition class of a node.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    Short,
    Expanded,
    /// Recognition failed (`EXPANDED_WITH_EXTRA_KEY`).
    Error,
}

/// The result of recognizing and validating one document at one position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Outcome {
    /// The class of the node at the top position.
    pub class: Class,
    /// For a list position: the class of each field of each element, in order.
    pub elements: Option<Vec<BTreeMap<String, Class>>>,
    /// An expanded node at an opaque position: its `value` content stays opaque.
    pub opaque_value: bool,
    /// The machine response; empty means the document is valid at the position.
    pub violations: Vec<Violation>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) enum Seg {
    Field(String),
    Index(usize),
    /// A ListItem segment (ADR 0006): the key components of one list element, in the declared key order.
    /// It follows the field segment that names the list. The derived order is ADR 0007's: by component
    /// name, strings by UTF-8 bytes before integers, integers numerically.
    Item(Vec<crate::listitem::KeyComp>),
}

pub(crate) type Path = Vec<Seg>;

pub(crate) fn location(pos: Option<crate::node::Pos>) -> Location {
    pos.map_or(Location::Unavailable, Location::At)
}

fn keys_of(node: &Node) -> Option<Vec<&str>> {
    match &node.value {
        Value::Map(entries) => Some(entries.iter().map(|e| e.key.as_str()).collect()),
        _ => None,
    }
}

/// ADR 0001 for one node.
pub(crate) fn classify(node: &Node, a: &[String]) -> Result<Class, Violation> {
    let keys = match keys_of(node) {
        Some(keys) => keys,
        None => return Ok(Class::Short),
    };
    let has_all = a.iter().all(|k| keys.contains(&k.as_str()));
    if !has_all {
        Ok(Class::Short)
    } else if keys.len() == a.len() {
        Ok(Class::Expanded)
    } else {
        Err(Violation {
            code: "EXPANDED_WITH_EXTRA_KEY",
            phase: Phase::Recognize,
            location: location(node.pos),
            logical_path: None,
        })
    }
}

/// Validation of a Short node at a position.
fn validate_short(
    node: &Node,
    position: &Position,
    path: &Path,
    out: &mut Vec<(Path, Violation)>,
) -> Result<(), Error> {
    match position {
        Position::Opaque => {}
        Position::Scalar => {
            if let Value::Map(_) = node.value {
                out.push((
                    path.clone(),
                    Violation {
                        code: "MAP_AT_SCALAR_POSITION",
                        phase: Phase::Validate,
                        location: location(node.pos),
                        logical_path: None,
                    },
                ));
            }
        }
        Position::Structured(fields) => {
            let entries = match &node.value {
                Value::Map(entries) => entries,
                _ => {
                    return Err(Error::Unsupported(
                        "a non-mapping at a structured position".into(),
                    ))
                }
            };
            for e in entries.iter().filter(|e| !fields.contains(&e.key)) {
                let mut p = path.clone();
                p.push(Seg::Field(e.key.clone()));
                out.push((
                    p,
                    Violation {
                        code: "UNKNOWN_FIELD",
                        phase: Phase::Validate,
                        location: location(e.key_pos),
                        logical_path: None,
                    },
                ));
            }
        }
        Position::ListOf(_) => {
            return Err(Error::Unsupported("a list position inside a record".into()));
        }
    }
    Ok(())
}

/// The canonical ADR 0006 text of a path made of field names; `None` where ADR 0007 leaves the
/// path out. That is the root alone (a single position with no structure around it), a list
/// element (the synthetic schema declares no identity, so the item cannot be named) and a
/// field name that cannot be written (empty, or holding a control character).
pub(crate) fn logical_path(path: &Path) -> Option<String> {
    if path.is_empty() {
        return None;
    }
    let mut out = String::from("$");
    for seg in path {
        match seg {
            Seg::Field(name) => {
                out.push('.');
                if !write_name(&mut out, name) {
                    return None;
                }
            }
            Seg::Item(item) => {
                if !write_selector(&mut out, item) {
                    return None;
                }
            }
            Seg::Index(_) => return None,
        }
    }
    Some(out)
}

/// A field or component name with the ADR 0006 escape set; false for a name that cannot be written.
fn write_name(out: &mut String, name: &str) -> bool {
    if name.is_empty() || name.chars().any(|c| c <= '\u{1f}' || c == '\u{7f}') {
        return false;
    }
    for c in name.chars() {
        if "\\.[]{}=,".contains(c) {
            out.push('\\');
        }
        out.push(c);
    }
    true
}

/// `[{name="value",n=1}]`: strings as canonical double-quoted JSON, integers bare.
fn write_selector(out: &mut String, item: &[crate::listitem::KeyComp]) -> bool {
    use crate::listitem::KeyValue;
    if item.is_empty() {
        return false;
    }
    out.push_str("[{");
    for (i, c) in item.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        if !write_name(out, &c.name) {
            return false;
        }
        out.push('=');
        match &c.value {
            KeyValue::Int(n) => out.push_str(&n.to_string()),
            KeyValue::Str(s) => write_json_string(out, s),
        }
    }
    out.push_str("}]");
    true
}

/// The one canonical writing of ADR 0006: `\"` and `\\`, the short escapes `\b \f \n \r \t`, `\u00xx`
/// (lower-case hex) for the other control characters, everything else literal UTF-8.
fn write_json_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Orders by logical path, then code (ADR 0007) and fills in each `logical_path`.
///
/// `(logical_path, code)` is unique in a response. Every violation that has a path is
/// distinct by construction (a field occurs once); the ones without a path (list elements,
/// unwritable names) cannot be told apart in the response, so only the first of each code, in
/// path order, is kept.
pub(crate) fn sorted(mut found: Vec<(Path, Violation)>) -> Vec<Violation> {
    found.sort_by(|(pa, va), (pb, vb)| pa.cmp(pb).then_with(|| va.code.cmp(vb.code)));
    let mut out: Vec<Violation> = Vec::new();
    for (path, mut v) in found {
        v.logical_path = logical_path(&path);
        if v.logical_path.is_none()
            && out
                .iter()
                .any(|o| o.logical_path.is_none() && o.code == v.code)
        {
            continue;
        }
        out.push(v);
    }
    out
}

/// Recognizes `node` at `position` and validates what recognition leaves Short.
///
/// # Errors
/// [`Error::Unsupported`] for a shape outside the subset described in the module docs.
pub fn recognize(node: &Node, position: &Position, a: &[String]) -> Result<Outcome, Error> {
    match position {
        Position::ListOf(record) => recognize_list(node, record, a),
        Position::Scalar | Position::Opaque | Position::Structured(_) => match classify(node, a) {
            Err(v) => Ok(Outcome {
                class: Class::Error,
                elements: None,
                opaque_value: false,
                violations: vec![v],
            }),
            Ok(Class::Expanded) => Ok(Outcome {
                class: Class::Expanded,
                elements: None,
                opaque_value: *position == Position::Opaque,
                violations: Vec::new(),
            }),
            Ok(class) => {
                let mut found = Vec::new();
                validate_short(node, position, &Vec::new(), &mut found)?;
                Ok(Outcome {
                    class,
                    elements: None,
                    opaque_value: false,
                    violations: sorted(found),
                })
            }
        },
    }
}

fn recognize_list(
    node: &Node,
    record: &[(String, Position)],
    a: &[String],
) -> Result<Outcome, Error> {
    let items = match &node.value {
        Value::Seq(items) => items,
        _ => return Err(Error::Unsupported("a non-list at a list position".into())),
    };
    let mut elements = Vec::with_capacity(items.len());
    let mut recognition: Vec<(Path, Violation)> = Vec::new();
    let mut validation: Vec<(Path, Violation)> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let entries = match &item.value {
            Value::Map(entries) => entries,
            _ => return Err(Error::Unsupported("a non-record list element".into())),
        };
        let mut classes = BTreeMap::new();
        for e in entries {
            let mut path = vec![Seg::Index(i), Seg::Field(e.key.clone())];
            let field_position = match record.iter().find(|(name, _)| *name == e.key) {
                Some((_, position)) => position,
                None => {
                    validation.push((
                        path,
                        Violation {
                            code: "UNKNOWN_FIELD",
                            phase: Phase::Validate,
                            location: location(e.key_pos),
                            logical_path: None,
                        },
                    ));
                    continue;
                }
            };
            match classify(&e.value, a) {
                Err(v) => {
                    recognition.push((path, v));
                    classes.insert(e.key.clone(), Class::Error);
                }
                Ok(class) => {
                    if class == Class::Short {
                        validate_short(&e.value, field_position, &path, &mut validation)?;
                    }
                    path.clear();
                    classes.insert(e.key.clone(), class);
                }
            }
        }
        elements.push(classes);
    }
    let (class, violations) = if recognition.is_empty() {
        (Class::Short, sorted(validation))
    } else {
        (Class::Error, sorted(recognition))
    };
    Ok(Outcome {
        class,
        elements: Some(elements),
        opaque_value: false,
        violations,
    })
}
