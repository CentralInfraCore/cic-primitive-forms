//! Expand and collapse (ADR 0001 "Round trip", ADR 0003 rules 5-6, ADR 0010), recursive over a compiled schema. The
//! Rust peer of `go/expand.go`; the shared cases are `testdata/p1/expand.json` and the P2 kinds of the corpus.
//!
//! Every primitive position (ADR 0002) is Access-wrapped by [`expand`]: a short position gets the symbolic defaults
//! (`access: inherit`, `modify: inherit`, `inherit` in the spelling of the bound grammar, `default_injection: null`,
//! `conformance: implemented`) and its value; an expanded position stays as authored. Nothing is resolved: the symbol
//! `inherit` stays the symbol (P3 resolves it), and an explicit policy setting is never touched. The recursion
//! follows the schema, never the YAML shape: into the fields of a composite and into the fields of a list element,
//! not into a set, an opaque value or a scalar. A list element is a record, not a position.
//!
//! [`collapse`] is the inverse under the law of ADR 0001: a position becomes short only if every policy field equals
//! the symbolic default, it was not authored expanded, its conformance basis is not `binding_attested` and the short
//! form would be recognized as short. What [`expand`] learned about the authored form is kept in a [`Provenance`],
//! keyed by the canonical logical path, so `collapse(expand(x))` gives back the meaning of `x`. The input is never
//! modified.
//!
//! Both expect a document that validates and fail closed otherwise: a key the schema does not declare is
//! `UNKNOWN_FIELD` (the first in document order), and a structure the schema does not allow is an
//! [`Error::Unsupported`].

use std::collections::BTreeMap;

use crate::access::{write_mode, Grammar, Mode, INHERIT_SYMBOL};
use crate::error::{Error, Location, Phase, Violation};
use crate::listitem::identify;
use crate::node::{Entry, Node, Value};
use crate::pathtext::{parse_path, resolve_path};
use crate::recognize::{classify, location, logical_path, Class, Path, Seg};
use crate::validate::{Shape, ShapeType};

/// The conformance basis (ADR 0010) that is never shortened: the value is binding data.
pub const BASIS_BINDING: &str = "binding_attested";

/// The origin of a document (ADR 0010).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DocOrigin {
    #[default]
    Authored,
    Materialized,
}

/// The code of the context error of ADR 0010: the origin and the attestation contradict each other.
pub const ORIGIN_ATTESTATION_MISMATCH: &str = "ORIGIN_ATTESTATION_MISMATCH";

/// The trusted context a document is processed with (ADR 0010): supplied by the caller, never read from the document, and
/// not interpreted or verified here. The default is an authored document. The entries of `attested_positions` are
/// resolved against the schema: each must name a primitive position of it, or the operation fails with the path error (or
/// `ORIGIN_ATTESTATION_MISMATCH` for the root and a list element) and nothing is expanded or collapsed.
///
/// `attestation` and `attested_positions` are present exactly when the origin is materialized (`Some(vec![])` is present
/// and empty: an attestation that grants nothing). A position of a materialized document that is not in
/// `attested_positions` has the basis default; one that is has `binding_attested` and is never shortened. For an authored
/// document the absence of a basis is allowed and is not an attestation. The entries are exact canonical logical paths.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Context {
    pub origin: DocOrigin,
    pub attestation: Option<String>,
    pub attested_positions: Option<Vec<String>>,
}

impl Context {
    fn check(&self, root: &Shape) -> Result<(), Error> {
        let materialized = self.origin == DocOrigin::Materialized;
        let consistent = self.attestation.as_ref().is_some_and(|a| !a.is_empty()) == materialized
            && self.attested_positions.is_some() == materialized;
        if !consistent {
            return Err(mismatch());
        }
        let positions = self.attested_positions.iter().flatten();
        for (i, p) in positions.clone().enumerate() {
            if positions.clone().take(i).any(|q| q == p) {
                return Err(mismatch());
            }
            // An entry must name a primitive position of the schema (ADR 0010): a binding is never credited with a position
            // that does not exist. A text that is not a canonical path, or that the schema cannot resolve, fails with the
            // path error; the root and a list element resolve but are not positions.
            let segs = parse_path(p).map_err(|e| Error::Contract(vec![e.violation()]))?;
            let resolved =
                resolve_path(root, &segs).map_err(|e| Error::Contract(vec![e.violation()]))?;
            if !resolved.primitive {
                return Err(mismatch());
            }
        }
        Ok(())
    }
}

/// The context error: no YAML node is at fault, so its source location is `not_applicable` (ADR 0010) and it has no
/// logical path.
fn mismatch() -> Error {
    Error::Contract(vec![Violation {
        code: ORIGIN_ATTESTATION_MISMATCH,
        phase: Phase::Validate,
        location: Location::NotApplicable,
        logical_path: None,
        path_offset: None,
    }])
}

/// How a primitive position of the authored document was written.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Origin {
    /// Written in expanded form: the position stays expanded, even when it equals the defaults.
    pub authored_expanded: bool,
}

/// The origin of each primitive position, keyed by its canonical logical path (ADR 0006).
pub type Provenance = BTreeMap<String, Origin>;

fn defaults(g: Grammar) -> Vec<(&'static str, Node)> {
    vec![
        ("access", Node::new(Value::Str(INHERIT_SYMBOL.into()))),
        ("modify", Node::new(Value::Str(INHERIT_SYMBOL.into()))),
        ("inherit", write_mode(Mode::Inherit, g)),
        ("default_injection", Node::new(Value::Null)),
        ("conformance", Node::new(Value::Str("implemented".into()))),
    ]
}

fn entry<'a>(n: &'a Node, key: &str) -> Option<&'a Node> {
    match &n.value {
        Value::Map(es) => es.iter().find(|e| e.key == key).map(|e| &e.value),
        _ => None,
    }
}

/// Expands one primitive position: a short node is wrapped with the symbolic defaults, an expanded node is returned as
/// authored (the flag says which it was). A node with all of `A` and more is `EXPANDED_WITH_EXTRA_KEY`.
///
/// # Errors
/// [`Error::Contract`] for that recognition error, [`Error::Unsupported`] for an `A` with a key that has no default.
pub fn expand_position(n: &Node, a: &[String], g: Grammar) -> Result<(Node, bool), Error> {
    match classify(n, a) {
        Err(v) => return Err(Error::Contract(vec![v])),
        Ok(Class::Expanded) => return Ok((n.clone(), true)),
        Ok(Class::Short | Class::Error) => {}
    }
    let defaults = defaults(g);
    let mut entries = Vec::with_capacity(a.len());
    for k in a {
        let value = if k == "value" {
            n.clone()
        } else if let Some((_, d)) = defaults.iter().find(|(name, _)| name == k) {
            d.clone()
        } else {
            return Err(Error::Unsupported(format!(
                "the expanded key set holds {k:?}, which has no symbolic default"
            )));
        };
        entries.push(Entry {
            key: k.clone(),
            key_pos: None,
            value,
        });
    }
    Ok((Node::new(Value::Map(entries)), false))
}

/// Shortens one expanded position when the law of ADR 0001 allows it, and otherwise returns it as it is. A node that is
/// not expanded is returned unchanged.
#[must_use]
pub fn collapse_position(
    n: &Node,
    authored_expanded: bool,
    basis: &str,
    a: &[String],
    g: Grammar,
) -> Node {
    if !matches!(classify(n, a), Ok(Class::Expanded)) || authored_expanded || basis == BASIS_BINDING
    {
        return n.clone();
    }
    for (k, d) in defaults(g) {
        if !entry(n, k).is_some_and(|got| got.same(&d)) {
            return n.clone();
        }
    }
    match entry(n, "value") {
        Some(value) if matches!(classify(value, a), Ok(Class::Short)) => value.clone(),
        _ => n.clone(),
    }
}

struct Walker<'a> {
    a: &'a [String],
    g: Grammar,
    prov: Provenance,
    attested: Vec<String>,
}

fn path_text(path: &Path) -> Result<String, Error> {
    logical_path(path).ok_or_else(|| {
        Error::Unsupported("a primitive position whose logical path cannot be written".into())
    })
}

/// Wraps every primitive position of `doc`, a mapping read against `shape` (a composite), and returns the expanded
/// document and the provenance of each position. Expansion creates no authority (ADR 0010): `ctx` is checked for
/// consistency and otherwise does not change the result.
///
/// # Errors
/// The recognition error of [`expand_position`] with its logical path, or [`Error::Unsupported`] for a structure the
/// schema does not allow (validate the document first).
pub fn expand(
    doc: &Node,
    shape: &Shape,
    a: &[String],
    g: Grammar,
    ctx: &Context,
) -> Result<(Node, Provenance), Error> {
    ctx.check(shape)?;
    let mut w = Walker {
        a,
        g,
        prov: Provenance::new(),
        attested: Vec::new(),
    };
    let out = w.record(doc, shape, &mut Vec::new(), true)?;
    Ok((out, w.prov))
}

/// The inverse of [`expand`] under the law of ADR 0001. `prov` says which positions were authored expanded (a position
/// it does not name is not). `ctx` is the trusted context of ADR 0010: the conformance basis of a position follows it,
/// never the writing, and a position in `ctx.attested_positions` is never shortened.
///
/// # Errors
/// `ORIGIN_ATTESTATION_MISMATCH` ([`Error::Contract`]) for a contradictory context, nothing is collapsed;
/// [`Error::Unsupported`] for a structure the schema does not allow.
pub fn collapse(
    doc: &Node,
    shape: &Shape,
    a: &[String],
    g: Grammar,
    prov: &Provenance,
    ctx: &Context,
) -> Result<Node, Error> {
    ctx.check(shape)?;
    let mut w = Walker {
        a,
        g,
        prov: prov.clone(),
        attested: ctx.attested_positions.clone().unwrap_or_default(),
    };
    w.record(doc, shape, &mut Vec::new(), false)
}

impl Walker<'_> {
    fn record(
        &mut self,
        n: &Node,
        shape: &Shape,
        path: &mut Path,
        expand: bool,
    ) -> Result<Node, Error> {
        match &shape.shape_type {
            ShapeType::Composite(fields) => self.fields(n, fields, path, expand),
            _ => Err(Error::Unsupported(
                "the root of a document is a composite".into(),
            )),
        }
    }

    fn fields(
        &mut self,
        n: &Node,
        fields: &[(String, Shape)],
        path: &mut Path,
        expand: bool,
    ) -> Result<Node, Error> {
        let entries = match &n.value {
            Value::Map(entries) => entries,
            _ => {
                return Err(Error::Unsupported(
                    "a value of another kind where a mapping is declared".to_string(),
                ))
            }
        };
        let mut out = Vec::with_capacity(entries.len());
        for e in entries {
            let value = match fields.iter().find(|(name, _)| *name == e.key) {
                // An undeclared key is not an intent: nothing validated or normalized can be made of it. The raw
                // document stays the caller's own input; a lossless pass-through would be a different, explicitly
                // unvalidated mode.
                None => {
                    let mut p = path.clone();
                    p.push(Seg::Field(e.key.clone()));
                    return Err(Error::Contract(vec![Violation {
                        code: "UNKNOWN_FIELD",
                        phase: Phase::Validate,
                        location: location(e.key_pos),
                        logical_path: logical_path(&p),
                        path_offset: None,
                    }]));
                }
                Some((_, shape)) => {
                    path.push(Seg::Field(e.key.clone()));
                    let v = self.position(&e.value, shape, path, expand);
                    path.pop();
                    v?
                }
            };
            out.push(Entry {
                key: e.key.clone(),
                key_pos: e.key_pos,
                value,
            });
        }
        Ok(Node {
            value: Value::Map(out),
            pos: n.pos,
        })
    }

    /// Expands or collapses one primitive position and what is below it.
    fn position(
        &mut self,
        n: &Node,
        shape: &Shape,
        path: &mut Path,
        expand: bool,
    ) -> Result<Node, Error> {
        let text = path_text(path)?;
        if expand {
            let (wrapper, authored) = expand_position(n, self.a, self.g).map_err(|e| match e {
                Error::Contract(mut vs) => {
                    for v in &mut vs {
                        v.logical_path = logical_path(path);
                    }
                    Error::Contract(vs)
                }
                other => other,
            })?;
            self.prov.insert(
                text,
                Origin {
                    authored_expanded: authored,
                },
            );
            return self.rewrap(wrapper, shape, path, true);
        }
        if !matches!(classify(n, self.a), Ok(Class::Expanded)) {
            return self.below(n, shape, path, false);
        }
        let inner = self.rewrap(n.clone(), shape, path, false)?;
        let basis = if self.attested.contains(&text) {
            BASIS_BINDING
        } else {
            ""
        };
        let authored = self.prov.get(&text).is_some_and(|o| o.authored_expanded);
        Ok(collapse_position(&inner, authored, basis, self.a, self.g))
    }

    /// Replaces the value of an expanded wrapper by the value processed below it.
    fn rewrap(
        &mut self,
        mut wrapper: Node,
        shape: &Shape,
        path: &mut Path,
        expand: bool,
    ) -> Result<Node, Error> {
        let mut result = Ok(());
        if let Value::Map(entries) = &mut wrapper.value {
            for e in entries.iter_mut().filter(|e| e.key == "value") {
                match self.below(&e.value, shape, path, expand) {
                    Ok(v) => e.value = v,
                    Err(err) => result = Err(err),
                }
            }
        }
        result.map(|()| wrapper)
    }

    /// What a position holds: the child positions of a composite, the fields of the elements of a list. A scalar, a set
    /// and an opaque value are not recursed.
    fn below(
        &mut self,
        v: &Node,
        shape: &Shape,
        path: &mut Path,
        expand: bool,
    ) -> Result<Node, Error> {
        if matches!(v.value, Value::Null) {
            return Ok(v.clone());
        }
        match &shape.shape_type {
            ShapeType::Composite(fields) => self.fields(v, fields, path, expand),
            ShapeType::DeclaredList { elements, .. } => {
                let items = match &v.value {
                    Value::Seq(items) => items,
                    _ => {
                        return Err(Error::Unsupported(
                            "a value of another kind where a list is declared".to_string(),
                        ))
                    }
                };
                let mut out = Vec::with_capacity(items.len());
                for it in items {
                    if !matches!(it.value, Value::Map(_)) {
                        return Err(Error::Unsupported(
                            "a value of another kind where a list element record is declared"
                                .to_string(),
                        ));
                    }
                    let (case, id) = identify(it, elements, self.a).map_err(|(code, _)| {
                        Error::Unsupported(format!(
                            "a list element without an identity ({code}): validate the document first"
                        ))
                    })?;
                    path.push(Seg::Item(id));
                    let el = self.fields(it, &case.fields, path, expand);
                    path.pop();
                    out.push(el?);
                }
                Ok(Node {
                    value: Value::Seq(out),
                    pos: v.pos,
                })
            }
            _ => Ok(v.clone()),
        }
    }
}
