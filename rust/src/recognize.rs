//! Recognition (ADR 0001) and position validation (ADR 0003, ADR 0007).
//!
//! A node at a primitive position is **Short** unless it is a mapping whose key set is
//! exactly the expanded set `A` (**Expanded**); a mapping holding all of `A` plus another
//! key is the one recognition error. `A` is passed in, never hardcoded. Recognition looks
//! at keys only, never at values.
//!
//! A response is phase-exclusive: if recognition finds any error only those are returned,
//! otherwise the validation errors of the Short nodes. Within a phase the errors are
//! ordered by logical path, then code (ADR 0007). The path is used for that order only; it
//! is not part of the response here, which the vectors of this slice do not carry.
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
enum Seg {
    Field(String),
    Index(usize),
}

type Path = Vec<Seg>;

fn location(pos: Option<crate::node::Pos>) -> Location {
    pos.map_or(Location::Unavailable, Location::At)
}

fn keys_of(node: &Node) -> Option<Vec<&str>> {
    match &node.value {
        Value::Map(entries) => Some(entries.iter().map(|e| e.key.as_str()).collect()),
        _ => None,
    }
}

/// ADR 0001 for one node.
fn classify(node: &Node, a: &[String]) -> Result<Class, Violation> {
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

/// Orders by logical path, then code (ADR 0007), and keeps one violation per code.
///
/// The response of this slice carries no `logical_path` (the vectors name a position
/// only), and `(logical_path, code)` is unique within a response; with the path absent that
/// leaves one violation per code, the one with the smallest path. Reporting every unknown
/// field needs the path in the response, which no vector of this slice asserts.
fn sorted(mut found: Vec<(Path, Violation)>) -> Vec<Violation> {
    found.sort_by(|(pa, va), (pb, vb)| pa.cmp(pb).then_with(|| va.code.cmp(vb.code)));
    let mut out: Vec<Violation> = Vec::new();
    for (_, v) in found {
        if !out.iter().any(|o| o.code == v.code) {
            out.push(v);
        }
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
