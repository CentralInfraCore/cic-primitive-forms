//! Presence and value validation of a document against a Shape (ADR 0003, ADR 0007).
//!
//! The Shape members used are the ones of the pinned grammar: `shape_type` (here `Scalar` with a
//! `scalar_type`, `Composite` with `fields`, `Collection`/list with `min_items`, `Opaque`),
//! `mandatory`/`optional`, `nullable` and `default`. Nothing else about a primitive is defined here.
//!
//! * **Absent is not a value.** `validate` takes `Option<&Node>`: `None` is a missing field, and
//!   `Some(Null)` is a field present with the value `null`. They are different states with different
//!   answers (`MISSING_MANDATORY` against `NULL_NOT_ALLOWED`).
//! * **A default is a candidate and nothing more.** An absent optional field that declares a default
//!   is *listed*; nothing is applied, and Role eligibility is not decided (P3). A default is never
//!   triggered by `null`, an empty string, `0` or `false`.
//! * **Anchors.** A missing mandatory field is anchored at its parent (an absent field has no text),
//!   an explicit `null` at the value, and an empty scalar, which has no text of its own, at the key.
//! * The response is every error of the phase, ordered by canonical `logical_path`, then code
//!   (ADR 0007). Recognition (ADR 0001) runs first at every node: an `EXPANDED_WITH_EXTRA_KEY` makes
//!   the response a recognition one.
//!
//! Outside the slice, each an explicit [`Error::Unsupported`] rather than a guess: a value of the
//! wrong type that has no code in the registry (a string where an integer is declared, a list where a
//! mapping is declared, ...), an expanded Access node in the validated document (its `value` is
//! validated with expand/collapse, P2), and the scalar types other than string, integer and boolean.
//! The input is never modified.

use crate::error::{Error, Location, Phase, Violation};
use crate::listitem::{identify, Elements, DUPLICATE_IDENTITY};
use crate::node::{Node, Value};
use crate::recognize::{classify, location, logical_path, sorted, Class, Path, Seg};

/// `mandatory` or `optional`: the presence axis of the Shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Presence {
    Mandatory,
    Optional,
}

/// The `scalar_type`s this slice checks.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScalarType {
    String,
    Integer,
    Boolean,
}

/// The `shape_type` and what it needs.
#[derive(Debug, Clone, PartialEq)]
pub enum ShapeType {
    Scalar(ScalarType),
    /// `fields` in the order they are checked.
    Composite(Vec<(String, Shape)>),
    /// A list; its elements are not described by this slice.
    List {
        min_items: Option<usize>,
    },
    /// Any value, nothing below it.
    Opaque,
    /// A `collection`/`set`: a list of scalars of type `item`, no key.
    Set {
        item: ScalarType,
        min_items: Option<usize>,
    },
    /// A list whose element structure the schema declares (`item_fields`, `item_choice`). Each element
    /// is identified by its declared key (ADR 0002, ADR 0006), its case is selected by field presence
    /// and its fields are validated.
    DeclaredList {
        min_items: Option<usize>,
        elements: Elements,
    },
}

/// A field descriptor: the members of a `nodes` entry that presence validation reads.
#[derive(Debug, Clone, PartialEq)]
pub struct Shape {
    pub shape_type: ShapeType,
    pub presence: Presence,
    pub nullable: bool,
    /// Only whether a default is declared matters here: it is never applied.
    pub has_default: bool,
}

/// The result of validating one document (or its absence) against a shape.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Validation {
    /// The machine response; empty means valid.
    pub violations: Vec<Violation>,
    /// Canonical paths (`$` for the root) of absent optional fields that declare a default, in path
    /// order. Candidates only: nothing is substituted.
    pub default_candidates: Vec<String>,
}

struct Walk<'a> {
    a: &'a [String],
    recognition: Vec<(Path, Violation)>,
    validation: Vec<(Path, Violation)>,
    candidates: Vec<(Path, String)>,
    expanded: bool,
}

fn found(code: &'static str, phase: Phase, location: Location) -> Violation {
    Violation {
        code,
        phase,
        location,
        logical_path: None,
    }
}

impl Walk<'_> {
    fn validation(&mut self, path: &Path, code: &'static str, at: Location) {
        self.validation
            .push((path.clone(), found(code, Phase::Validate, at)));
    }

    /// `key_pos` and `parent_pos` are where the field's key and its parent mapping start.
    fn visit(
        &mut self,
        node: Option<&Node>,
        shape: &Shape,
        path: &mut Path,
        (key_pos, parent_pos): (Option<crate::node::Pos>, Option<crate::node::Pos>),
    ) -> Result<(), Error> {
        let node = match node {
            Some(node) => node,
            None => return self.absent(shape, path, parent_pos),
        };
        if matches_null(node) {
            if !shape.nullable {
                // An empty scalar has no position of its own: the key is the anchor.
                self.validation(path, "NULL_NOT_ALLOWED", location(node.pos.or(key_pos)));
            }
            return Ok(());
        }
        match classify(node, self.a) {
            Err(v) => {
                self.recognition.push((path.clone(), v));
                return Ok(());
            }
            Ok(Class::Expanded) => {
                self.expanded = true;
                return Ok(());
            }
            Ok(_) => {}
        }
        match &shape.shape_type {
            ShapeType::Opaque => Ok(()),
            ShapeType::Scalar(t) => self.scalar(node, *t, path),
            ShapeType::List { min_items } => self.list(node, *min_items, path),
            ShapeType::Set { item, min_items } => self.set(node, *item, *min_items, path),
            ShapeType::DeclaredList {
                min_items,
                elements,
            } => {
                self.list(node, *min_items, path)?;
                self.elements(node, elements, path)
            }
            ShapeType::Composite(fields) => self.composite(node, fields, path),
        }
    }

    fn absent(
        &mut self,
        shape: &Shape,
        path: &Path,
        parent_pos: Option<crate::node::Pos>,
    ) -> Result<(), Error> {
        if shape.presence == Presence::Mandatory {
            self.validation(path, "MISSING_MANDATORY", location(parent_pos));
        } else if shape.has_default {
            let text = if path.is_empty() {
                Some("$".to_string())
            } else {
                logical_path(path)
            };
            if let Some(text) = text {
                self.candidates.push((path.clone(), text));
            }
        }
        Ok(())
    }

    fn scalar(&mut self, node: &Node, t: ScalarType, path: &Path) -> Result<(), Error> {
        let fits = match (&node.value, t) {
            (Value::Map(_), _) => {
                self.validation(path, "MAP_AT_SCALAR_POSITION", location(node.pos));
                return Ok(());
            }
            (Value::Str(_), ScalarType::String)
            | (Value::Int(_), ScalarType::Integer)
            | (Value::Bool(_), ScalarType::Boolean) => true,
            _ => false,
        };
        if fits {
            Ok(())
        } else {
            Err(Error::Unsupported(format!(
                "a {:?} value where {:?} is declared (no code in the registry)",
                kind_name(&node.value),
                t
            )))
        }
    }

    fn list(&mut self, node: &Node, min_items: Option<usize>, path: &Path) -> Result<(), Error> {
        let items = match &node.value {
            Value::Seq(items) => items,
            other => {
                return Err(Error::Unsupported(format!(
                    "a {:?} value where a list is declared (no code in the registry)",
                    kind_name(other)
                )))
            }
        };
        if min_items.is_some_and(|min| items.len() < min) {
            self.validation(path, "MIN_ITEMS_NOT_MET", location(node.pos));
        }
        Ok(())
    }

    fn elements(&mut self, node: &Node, elements: &Elements, path: &mut Path) -> Result<(), Error> {
        let items = match &node.value {
            Value::Seq(items) => items,
            _ => return Ok(()),
        };
        let mut found = Vec::new();
        for it in items {
            if !matches!(it.value, Value::Map(_)) {
                return Err(Error::Unsupported(format!(
                    "a {:?} value where a list element record is declared (no code in the registry)",
                    kind_name(&it.value)
                )));
            }
            match identify(it, elements, self.a) {
                Ok((case, id)) => found.push((it, case, Seg::Item(id))),
                Err((code, at)) => self.validation(path, code, location(at.pos)),
            }
        }
        // Two elements with one identity cannot be told apart by a path: one error at the identity, and
        // nothing is reported below a node that already has an error.
        let twice = |seg: &Seg| found.iter().filter(|(_, _, s)| s == seg).count() > 1;
        // The error is anchored at the second element that has the identity.
        let mut seen: Vec<&Seg> = Vec::new();
        let mut reported: Vec<&Seg> = Vec::new();
        for (it, _, seg) in &found {
            if seen.contains(&seg) && !reported.contains(&seg) {
                reported.push(seg);
                path.push(seg.clone());
                self.validation(path, DUPLICATE_IDENTITY, location(it.pos));
                path.pop();
            }
            seen.push(seg);
        }
        for (it, case, seg) in &found {
            if twice(seg) {
                continue;
            }
            path.push(seg.clone());
            let result = self.composite(it, &case.fields, path);
            path.pop();
            result?;
        }
        Ok(())
    }

    fn set(
        &mut self,
        node: &Node,
        item: ScalarType,
        min_items: Option<usize>,
        path: &Path,
    ) -> Result<(), Error> {
        let items = match &node.value {
            Value::Seq(items) => items,
            other => {
                return Err(Error::Unsupported(format!(
                    "a {:?} value where a set is declared (no code in the registry)",
                    kind_name(other)
                )))
            }
        };
        if min_items.is_some_and(|min| items.len() < min) {
            self.validation(path, "MIN_ITEMS_NOT_MET", location(node.pos));
        }
        for it in items {
            self.scalar(it, item, path)?;
        }
        Ok(())
    }

    fn composite(
        &mut self,
        node: &Node,
        fields: &[(String, Shape)],
        path: &mut Path,
    ) -> Result<(), Error> {
        let entries = match &node.value {
            Value::Map(entries) => entries,
            other => {
                return Err(Error::Unsupported(format!(
                    "a {:?} value where a mapping is declared (no code in the registry)",
                    kind_name(other)
                )))
            }
        };
        for (name, shape) in fields {
            let entry = entries.iter().find(|e| e.key == *name);
            path.push(Seg::Field(name.clone()));
            let result = self.visit(
                entry.map(|e| &e.value),
                shape,
                path,
                (entry.and_then(|e| e.key_pos), node.pos),
            );
            path.pop();
            result?;
        }
        for e in entries
            .iter()
            .filter(|e| !fields.iter().any(|(n, _)| *n == e.key))
        {
            let mut p = path.clone();
            p.push(Seg::Field(e.key.clone()));
            self.validation(&p, "UNKNOWN_FIELD", location(e.key_pos));
        }
        Ok(())
    }
}

fn matches_null(node: &Node) -> bool {
    std::mem::discriminant(&node.value) == std::mem::discriminant(&Value::Null)
}

fn kind_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Int(_) => "integer",
        Value::Str(_) => "string",
        Value::Seq(_) => "list",
        Value::Map(_) => "mapping",
    }
}

/// Validates `node` (`None` is an absent field) against `shape`. `a` is the expanded key set `A`
/// that recognition needs.
///
/// # Errors
/// [`Error::Unsupported`] for the cases listed in the module documentation.
pub fn validate(node: Option<&Node>, shape: &Shape, a: &[String]) -> Result<Validation, Error> {
    let mut walk = Walk {
        a,
        recognition: Vec::new(),
        validation: Vec::new(),
        candidates: Vec::new(),
        expanded: false,
    };
    walk.visit(node, shape, &mut Vec::new(), (None, None))?;
    if walk.expanded {
        return Err(Error::Unsupported(
            "an expanded Access node inside a validated document (P2)".into(),
        ));
    }
    walk.candidates.sort_by(|x, y| x.0.cmp(&y.0));
    let violations = if walk.recognition.is_empty() {
        sorted(walk.validation)
    } else {
        sorted(walk.recognition)
    };
    Ok(Validation {
        violations,
        default_candidates: walk.candidates.into_iter().map(|(_, t)| t).collect(),
    })
}
