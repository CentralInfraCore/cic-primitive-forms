//! `diff` (P4, steps D1-D3; ADR 0008 and docs/P4-DIFF-PATCH.md): the deterministic difference between two logical views of one schema.
//!
//! A view is a document read under a context: [`diff`] validates it (every expanded Access wrapper read as its value), expands it (ADR 0001:
//! every primitive position carries its value and its five declaration fields) and compares the expanded trees along the schema. The
//! result is evidence of a difference and nothing else: events in a fixed order, the writing differences in `representation`, and the exact
//! bytes of both in [`DiffResult::canonical`]. Nothing is resolved, patched or judged; a difference is not drift.
//!
//! What makes a difference: the logical value of a scalar or an opaque position, a declaration field (compared type-strictly), the elements
//! of a set, a node that exists on one side only. What does not: mapping order, list order (lists are paired by identity), flow or block
//! style, quoting, and short against expanded writing of the same declaration, which is reported in `representation`. A node that appears or
//! disappears is one event at its top, carrying its expanded logical fragment; a change below gives no event for the parent.
//!
//! Scope: a diff compares only what the caller says was observed. A scope entry is a canonical path whose subtree is comparable; absence
//! inside it is absence, nothing outside it is compared, and the scope is applied before parent and child events are merged. An entry nested
//! in another one adds nothing.
//!
//! The Go peer is go/diff.go, go/diffcompare.go and go/diffcanonical.go.

use std::cmp::Ordering;

use crate::access::Grammar;
use crate::error::{Error, Location, Phase, Violation};
use crate::expand::{entry, expand, path_text, Context, Provenance};
use crate::listitem::{
    identify, Case, Elements, KeyComp, CASE_AMBIGUOUS, CASE_NONE, DUPLICATE_IDENTITY, KEY_MISSING,
    KEY_TYPE, KEY_UNSUPPORTED,
};
use crate::node::{Entry, Node, Value};
use crate::pathtext::{parse_path, resolve_path, PathSeg};
use crate::recognize::{classify, logical_path, Class, Path, Seg};
use crate::sourcespan::path_failure;
use crate::validate::{Shape, ShapeType};

/// The code of the context error of the phase `diff` (ADR 0008).
pub const DIFF_CONTEXT_MISMATCH: &str = "DIFF_CONTEXT_MISMATCH";
/// The code for a float or an integer beyond 64 bits in an operand (ADR 0008, provisional).
pub const DIFF_UNSUPPORTED_VALUE: &str = "DIFF_UNSUPPORTED_VALUE";

const VALUE: &str = "value";

/// What a view was read under: the schema and its digest, the grammar and its digest, and the surface. Two views with different contexts are
/// never compared (`DIFF_CONTEXT_MISMATCH`).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DiffContext {
    pub schema: String,
    pub schema_digest: String,
    pub grammar: String,
    pub grammar_digest: String,
    pub surface: String,
}

/// One operand: the document as authored, the context it was read under, and the trusted context of ADR 0010 it is expanded with.
#[derive(Debug, Clone)]
pub struct DiffView<'a> {
    pub doc: &'a Node,
    pub context: DiffContext,
    pub trust: Context,
}

/// The kinds of an event of a diff.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum EventKind {
    Added,
    PolicyDeclarationChange,
    Removed,
    SetChange,
    ValueChange,
}

impl EventKind {
    /// The name in the canonical bytes. The order of the variants is the bytewise order of these names.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            EventKind::Added => "added",
            EventKind::PolicyDeclarationChange => "policy_declaration_change",
            EventKind::Removed => "removed",
            EventKind::SetChange => "set_change",
            EventKind::ValueChange => "value_change",
        }
    }
}

/// One declaration field of an expanded position that differs.
#[derive(Debug, Clone)]
pub struct DeclChange {
    pub field: String,
    pub previous: Node,
    pub next: Node,
}

/// One difference. Which fields are set depends on `kind`: `previous` for removed and value_change, `next` for added and value_change (an
/// appearing or disappearing node carries its expanded fragment), `changed` for policy_declaration_change in field order, `added` and
/// `removed` (sorted) for set_change.
#[derive(Debug, Clone)]
pub struct DiffEvent {
    pub kind: EventKind,
    pub path: String,
    pub previous: Option<Node>,
    pub next: Option<Node>,
    pub changed: Vec<DeclChange>,
    pub added: Vec<Node>,
    pub removed: Vec<Node>,
    order: Path,
}

/// A writing difference that is not a difference: a position present in both views that was written short in one and expanded in the other.
#[derive(Debug, Clone)]
pub struct RepresentationChange {
    pub path: String,
    pub previous: &'static str,
    pub next: &'static str,
    order: Path,
}

/// The result of [`diff`]. The views are logically equal exactly when `changes` is empty.
#[derive(Debug, Clone, Default)]
pub struct DiffResult {
    pub changes: Vec<DiffEvent>,
    pub representation: Vec<RepresentationChange>,
}

fn failure(code: &'static str, path: Option<String>, operand: Option<&'static str>) -> Error {
    Error::Contract(vec![Violation {
        code,
        phase: Phase::Diff,
        location: Location::NotApplicable,
        logical_path: path,
        path_offset: None,
        operand,
    }])
}

/// A contract error found while reading a view, reduced to its first violation, in the phase `path_of` for an identity code, with the operand
/// named. Any other error is returned as it is.
fn for_operand(err: Error, operand: &'static str) -> Error {
    match err {
        Error::Contract(vs) => {
            let mut v = vs[0].clone();
            if [
                KEY_MISSING,
                KEY_TYPE,
                KEY_UNSUPPORTED,
                CASE_NONE,
                CASE_AMBIGUOUS,
                DUPLICATE_IDENTITY,
            ]
            .contains(&v.code)
            {
                v.phase = Phase::PathOf;
            }
            v.operand = Some(operand);
            Error::Contract(vec![v])
        }
        other => other,
    }
}

/// Whether anything inside `n` is an unsupported number.
fn holds_unsupported(n: &Node) -> bool {
    match &n.value {
        Value::Unsupported(_) => true,
        Value::Seq(items) => items.iter().any(holds_unsupported),
        Value::Map(entries) => entries.iter().any(|e| holds_unsupported(&e.value)),
        _ => false,
    }
}

/// The logical path of the first position of the value `v` that holds an unsupported number, in the order of the field names. A value the
/// schema does not describe at the place (an undeclared key, a list element without an identity) is named by the position above it.
fn unsupported_below(v: &Node, shape: &Shape, path: &Path, a: &[String]) -> Option<Path> {
    match (&shape.shape_type, &v.value) {
        (ShapeType::Composite(fields), Value::Map(entries)) => {
            let mut sorted: Vec<&Entry> = entries.iter().collect();
            sorted.sort_by(|x, y| x.key.cmp(&y.key));
            for e in sorted {
                match fields.iter().find(|(name, _)| *name == e.key) {
                    None => {
                        if holds_unsupported(&e.value) {
                            return Some(path.clone());
                        }
                    }
                    Some((name, field)) => {
                        let mut p = path.clone();
                        p.push(Seg::Field(name.clone()));
                        if let Some(found) = unsupported_at(&e.value, field, &p, a) {
                            return Some(found);
                        }
                    }
                }
            }
            None
        }
        (ShapeType::DeclaredList { elements, .. }, Value::Seq(items)) => {
            for it in items {
                match identify(it, elements, a) {
                    Err(_) => {
                        if holds_unsupported(it) {
                            return Some(path.clone());
                        }
                    }
                    Ok((case, id)) => {
                        let mut p = path.clone();
                        p.push(Seg::Item(id));
                        if let Some(found) = unsupported_below(it, &record_shape(case), &p, a) {
                            return Some(found);
                        }
                    }
                }
            }
            None
        }
        _ => holds_unsupported(v).then(|| path.clone()),
    }
}

/// One position: through an expanded wrapper at its value, and at the other members of the wrapper as a whole.
fn unsupported_at(n: &Node, shape: &Shape, path: &Path, a: &[String]) -> Option<Path> {
    match (classify(n, a), &n.value) {
        (Ok(Class::Expanded), Value::Map(entries)) => {
            for e in entries {
                if e.key == VALUE {
                    if let Some(found) = unsupported_below(&e.value, shape, path, a) {
                        return Some(found);
                    }
                } else if holds_unsupported(&e.value) {
                    return Some(path.clone());
                }
            }
            None
        }
        _ => unsupported_below(n, shape, path, a),
    }
}

/// The composite shape of the fields of a list element.
fn record_shape(case: &Case) -> Shape {
    Shape {
        shape_type: ShapeType::Composite(case.fields.clone()),
        presence: crate::validate::Presence::Optional,
        nullable: false,
        has_default: false,
        default: None,
    }
}

fn read_view(
    v: &DiffView<'_>,
    root: &Shape,
    a: &[String],
    g: Grammar,
    operand: &'static str,
) -> Result<(Node, Provenance), Error> {
    crate::editscalar::refuse_invalid(v.doc, root, a).map_err(|e| for_operand(e, operand))?;
    expand(v.doc, root, a, g, &v.trust).map_err(|e| for_operand(e, operand))
}

fn seg_of(s: &PathSeg) -> Seg {
    match s {
        PathSeg::Field(f) => Seg::Field(f.clone()),
        PathSeg::Item(id) => Seg::Item(id.clone()),
    }
}

/// The scope as canonical paths, without the entries that lie inside another one and the repeated ones. `None` is the whole document; a scope
/// that is given and empty compares nothing.
fn scope_entries(root: &Shape, scope: Option<&[String]>) -> Result<Vec<Path>, Error> {
    let Some(scope) = scope else {
        return Ok(vec![Vec::new()]);
    };
    let mut all: Vec<Path> = Vec::with_capacity(scope.len());
    for text in scope {
        let segs = parse_path(text).map_err(path_failure)?;
        resolve_path(root, &segs).map_err(path_failure)?;
        all.push(segs.iter().map(seg_of).collect());
    }
    let mut keep: Vec<Path> = Vec::new();
    for (i, e) in all.iter().enumerate() {
        let inside = all.iter().any(|o| o.len() < e.len() && e.starts_with(o));
        let repeated = all[..i].iter().any(|o| o == e);
        if !inside && !repeated {
            keep.push(e.clone());
        }
    }
    Ok(keep)
}

/// A place of an expanded tree: a position (a wrapper, with the shape of the field) or a record (the root, or a list element, with the fields).
#[derive(Clone, Copy)]
enum Place<'a> {
    Position(&'a Shape),
    Record(&'a [(String, Shape)]),
}

#[derive(Clone, Copy)]
struct Slot<'a> {
    node: &'a Node,
    place: Place<'a>,
}

impl<'a> Slot<'a> {
    /// The record that holds the fields below this slot: the record itself, or the value of a composite position.
    fn fields(&self) -> Option<(&'a Node, &'a [(String, Shape)])> {
        match self.place {
            Place::Record(fields) => Some((self.node, fields)),
            Place::Position(shape) => match (&shape.shape_type, entry(self.node, VALUE)) {
                (ShapeType::Composite(fields), Some(v)) if matches!(v.value, Value::Map(_)) => {
                    Some((v, fields))
                }
                _ => None,
            },
        }
    }
}

/// Walks `path` below `slot` in an expanded tree. `Ok(None)` is a place the tree does not have; a place below a value that cannot hold it is
/// refused, not guessed.
fn descend<'a>(slot: Slot<'a>, path: &[Seg], a: &[String]) -> Result<Option<Slot<'a>>, Error> {
    let mut at = slot;
    for s in path {
        match s {
            Seg::Item(id) => {
                let (Place::Position(shape), Some(list)) = (at.place, entry(at.node, VALUE)) else {
                    return Err(Error::Unsupported(
                        "a list element below a value that is not a list".into(),
                    ));
                };
                let (ShapeType::DeclaredList { elements, .. }, Value::Seq(items)) =
                    (&shape.shape_type, &list.value)
                else {
                    return Err(Error::Unsupported(
                        "a list element below a value that is not a list".into(),
                    ));
                };
                let found = items.iter().find_map(|it| match identify(it, elements, a) {
                    Ok((case, got)) if got == *id => Some(Slot {
                        node: it,
                        place: Place::Record(&case.fields),
                    }),
                    _ => None,
                });
                match found {
                    Some(next) => at = next,
                    None => return Ok(None),
                }
            }
            Seg::Field(name) => {
                let Some((rec, fields)) = at.fields() else {
                    return Err(Error::Unsupported(
                        "a path below a value that is not a mapping".into(),
                    ));
                };
                let Some(e) = entry(rec, name) else {
                    return Ok(None);
                };
                let Some((_, shape)) = fields.iter().find(|(n, _)| n == name) else {
                    return Ok(None);
                };
                at = Slot {
                    node: e,
                    place: Place::Position(shape),
                };
            }
            Seg::Index(_) => return Ok(None),
        }
    }
    Ok(Some(at))
}

struct Differ<'a> {
    a: &'a [String],
    prov_a: &'a Provenance,
    prov_b: &'a Provenance,
    result: DiffResult,
}

/// A list element with its identity, in the order the declaration gives (the case first).
struct Element<'n, 'c> {
    node: &'n Node,
    case: &'c Case,
    id: Vec<KeyComp>,
    order: usize,
}

impl Element<'_, '_> {
    fn compare(&self, other: &Self) -> Ordering {
        self.order
            .cmp(&other.order)
            .then_with(|| self.id.cmp(&other.id))
    }
}

fn elements<'n, 'c>(list: &'n Node, els: &'c Elements, a: &[String]) -> Vec<Element<'n, 'c>> {
    let Value::Seq(items) = &list.value else {
        return Vec::new();
    };
    items
        .iter()
        .filter_map(|it| identify(it, els, a).ok().map(|(case, id)| (it, case, id)))
        .map(|(node, case, id)| Element {
            node,
            case,
            id,
            order: els
                .cases
                .iter()
                .position(|o| o.name == case.name)
                .unwrap_or(0),
        })
        .collect()
}

/// The order of ADR 0008 for set elements: by kind rank (null, booleans, integers, strings), then by value.
fn compare_scalars(x: &Node, y: &Node) -> Ordering {
    fn rank(n: &Node) -> u8 {
        match n.value {
            Value::Null => 0,
            Value::Bool(_) => 1,
            Value::Int(_) => 2,
            _ => 3,
        }
    }
    match (&x.value, &y.value) {
        (Value::Bool(p), Value::Bool(q)) => p.cmp(q),
        (Value::Int(p), Value::Int(q)) => p.cmp(q),
        (Value::Str(p), Value::Str(q)) => p.cmp(q),
        _ => rank(x).cmp(&rank(y)),
    }
}

fn sorted_scalars(list: &Node) -> Vec<Node> {
    let Value::Seq(items) = &list.value else {
        return Vec::new();
    };
    let mut out: Vec<Node> = items.clone();
    out.sort_by(compare_scalars);
    out.dedup_by(|x, y| compare_scalars(x, y) == Ordering::Equal);
    out
}

/// The elements of `b` that `a` lacks and of `a` that `b` lacks, sorted.
fn set_difference(a: &Node, b: &Node) -> (Vec<Node>, Vec<Node>) {
    let has = |set: &Node, x: &Node| match &set.value {
        Value::Seq(items) => items.iter().any(|y| y.same(x)),
        _ => false,
    };
    let added = sorted_scalars(b)
        .into_iter()
        .filter(|x| !has(a, x))
        .collect();
    let removed = sorted_scalars(a)
        .into_iter()
        .filter(|x| !has(b, x))
        .collect();
    (added, removed)
}

fn writing(expanded: bool) -> &'static str {
    if expanded {
        "expanded"
    } else {
        "short"
    }
}

impl Differ<'_> {
    fn emit(&mut self, kind: EventKind, path: &Path, text: String) -> &mut DiffEvent {
        self.result.changes.push(DiffEvent {
            kind,
            path: text,
            previous: None,
            next: None,
            changed: Vec::new(),
            added: Vec::new(),
            removed: Vec::new(),
            order: path.clone(),
        });
        let last = self.result.changes.len() - 1;
        &mut self.result.changes[last]
    }

    /// A place that only one side has: one event, with its fragment.
    fn gone(&mut self, kind: EventKind, slot: Slot<'_>, path: &Path) -> Result<(), Error> {
        let text = path_text(path)?;
        let fragment = match slot.place {
            Place::Record(fields) => self.record_fragment(slot.node, fields),
            Place::Position(shape) => self.position_fragment(slot.node, shape),
        };
        let e = self.emit(kind, path, text);
        if kind == EventKind::Added {
            e.next = Some(fragment);
        } else {
            e.previous = Some(fragment);
        }
        Ok(())
    }

    fn slots(
        &mut self,
        path: &Path,
        a: Option<Slot<'_>>,
        b: Option<Slot<'_>>,
    ) -> Result<(), Error> {
        match (a, b) {
            (Some(sa), Some(sb)) => match sa.place {
                Place::Record(fields) => self.record(fields, sa.node, sb.node, path),
                Place::Position(shape) => self.position(shape, sa.node, sb.node, path),
            },
            (Some(sa), None) => self.gone(EventKind::Removed, sa, path),
            (None, Some(sb)) => self.gone(EventKind::Added, sb, path),
            (None, None) => Ok(()),
        }
    }

    /// The declared fields of two records.
    fn record(
        &mut self,
        fields: &[(String, Shape)],
        ra: &Node,
        rb: &Node,
        path: &Path,
    ) -> Result<(), Error> {
        for (name, shape) in fields {
            let mut p = path.clone();
            p.push(Seg::Field(name.clone()));
            match (entry(ra, name), entry(rb, name)) {
                (Some(na), Some(nb)) => self.position(shape, na, nb, &p)?,
                (Some(na), None) => self.gone(
                    EventKind::Removed,
                    Slot {
                        node: na,
                        place: Place::Position(shape),
                    },
                    &p,
                )?,
                (None, Some(nb)) => self.gone(
                    EventKind::Added,
                    Slot {
                        node: nb,
                        place: Place::Position(shape),
                    },
                    &p,
                )?,
                (None, None) => {}
            }
        }
        Ok(())
    }

    /// One primitive position present in both views: its declaration, its writing, its value.
    fn position(&mut self, shape: &Shape, na: &Node, nb: &Node, path: &Path) -> Result<(), Error> {
        let text = path_text(path)?;
        let mut changed: Vec<DeclChange> = Vec::new();
        for k in self.a.iter().filter(|k| *k != VALUE) {
            let (Some(pa), Some(pb)) = (entry(na, k), entry(nb, k)) else {
                continue;
            };
            if !pa.same(pb) {
                changed.push(DeclChange {
                    field: k.clone(),
                    previous: pa.clone(),
                    next: pb.clone(),
                });
            }
        }
        if !changed.is_empty() {
            changed.sort_by(|x, y| x.field.cmp(&y.field));
            self.emit(EventKind::PolicyDeclarationChange, path, text.clone())
                .changed = changed;
        }
        let was = self.prov_a.get(&text).is_some_and(|o| o.authored_expanded);
        let is = self.prov_b.get(&text).is_some_and(|o| o.authored_expanded);
        if was != is {
            self.result.representation.push(RepresentationChange {
                path: text.clone(),
                previous: writing(was),
                next: writing(is),
                order: path.clone(),
            });
        }
        let (Some(va), Some(vb)) = (entry(na, VALUE), entry(nb, VALUE)) else {
            return Ok(());
        };
        self.value(shape, va, vb, &text, path)
    }

    /// What two positions hold.
    fn value(
        &mut self,
        shape: &Shape,
        va: &Node,
        vb: &Node,
        text: &str,
        path: &Path,
    ) -> Result<(), Error> {
        match &shape.shape_type {
            ShapeType::Scalar(_) | ShapeType::Opaque => {
                if !va.same(vb) {
                    let e = self.emit(EventKind::ValueChange, path, text.to_string());
                    e.previous = Some(va.clone());
                    e.next = Some(vb.clone());
                }
                return Ok(());
            }
            _ => {}
        }
        match (&va.value, &vb.value) {
            (Value::Null, Value::Null) => return Ok(()),
            (Value::Null, _) | (_, Value::Null) => {
                return Err(Error::Unsupported(
                    "a null against a collection (no contract for it)".into(),
                ))
            }
            _ => {}
        }
        match &shape.shape_type {
            ShapeType::Set { .. } => {
                let (added, removed) = set_difference(va, vb);
                if !added.is_empty() || !removed.is_empty() {
                    let e = self.emit(EventKind::SetChange, path, text.to_string());
                    e.added = added;
                    e.removed = removed;
                }
                Ok(())
            }
            ShapeType::Composite(fields) => self.record(fields, va, vb, path),
            ShapeType::DeclaredList { elements, .. } => self.list(elements, va, vb, path),
            _ => Err(Error::Unsupported(
                "a list that declares no elements (no contract for it)".into(),
            )),
        }
    }

    /// Pairs the elements of two lists by identity.
    fn list(&mut self, els: &Elements, va: &Node, vb: &Node, path: &Path) -> Result<(), Error> {
        let ea = elements(va, els, self.a);
        let eb = elements(vb, els, self.a);
        for x in &ea {
            let mut p = path.clone();
            p.push(Seg::Item(x.id.clone()));
            match eb.iter().find(|y| x.compare(y) == Ordering::Equal) {
                None => self.gone(
                    EventKind::Removed,
                    Slot {
                        node: x.node,
                        place: Place::Record(&x.case.fields),
                    },
                    &p,
                )?,
                Some(y) => self.record(&x.case.fields, x.node, y.node, &p)?,
            }
        }
        for y in &eb {
            if !ea.iter().any(|x| x.compare(y) == Ordering::Equal) {
                let mut p = path.clone();
                p.push(Seg::Item(y.id.clone()));
                self.gone(
                    EventKind::Added,
                    Slot {
                        node: y.node,
                        place: Place::Record(&y.case.fields),
                    },
                    &p,
                )?;
            }
        }
        Ok(())
    }

    /// The expanded logical fragment of a position: its wrapper, with what it holds in the order of ADR 0008 (the elements of a list by
    /// identity, the elements of a set sorted).
    fn position_fragment(&self, n: &Node, shape: &Shape) -> Node {
        let Value::Map(entries) = &n.value else {
            return n.clone();
        };
        Node::new(Value::Map(
            entries
                .iter()
                .map(|e| Entry {
                    key: e.key.clone(),
                    key_pos: None,
                    value: if e.key == VALUE {
                        self.value_fragment(&e.value, shape)
                    } else {
                        e.value.clone()
                    },
                })
                .collect(),
        ))
    }

    fn value_fragment(&self, v: &Node, shape: &Shape) -> Node {
        if matches!(v.value, Value::Null) {
            return v.clone();
        }
        match &shape.shape_type {
            ShapeType::Composite(fields) => self.record_fragment(v, fields),
            ShapeType::Set { .. } => Node::new(Value::Seq(sorted_scalars(v))),
            ShapeType::DeclaredList {
                elements: declared, ..
            } => {
                let mut els = elements(v, declared, self.a);
                els.sort_by(Element::compare);
                Node::new(Value::Seq(
                    els.iter()
                        .map(|x| self.record_fragment(x.node, &x.case.fields))
                        .collect(),
                ))
            }
            _ => v.clone(),
        }
    }

    fn record_fragment(&self, rec: &Node, fields: &[(String, Shape)]) -> Node {
        Node::new(Value::Map(
            fields
                .iter()
                .filter_map(|(name, shape)| {
                    entry(rec, name).map(|e| Entry {
                        key: name.clone(),
                        key_pos: None,
                        value: self.position_fragment(e, shape),
                    })
                })
                .collect(),
        ))
    }
}

/// Compares two views, A to B, read under `root` (a composite) with the expanded key set `a` and the bound grammar `g`.
///
/// The order of what it refuses: the contexts differ (`DIFF_CONTEXT_MISMATCH`); a scope entry that is not a canonical path of the schema; a
/// float or an integer beyond 64 bits anywhere in an operand, A first (`DIFF_UNSUPPORTED_VALUE`, whether or not it would take part); a view
/// that does not validate or expand, A first, with the operand named. One error per response. Nothing is compared before every operand has
/// been read.
///
/// `scope` is `None` for the whole document, otherwise the canonical paths of the observed subtrees (an empty list compares nothing).
///
/// # Errors
/// The errors above, as [`Error::Contract`]; [`Error::Unsupported`] for a structure the schema does not allow or a null against a collection.
pub fn diff(
    va: &DiffView<'_>,
    vb: &DiffView<'_>,
    root: &Shape,
    a: &[String],
    g: Grammar,
    scope: Option<&[String]>,
) -> Result<DiffResult, Error> {
    if va.context != vb.context {
        return Err(failure(DIFF_CONTEXT_MISMATCH, None, None));
    }
    let entries = scope_entries(root, scope)?;
    for (view, operand) in [(va, "a"), (vb, "b")] {
        if let Some(p) = unsupported_at(view.doc, root, &Vec::new(), a) {
            return Err(failure(
                DIFF_UNSUPPORTED_VALUE,
                logical_path(&p),
                Some(operand),
            ));
        }
    }
    let (ea, pa) = read_view(va, root, a, g, "a")?;
    let (eb, pb) = read_view(vb, root, a, g, "b")?;
    let ShapeType::Composite(fields) = &root.shape_type else {
        return Err(Error::Unsupported(
            "the root of a document is a composite".into(),
        ));
    };
    let mut d = Differ {
        a,
        prov_a: &pa,
        prov_b: &pb,
        result: DiffResult::default(),
    };
    let top = |node| Slot {
        node,
        place: Place::Record(fields),
    };
    for e in &entries {
        let sa = descend(top(&ea), e, a)?;
        let sb = descend(top(&eb), e, a)?;
        d.slots(e, sa, sb)?;
    }
    d.result
        .changes
        .sort_by(|x, y| x.order.cmp(&y.order).then(x.kind.cmp(&y.kind)));
    d.result
        .representation
        .sort_by(|x, y| x.order.cmp(&y.order));
    Ok(d.result)
}

// --- the canonical bytes (ADR 0008) ---------------------------------------------------------------------------------------------------
//
// UTF-8, no BOM, no whitespace outside strings, object keys sorted by their UTF-8 bytes, integers as 0 or -?[1-9][0-9]*, no floats,
// absence by omitting the field. The writer is here and not a JSON library, so the bytes do not depend on one.

fn text(s: &str) -> Node {
    Node::new(Value::Str(s.to_string()))
}

fn object(entries: Vec<(&str, Node)>) -> Node {
    Node::map(entries)
}

fn event_node(e: &DiffEvent) -> Node {
    let mut m = vec![("kind", text(e.kind.as_str())), ("path", text(&e.path))];
    match e.kind {
        EventKind::Added => m.push((
            "next",
            e.next.clone().unwrap_or_else(|| Node::new(Value::Null)),
        )),
        EventKind::Removed => m.push((
            "previous",
            e.previous.clone().unwrap_or_else(|| Node::new(Value::Null)),
        )),
        EventKind::ValueChange => {
            m.push((
                "previous",
                e.previous.clone().unwrap_or_else(|| Node::new(Value::Null)),
            ));
            m.push((
                "next",
                e.next.clone().unwrap_or_else(|| Node::new(Value::Null)),
            ));
        }
        EventKind::PolicyDeclarationChange => {
            let changed = e
                .changed
                .iter()
                .map(|c| {
                    (
                        c.field.as_str(),
                        object(vec![
                            ("previous", c.previous.clone()),
                            ("next", c.next.clone()),
                        ]),
                    )
                })
                .collect();
            m.push(("changed", object(changed)));
        }
        EventKind::SetChange => {
            m.push(("added", Node::new(Value::Seq(e.added.clone()))));
            m.push(("removed", Node::new(Value::Seq(e.removed.clone()))));
        }
    }
    object(m)
}

impl DiffResult {
    /// The exact bytes of the result: `{"changes":[...],"representation":[...],"version":1}`.
    #[must_use]
    pub fn canonical(&self) -> String {
        let changes = Node::new(Value::Seq(self.changes.iter().map(event_node).collect()));
        let representation = Node::new(Value::Seq(
            self.representation
                .iter()
                .map(|c| {
                    object(vec![
                        ("kind", text("representation_change")),
                        ("path", text(&c.path)),
                        ("previous", text(c.previous)),
                        ("next", text(c.next)),
                    ])
                })
                .collect(),
        ));
        let doc = object(vec![
            ("changes", changes),
            ("representation", representation),
            ("version", Node::new(Value::Int(1))),
        ]);
        let mut out = String::new();
        write_canonical(&mut out, &doc);
        out
    }
}

fn write_canonical(out: &mut String, n: &Node) {
    match &n.value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(&b.to_string()),
        Value::Int(i) => out.push_str(&i.to_string()),
        Value::Str(s) => write_string(out, s),
        Value::Seq(items) => {
            out.push('[');
            for (i, it) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_canonical(out, it);
            }
            out.push(']');
        }
        Value::Map(entries) => {
            let mut sorted: Vec<&Entry> = entries.iter().collect();
            sorted.sort_by(|x, y| x.key.as_bytes().cmp(y.key.as_bytes()));
            out.push('{');
            for (i, e) in sorted.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_string(out, &e.key);
                out.push(':');
                write_canonical(out, &e.value);
            }
            out.push('}');
        }
        // never reaches the output: the diff refuses an unsupported number before it compares anything
        Value::Unsupported(s) => out.push_str(s),
    }
}

/// A string as ADR 0008 fixes it: `\"` and `\\`, `\b \f \n \r \t`, the other controls below U+0020 as `\u00xx` in lowercase hex, everything
/// else literal (no `\/`, no escaped non-ASCII, U+007F literal).
fn write_string(out: &mut String, s: &str) {
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
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}
