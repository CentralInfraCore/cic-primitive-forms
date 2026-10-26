//! `patch` (P4, step P4-2; ADR 0013 and docs/P4-DIFF-PATCH.md): the first unit of applying a diff to a document, text in and text out.
//!
//! It applies only `value_change` events on existing primitive scalars of A, each as [`edit_scalar`] (ADR 0012), so every refusal of that operation keeps its
//! code and its phase. Everything else in a diff is refused, as a whole, with `PATCH_UNSUPPORTED_EVENT`. The checks come in the fixed order of ADR 0013,
//! section 3, all of rows 1-6 before the first byte changes; on any error no text is returned. A patch is not an authorization: it does not know who may apply
//! a diff, and an empty diff is neither evidence of provenance nor of authorization.
//!
//! What it verifies is A under the schema, that the context matches the schema it was given, that the version of the diff is 1, that every path resolves, that
//! the position is there and holds `previous` (type-strict). What it takes from the caller, on trust, is that the diff was made under that context: the bytes of a
//! diff carry none (ADR 0008 is not changed). The Go peer is go/patch.go.

use crate::access::Grammar;
use crate::diff::{DiffContext, DiffEvent, EventKind};
use crate::editscalar::{edit_scalar, refuse_invalid};
use crate::error::{Error, Location, Phase, Violation};
use crate::listitem::{Case, Elements};
use crate::node::{Node, Pos};
use crate::parse::parse;
use crate::pathtext::{parse_path, resolve_path, PathSeg};
use crate::sourcespan::{locate, path_failure, source_span, SourceSpan};
use crate::validate::{Shape, ShapeType};

/// The code of the context error of the phase `patch` (ADR 0013).
pub const PATCH_CONTEXT_MISMATCH: &str = "PATCH_CONTEXT_MISMATCH";
/// The code for an event or a target the first unit does not apply.
pub const PATCH_UNSUPPORTED_EVENT: &str = "PATCH_UNSUPPORTED_EVENT";
/// The code for a position the schema has and the document does not.
pub const PATCH_TARGET_ABSENT: &str = "PATCH_TARGET_ABSENT";
/// The code for a value the document holds that is not the `previous` of the event.
pub const PATCH_CONFLICT: &str = "PATCH_CONFLICT";

/// One event of a diff as the patch reads it: its kind, its path, and the typed values of a value change.
#[derive(Debug, Clone)]
pub struct PatchEvent {
    pub kind: EventKind,
    pub path: String,
    pub previous: Option<Node>,
    pub next: Option<Node>,
}

impl From<&DiffEvent> for PatchEvent {
    fn from(e: &DiffEvent) -> Self {
        PatchEvent {
            kind: e.kind,
            path: e.path.clone(),
            previous: e.previous.clone(),
            next: e.next.clone(),
        }
    }
}

/// A diff as the patch reads it: the version of its canonical bytes and its events in the canonical order of ADR 0008. Representation entries are not
/// events and are not passed.
#[derive(Debug, Clone)]
pub struct PatchDiff {
    pub version: i64,
    pub changes: Vec<PatchEvent>,
}

/// Where a replaced scalar was in A and where it is in the result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PatchSpan {
    pub path: String,
    pub before: SourceSpan,
    pub after: SourceSpan,
}

/// A successful patch: the new text, the paths of the scalars it replaced in the order of the diff, and their spans.
#[derive(Debug, Clone)]
pub struct PatchResult {
    pub text: String,
    pub replaced: Vec<String>,
    pub spans: Vec<PatchSpan>,
}

fn failure(code: &'static str, path: &str, at: Option<Pos>) -> Error {
    // (outside the macro: the branch tool does not enumerate an `if` inside a macro invocation)
    let logical_path = if path.is_empty() {
        None
    } else {
        Some(path.to_string())
    };
    Error::Contract(vec![Violation {
        code,
        phase: Phase::Patch,
        location: at.map_or(Location::NotApplicable, Location::At),
        logical_path,
        path_offset: None,
        operand: None,
    }])
}

/// Whether `segs`, the path of a scalar position, ends at a key field of a list element: the element selector is the segment before the last one, and the
/// last one names a field of the key of the case it selects.
fn is_key_component(root: &Shape, segs: &[PathSeg]) -> bool {
    let (last, prefix) = match segs.split_last() {
        Some((PathSeg::Field(name), prefix)) => (name, prefix),
        _ => return false,
    };
    let mut fields: &[(String, Shape)] = match &root.shape_type {
        ShapeType::Composite(f) => f,
        _ => &[],
    };
    let mut elements: Option<&Elements> = None;
    let mut case: Option<&Case> = None;
    for s in prefix {
        case = None;
        match s {
            PathSeg::Field(name) => {
                if let Some((_, shape)) = fields.iter().find(|(f, _)| f == name) {
                    match &shape.shape_type {
                        ShapeType::Composite(f) => fields = f,
                        ShapeType::DeclaredList { elements: e, .. } => elements = Some(e),
                        _ => {}
                    }
                }
            }
            PathSeg::Item(comps) => {
                let names: Vec<&String> = comps.iter().map(|c| &c.name).collect();
                case = elements.and_then(|e| {
                    e.cases
                        .iter()
                        .find(|c| c.key.iter().collect::<Vec<_>>() == names)
                });
                if let Some(c) = case {
                    fields = &c.fields;
                }
            }
        }
    }
    case.is_some_and(|c| c.key.contains(last))
}

/// Applies `d` to `text`. `schema` is the context of the schema the caller passes `root` for, `context` the one the caller says `d` was made under; they must be
/// equal (`PATCH_CONTEXT_MISMATCH`), which is all that can be verified of it. `a` is the expanded key set and `g` the bound grammar, as everywhere.
///
/// # Errors
/// The checks of ADR 0013, section 3, in that order, one error per response; no text comes with an error.
pub fn patch(
    text: &str,
    root: &Shape,
    a: &[String],
    _g: Grammar,
    schema: &DiffContext,
    context: &DiffContext,
    d: &PatchDiff,
) -> Result<PatchResult, Error> {
    let doc = parse(text)?;
    refuse_invalid(&doc, root, a)?;
    if schema != context || d.version != 1 {
        return Err(failure(PATCH_CONTEXT_MISMATCH, "", None));
    }
    let mut parsed = Vec::with_capacity(d.changes.len());
    let mut nexts: Vec<&Node> = Vec::with_capacity(d.changes.len());
    let mut unresolved = Vec::new();
    for e in &d.changes {
        // (a value change that does not carry both values is not the event section 2 describes)
        match (e.kind, &e.previous, &e.next) {
            (EventKind::ValueChange, Some(_), Some(next)) => nexts.push(next),
            _ => return Err(failure(PATCH_UNSUPPORTED_EVENT, &e.path, None)),
        }
        match parse_path(&e.path).and_then(|segs| resolve_path(root, &segs).map(|r| (segs, r))) {
            Ok((segs, res)) => {
                if res.target != "scalar" || is_key_component(root, &segs) {
                    return Err(failure(PATCH_UNSUPPORTED_EVENT, &e.path, None));
                }
                parsed.push(segs);
            }
            Err(perr) => {
                unresolved.push(perr);
                parsed.push(Vec::new());
            }
        }
    }
    if let Some(perr) = unresolved.into_iter().next() {
        return Err(path_failure(perr));
    }
    for (e, segs) in d.changes.iter().zip(&parsed) {
        let found = locate(&doc, root, segs, a);
        let value = match found.value.filter(|v| v.pos.is_some()) {
            Some(v) => v,
            None => return Err(failure(PATCH_TARGET_ABSENT, &e.path, found.parent.pos)),
        };
        if !e
            .previous
            .as_ref()
            .is_some_and(|previous| value.same(previous))
        {
            return Err(failure(PATCH_CONFLICT, &e.path, value.pos));
        }
    }
    let mut out = text.to_string();
    let mut replaced = Vec::with_capacity(d.changes.len());
    for (e, next) in d.changes.iter().zip(&nexts) {
        out = edit_scalar(&out, root, &e.path, a, next)?.text;
        replaced.push(e.path.clone());
    }
    let mut spans = Vec::with_capacity(replaced.len());
    for p in &replaced {
        spans.push(PatchSpan {
            path: p.clone(),
            before: source_span(text, root, p, a)?,
            after: source_span(&out, root, p, a)?,
        });
    }
    Ok(PatchResult {
        text: out,
        replaced,
        spans,
    })
}
