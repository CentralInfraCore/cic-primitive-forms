//! The identity of a list element (ADR 0002 "List identity", ADR 0006 "Building a path from a document").
//! The Rust peer of `go/listitem.go`.
//!
//! The identity comes only from the pinned schema: the declared key (`role: key` / `item_key`, or the key
//! of the `item_choice` case), never from the names of the fields and never from the position in the list.
//! A case of an `item_choice` is selected by field presence on the raw authored record: it matches when
//! every key of the record is a declared field of the case and every mandatory field of the case, key
//! fields included, is present; zero or several matches is an error. An Access wrapper around a key value
//! is looked through, so the short and the expanded form give the same identity.

use crate::error::{Error, Location, Phase, Violation};
use crate::node::{Node, Value};
use crate::recognize::{classify, logical_path, Class, Path, Seg};
use crate::validate::{Presence, ScalarType, Shape, ShapeType};

pub const KEY_MISSING: &str = "PATH_KEY_MISSING";
pub const KEY_TYPE: &str = "PATH_KEY_TYPE";
pub const KEY_UNSUPPORTED: &str = "PATH_KEY_UNSUPPORTED";
pub const CASE_NONE: &str = "PATH_CASE_NONE";
pub const CASE_AMBIGUOUS: &str = "PATH_CASE_AMBIGUOUS";
pub const DUPLICATE_IDENTITY: &str = "PATH_DUPLICATE_IDENTITY";

/// The value of one key component: a string or an integer (ADR 0006: no other key type has an encoding).
/// The derived order puts strings before integers, as ADR 0007 compares them.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum KeyValue {
    Str(String),
    Int(i64),
    /// A value that has no encoding (ADR 0006), only for `format_path`, which is given components from outside; an identity
    /// built from a document never holds one.
    Boolean(bool),
    /// An integer beyond signed 64 bits, only for `format_path`.
    OutOfRange,
}

/// One component of an element identity: the name of the key field and its typed value.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct KeyComp {
    pub name: String,
    pub value: KeyValue,
}

/// One element structure of a list: the only one of a plain list (`item_fields`), one of the alternatives
/// of an `item_choice`. `key` is the declared key, in order.
#[derive(Debug, Clone, PartialEq)]
pub struct Case {
    pub name: String,
    pub fields: Vec<(String, Shape)>,
    pub key: Vec<String>,
}

/// The elements of a list. `choice` says the case is selected by the presence of the declared fields
/// (`item_choice`); a plain list has exactly one case and no selection.
#[derive(Debug, Clone, PartialEq)]
pub struct Elements {
    pub choice: bool,
    pub cases: Vec<Case>,
}

/// One step of an address in a document: a field name or a list index. The index only selects the element;
/// the path that comes out names it by its identity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    Field(String),
    Index(usize),
}

fn entry<'a>(n: &'a Node, key: &str) -> Option<&'a Node> {
    match &n.value {
        Value::Map(entries) => entries.iter().find(|e| e.key == key).map(|e| &e.value),
        _ => None,
    }
}

/// Looks through an expanded Access wrapper: the node whose value is meant. `None` for a wrapper that has
/// no `value` entry (not an identity).
fn through<'a>(n: &'a Node, a: &[String]) -> Option<&'a Node> {
    match classify(n, a) {
        Ok(Class::Expanded) => entry(n, "value"),
        _ => Some(n),
    }
}

fn field_of<'a>(c: &'a Case, name: &str) -> Option<&'a Shape> {
    c.fields.iter().find(|(n, _)| n == name).map(|(_, s)| s)
}

/// The ADR 0002 case rule for a record.
fn matches(c: &Case, rec: &Node) -> bool {
    let declared = match &rec.value {
        Value::Map(entries) => entries.iter().all(|e| field_of(c, &e.key).is_some()),
        _ => false,
    };
    declared
        && c.fields.iter().all(|(name, shape)| {
            let required = shape.presence == Presence::Mandatory || c.key.contains(name);
            !required || entry(rec, name).is_some()
        })
}

/// Why an element has no identity: the code, and the node a source position is taken from (ADR 0007): the
/// key value for a key of the wrong or an unsupported type, the record for every other error.
pub(crate) type Refusal<'n> = (&'static str, &'n Node);

/// Selects the case of a record and builds its identity.
pub(crate) fn identify<'e, 'n>(
    rec: &'n Node,
    els: &'e Elements,
    a: &[String],
) -> Result<(&'e Case, Vec<KeyComp>), Refusal<'n>> {
    let case = if els.choice {
        let mut found: Option<&Case> = None;
        for c in &els.cases {
            if matches(c, rec) {
                if found.is_some() {
                    return Err((CASE_AMBIGUOUS, rec));
                }
                found = Some(c);
            }
        }
        found.ok_or((CASE_NONE, rec))?
    } else {
        &els.cases[0]
    };
    let mut id = Vec::with_capacity(case.key.len());
    for name in &case.key {
        let raw = entry(rec, name).ok_or((KEY_MISSING, rec))?;
        let scalar = match field_of(case, name).map(|s| &s.shape_type) {
            Some(ShapeType::Scalar(t)) if *t != ScalarType::Boolean => *t,
            _ => return Err((KEY_UNSUPPORTED, raw)),
        };
        let v = through(raw, a).ok_or((KEY_TYPE, raw))?;
        let value = match (&v.value, scalar) {
            (Value::Str(s), ScalarType::String) => KeyValue::Str(s.clone()),
            (Value::Int(n), ScalarType::Integer) => KeyValue::Int(*n),
            _ => return Err((KEY_TYPE, raw)),
        };
        id.push(KeyComp {
            name: name.clone(),
            value,
        });
    }
    Ok((case, id))
}

fn fail(code: &'static str, path: &Path) -> Error {
    Error::Contract(vec![Violation {
        code,
        phase: Phase::PathOf,
        location: Location::Unavailable,
        logical_path: logical_path(path),
    }])
}

fn nothing(step: usize, why: &str) -> Error {
    Error::Unsupported(format!("address step {step}: {why}"))
}

/// The canonical logical path (ADR 0006) of the node that `at` addresses in `doc`, read against `shape` (a
/// composite). Access wrappers are looked through on the way, so the short and the expanded form give one
/// path; a list element is named by its identity, not by its index.
///
/// # Errors
/// A failure of the identity is [`Error::Contract`] of phase `path_of` whose logical path is the list's (or
/// the identity's, for a duplicate); an address that does not lead to a node of the document and the schema
/// is [`Error::Unsupported`].
pub fn path_of(doc: &Node, shape: &Shape, a: &[String], at: &[Step]) -> Result<String, Error> {
    let no_fields: &[(String, Shape)] = &[];
    let mut fields = match &shape.shape_type {
        ShapeType::Composite(f) => f.as_slice(),
        _ => no_fields,
    };
    let mut node = doc;
    let mut path: Path = Vec::new();
    let mut i = 0;
    while i < at.len() {
        let name = match &at[i] {
            Step::Field(name) => name,
            Step::Index(_) => {
                return Err(nothing(i, "a list index needs the list field before it"))
            }
        };
        let found = (entry(node, name), fields.iter().find(|(n, _)| n == name));
        let (next, sub) = match found {
            (Some(next), Some((_, sub))) => (next, sub),
            _ => return Err(nothing(i, "not a field of the document and the schema")),
        };
        path.push(Seg::Field(name.clone()));
        node = through(next, a).unwrap_or(next);
        fields = match &sub.shape_type {
            ShapeType::Composite(f) => f.as_slice(),
            _ => no_fields,
        };
        i += 1;
        let (elements, idx) = match (&sub.shape_type, at.get(i)) {
            (ShapeType::DeclaredList { elements, .. }, Some(Step::Index(idx))) => (elements, *idx),
            _ => continue,
        };
        let items = match &node.value {
            Value::Seq(items) => items,
            _ => return Err(nothing(i, "no such list element")),
        };
        let rec = items
            .get(idx)
            .ok_or_else(|| nothing(i, "no such list element"))?;
        if !matches!(rec.value, Value::Map(_)) {
            return Err(nothing(i, "a list element is not a record"));
        }
        let (case, id) = identify(rec, elements, a).map_err(|(code, _)| fail(code, &path))?;
        let item = Seg::Item(id);
        for (j, other) in items.iter().enumerate() {
            let twin = identify(other, elements, a)
                .ok()
                .map(|(_, oid)| Seg::Item(oid));
            if j != idx && twin.as_ref() == Some(&item) {
                let mut p = path.clone();
                p.push(item);
                return Err(fail(DUPLICATE_IDENTITY, &p));
            }
        }
        path.push(item);
        node = rec;
        fields = case.fields.as_slice();
        i += 1;
    }
    Ok(logical_path(&path).unwrap_or_else(|| "$".to_string()))
}
