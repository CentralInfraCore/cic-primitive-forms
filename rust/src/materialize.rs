//! Materialize a flat document (P5-2, first unit; `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 11, ADR 0014). The Go peer is `go/materialize.go`; the
//! shared cases are `testdata/p5/materialize.json`.
//!
//! The output is the candidate object of the engine's `MaterializedObject` (`cic-primitive-engine` `engine/src/materialized.rs`) for an intent document: a flat
//! map from a field name to its capability, its provenance and its value, the projection `{"fields": [{key, capability, provenance, value}]}` sorted by key,
//! and the `output_digest` of that projection (the digest of its canonical bytes, [`crate::canonical`]).
//!
//! What this unit decides, and what it refuses (each refusal is a [`MaterializeError`], never a guess):
//!
//! - **Flat only.** Every declared position of the schema must be a top-level scalar (or an opaque value that holds a scalar). A composite, a list or a declared
//!   list is `not_flat`: the engine's key is a flat field name and nothing says what the key of a nested position is. A set is `set`: its order is not decided
//!   (decision H, fail closed). The refusal is of the **schema**, so a nested position that the document leaves out still refuses.
//! - **Capability is an input of the caller**, a map from a field name to `implemented`, `not_implemented` or `deprecated`. A declared field with none is
//!   `capability_missing`, a value of another spelling is `capability_unknown`, a key that is not a declared field is `capability_undeclared`. Nothing is derived
//!   from the authored document (ADR 0010: an authored position's capability is not evidence). The digest records the value the caller gave; whether the caller
//!   is entitled to it is a second claim that the digest does not make.
//! - **An omitted optional field with no default is refused** (`omitted_optional`). The engine can hold an `Authored(None)` but its spec (B2) leaves open
//!   whether it is the same as a field the author did not write, and the provenance is part of the digested claim. Only a field that is written, or filled
//!   by its schema default, is materialized.
//! - A document the validation refuses is `invalid_document`; one the defaults cannot be applied to is `unsupported`; none at all is `no_document`.
//!
//! The order of the checks is part of the contract (the two languages must give the same first reason): the schema, then the capability map (undeclared keys in
//! byte order, then the declared fields in schema order), then the document, then the fields in schema order, then the canonical bytes.
//!
//! `expected_keys` is the declared top-level field names read from the schema, in byte order. It is what the engine's `try_new` is given as the key set to be
//! complete against. It is derived here from the schema directly, not from the fields that were built, so that a field lost on the way is not lost twice;
//! the proof of that independence is the harness (a key set of the generator that never saw this code).

use std::collections::BTreeMap;
use std::fmt;

use crate::canonical::{canonical_bytes, canonical_digest, CanonicalError};
use crate::defaults::{apply_defaults, VALUE_DEFAULT};
use crate::node::{Entry, Node, Value};
use crate::recognize::{logical_path, Seg};
use crate::validate::{Shape, ShapeType};

pub const MATERIALIZE_NO_DOCUMENT: &str = "no_document";
pub const MATERIALIZE_INVALID_DOCUMENT: &str = "invalid_document";
pub const MATERIALIZE_UNSUPPORTED: &str = "unsupported";
pub const MATERIALIZE_NOT_FLAT: &str = "not_flat";
pub const MATERIALIZE_SET: &str = "set";
pub const MATERIALIZE_OMITTED_OPTIONAL: &str = "omitted_optional";
pub const MATERIALIZE_CAPABILITY_MISSING: &str = "capability_missing";
pub const MATERIALIZE_CAPABILITY_UNKNOWN: &str = "capability_unknown";
pub const MATERIALIZE_CAPABILITY_UNDECLARED: &str = "capability_undeclared";

/// The three capabilities of the engine's enum, by their spelling in the projection.
const CAPABILITIES: [&str; 3] = ["implemented", "not_implemented", "deprecated"];

/// The provenance of an intent-side field, by its spelling in the projection (`authored`, `schema_default`).
const PROVENANCE_AUTHORED: &str = "authored";
const PROVENANCE_SCHEMA_DEFAULT: &str = "schema_default";

/// Why a document was not materialized, and at which field (`""` when the refusal is not about one).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MaterializeError {
    pub reason: &'static str,
    pub field: String,
}

impl fmt::Display for MaterializeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "not materialized: {} ({})", self.reason, self.field)
    }
}

impl std::error::Error for MaterializeError {}

fn refuse(reason: &'static str, field: &str) -> MaterializeError {
    MaterializeError {
        reason,
        field: field.to_string(),
    }
}

/// One field of the candidate object.
#[derive(Debug, Clone)]
pub struct MaterializedField {
    pub key: String,
    /// `implemented`, `not_implemented` or `deprecated`, as the caller gave it.
    pub capability: String,
    /// `authored` or `schema_default`.
    pub provenance: &'static str,
    pub value: Node,
}

/// The candidate object of a flat document, its projection and its `output_digest`.
#[derive(Debug, Clone)]
pub struct Materialized {
    /// In byte order of the key.
    pub fields: Vec<MaterializedField>,
    /// The declared top-level field names, in byte order (see the module comment).
    pub expected_keys: Vec<String>,
    /// The canonical bytes of the projection, as text (they are UTF-8).
    pub projection_json: String,
    /// `sha256:` and the hex digest of the canonical bytes of the projection.
    pub output_digest: String,
}

/// Materializes the flat document `doc` against `root`. `a` is the key set A of the Access atom, as for [`apply_defaults`]; `capabilities` maps a field to its
/// capability.
///
/// # Errors
/// A [`MaterializeError`] with the reason of the module comment.
pub fn materialize_flat(
    doc: Option<&Node>,
    root: &Shape,
    a: &[String],
    capabilities: &BTreeMap<String, String>,
) -> Result<Materialized, MaterializeError> {
    let declared = match &root.shape_type {
        ShapeType::Composite(declared) => declared,
        _ => return Err(refuse(MATERIALIZE_NOT_FLAT, "")),
    };
    for (name, shape) in declared {
        match shape.shape_type {
            ShapeType::Composite(_) | ShapeType::List { .. } | ShapeType::DeclaredList { .. } => {
                return Err(refuse(MATERIALIZE_NOT_FLAT, name))
            }
            ShapeType::Set { .. } => return Err(refuse(MATERIALIZE_SET, name)),
            ShapeType::Scalar(_) | ShapeType::Opaque => {}
        }
    }
    for key in capabilities.keys() {
        if !declared.iter().any(|(name, _)| name == key) {
            return Err(refuse(MATERIALIZE_CAPABILITY_UNDECLARED, key));
        }
    }
    for (name, _) in declared {
        match capabilities.get(name) {
            None => return Err(refuse(MATERIALIZE_CAPABILITY_MISSING, name)),
            Some(c) if !CAPABILITIES.contains(&c.as_str()) => {
                return Err(refuse(MATERIALIZE_CAPABILITY_UNKNOWN, name))
            }
            Some(_) => {}
        }
    }
    if doc.is_none() {
        return Err(refuse(MATERIALIZE_NO_DOCUMENT, ""));
    }
    let defaulted =
        apply_defaults(doc, root, a).map_err(|_| refuse(MATERIALIZE_UNSUPPORTED, ""))?;
    let entries = match defaulted.document.map(|d| d.value) {
        Some(Value::Map(entries)) => entries,
        _ => return Err(refuse(MATERIALIZE_INVALID_DOCUMENT, "")),
    };
    let mut fields = Vec::with_capacity(declared.len());
    for (name, _) in declared {
        let entry = match entries.iter().find(|e| e.key == *name) {
            Some(entry) => entry,
            None => return Err(refuse(MATERIALIZE_OMITTED_OPTIONAL, name)),
        };
        if matches!(entry.value.value, Value::Map(_) | Value::Seq(_)) {
            return Err(refuse(MATERIALIZE_NOT_FLAT, name));
        }
        // a field name that cannot be written as a path has no origin of its own: the origins of two such fields would share the key ""
        let path = logical_path(&vec![Seg::Field(name.clone())]).unwrap_or_default();
        if path.is_empty() {
            return Err(refuse(MATERIALIZE_UNSUPPORTED, name));
        }
        let provenance = if defaulted
            .origins
            .iter()
            .any(|o| o.path == path && o.origin == VALUE_DEFAULT)
        {
            PROVENANCE_SCHEMA_DEFAULT
        } else {
            PROVENANCE_AUTHORED
        };
        fields.push(MaterializedField {
            key: name.clone(),
            capability: capabilities[name].clone(),
            provenance,
            value: entry.value.clone(),
        });
    }
    fields.sort_by(|x, y| x.key.cmp(&y.key));
    let mut expected_keys: Vec<String> = declared.iter().map(|(name, _)| name.clone()).collect();
    expected_keys.sort();
    let bytes = canonical_bytes(&projection(&fields))
        .map_err(|CanonicalError { reason }| refuse(reason, ""))?;
    Ok(Materialized {
        fields,
        expected_keys,
        projection_json: String::from_utf8_lossy(&bytes).into_owned(),
        output_digest: canonical_digest(&bytes),
    })
}

fn text(s: &str) -> Node {
    Node::new(Value::Str(s.to_string()))
}

fn entry(key: &str, value: Node) -> Entry {
    Entry {
        key: key.to_string(),
        key_pos: None,
        value,
    }
}

/// `{"fields": [{key, capability, provenance, value}]}`, as the engine's `materialized_object_projection` builds it for an intent-side field.
fn projection(fields: &[MaterializedField]) -> Node {
    let items = fields
        .iter()
        .map(|f| {
            Node::new(Value::Map(vec![
                entry("key", text(&f.key)),
                entry("capability", text(&f.capability)),
                entry("provenance", text(f.provenance)),
                entry("value", f.value.clone()),
            ]))
        })
        .collect();
    Node::new(Value::Map(vec![entry(
        "fields",
        Node::new(Value::Seq(items)),
    )]))
}
