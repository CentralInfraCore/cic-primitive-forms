//! Applying the schema defaults to an authored document (P3, second unit; ADR 0003 rules 3 and 4, ADR 0011). The
//! Rust peer of `go/defaults.go`; the shared cases are `testdata/p1/defaults.json`.
//!
//! A default applies to an **absent** field and to nothing else: never to an explicit `null`, `""`, `0`, `false`,
//! `[]` or `{}`. Whether a node may take one was decided when the schema was compiled (ADR 0011: authority `config`,
//! optional, no `key`, `reference`, `derived` or `volatile`; anything else is `SCHEMA_INVALID`), so a [`Shape`] that
//! carries a default takes it.
//!
//! Decisions of this unit, written down because the documents do not settle them:
//!
//! - The walk goes through the fields that are present: a present composite, and the records of a declared list
//!   (named by their declared identity). It never goes into an absent composite or an absent list: defaults do not
//!   invent structure, and the defaults of its fields are not applied.
//! - The default of a field is inserted as a copy, in the short form (a bare value), after the entries of its
//!   mapping, in schema order. A default is a whole value: nothing below it is defaulted again, and it is origin
//!   `default` as a whole. Composing with `expand` (the symbolic policy of the position) is the next unit.
//! - The input is validated first. A document with violations, or that this slice does not support, is not
//!   defaulted: the violations are returned and no document is produced. The input node is never modified.
//! - [`ValueOrigin`] is relative to the input. Applying the defaults to the result again changes nothing
//!   (idempotence), but it reports the inserted values as authored, because from then on they are written in the
//!   document.

use crate::error::{Error, Violation};
use crate::listitem::identify;
use crate::node::{Entry, Node, Value};
use crate::recognize::{logical_path, Path, Seg};
use crate::validate::{validate, Shape, ShapeType};

/// The two origins of a value (P3 design, section 2). Never "inherited": inheritance concerns policy rules.
pub const VALUE_AUTHORED: &str = "authored";
pub const VALUE_DEFAULT: &str = "default";

/// The origin of one declared position that the output document holds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValueOrigin {
    /// The canonical logical path (ADR 0006); list elements are named by their identity.
    pub path: String,
    /// [`VALUE_AUTHORED`] or [`VALUE_DEFAULT`].
    pub origin: &'static str,
}

/// The result of [`apply_defaults`].
#[derive(Debug, Clone)]
pub struct Defaulted {
    /// The machine response of the validation that ran first. When it is not empty, `document` is `None`.
    pub violations: Vec<Violation>,
    /// A copy of the input with the defaults of the absent fields inserted.
    pub document: Option<Node>,
    /// Every declared position of `document`, in path order: authored, or default for an inserted value.
    pub origins: Vec<ValueOrigin>,
}

/// Validates the authored document against `root` and inserts the declared default of every absent optional field
/// that has one. `a` is the key set A of the Access atom, as for [`validate`].
///
/// # Errors
/// Those of [`validate`]: an unsupported input is an [`Error::Unsupported`], never a guess.
pub fn apply_defaults(doc: Option<&Node>, root: &Shape, a: &[String]) -> Result<Defaulted, Error> {
    let v = validate(doc, root, a)?;
    let doc = match (doc, v.violations.is_empty()) {
        (Some(doc), true) => doc,
        _ => {
            return Ok(Defaulted {
                violations: v.violations,
                document: None,
                origins: Vec::new(),
            })
        }
    };
    let mut out = doc.clone();
    let mut found: Vec<(Path, &'static str)> = Vec::new();
    let mut unwritable = false;
    if let ShapeType::Composite(fields) = &root.shape_type {
        composite(
            &mut out,
            fields,
            a,
            &mut Vec::new(),
            &mut found,
            &mut unwritable,
        );
    }
    if unwritable {
        return Err(Error::Unsupported(
            "a primitive position whose logical path cannot be written".into(),
        ));
    }
    found.sort_by(|x, y| x.0.cmp(&y.0));
    let origins = found
        .into_iter()
        .map(|(p, origin)| ValueOrigin {
            path: logical_path(&p).unwrap_or_default(),
            origin,
        })
        .collect();
    Ok(Defaulted {
        violations: Vec::new(),
        document: Some(out),
        origins,
    })
}

/// Walks the mapping `n`, which the validation accepted, and defaults its absent fields.
fn composite(
    n: &mut Node,
    fields: &[(String, Shape)],
    a: &[String],
    path: &mut Path,
    found: &mut Vec<(Path, &'static str)>,
    unwritable: &mut bool,
) {
    let entries = match &mut n.value {
        Value::Map(entries) => entries,
        _ => return,
    };
    for (name, shape) in fields {
        path.push(Seg::Field(name.clone()));
        match entries.iter().position(|e| e.key == *name) {
            Some(i) => {
                found.push((path.clone(), VALUE_AUTHORED));
                visit(&mut entries[i].value, shape, a, path, found, unwritable);
            }
            None => {
                // After the validation an absent field is an optional one.
                if let Some(d) = &shape.default {
                    if logical_path(path).is_none() {
                        // the origin of an inserted value is keyed by its path: no path, no insertion
                        *unwritable = true;
                        path.pop();
                        return;
                    }
                    entries.push(Entry {
                        key: name.clone(),
                        key_pos: None,
                        value: d.0.clone(),
                    });
                    found.push((path.clone(), VALUE_DEFAULT));
                }
            }
        }
        path.pop();
    }
}

/// Goes below a present value: a composite, or the records of a declared list. Everything else is a leaf.
fn visit(
    value: &mut Node,
    shape: &Shape,
    a: &[String],
    path: &mut Path,
    found: &mut Vec<(Path, &'static str)>,
    unwritable: &mut bool,
) {
    match (&shape.shape_type, &value.value) {
        (ShapeType::Composite(fields), _) => composite(value, fields, a, path, found, unwritable),
        (ShapeType::DeclaredList { elements, .. }, _) => {
            let items = match &mut value.value {
                Value::Seq(items) => items,
                _ => return,
            };
            for rec in items {
                // Not reachable as an error: the validation refused a record without an identity.
                let (case, id) = match identify(rec, elements, a).ok() {
                    Some(found) => found,
                    None => continue,
                };
                path.push(Seg::Item(id));
                composite(rec, &case.fields, a, path, found, unwritable);
                path.pop();
            }
        }
        _ => {}
    }
}
