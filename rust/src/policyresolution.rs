//! `PolicyResolution` (P3, fourth unit; `docs/P3-UNIT4-POLICY-RESOLUTION.md`): the effective access and modify rule of every position
//! of a normalized object, found by walking the object together with the compiled schema. The Rust peer of
//! `go/policyresolution.go`.
//!
//! The positions are the ones that exist in the object: nothing is created to be resolved, and the origin map of the normalization is
//! neither read nor changed. The objects of the graph are the root, the value of a composite position and every list element (named
//! by its declared identity); the carrier of an object is the Access wrapper of the position that holds it (for a list element, the
//! wrapper of the list field). The only PolicySurface a compiled schema has is the root's. The result is data: [`resolve_rule`] finds
//! the rule, and the decision (a requestor, a certificate, an owner) is the Relay's.

use std::collections::BTreeMap;

use crate::access::{read_mode, read_rule, Grammar, Mode, Rule};
use crate::error::Error;
use crate::expand::path_text;
use crate::listitem::identify;
use crate::node::{Node, Value};
use crate::policy::{PolicyRule, PolicySurface};
use crate::recognize::{classify, Class, Path, Seg};
use crate::resolve::{
    resolve_rule, Carrier, EffectiveRule, ObjectPolicy, PolicyGraph, PolicyObject,
};
use crate::validate::{Shape, ShapeType};

/// The effective rule of one position for both operations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolicyEntry {
    pub access: EffectiveRule,
    pub modify: EffectiveRule,
}

/// Keyed by the canonical logical path of the position.
pub type PolicyResolution = BTreeMap<String, PolicyEntry>;

struct Target {
    path: String,
    object: String,
    access: Option<PolicyRule>,
    modify: Option<PolicyRule>,
}

struct Walker<'a> {
    a: &'a [String],
    g: Grammar,
    graph: PolicyGraph,
    targets: Vec<Target>,
}

fn unsupported(detail: String) -> Error {
    Error::Unsupported(detail)
}

/// Resolves every position of `object`, an expanded document (the object of a `NormalizationResult`) read against `root`. `policy` is
/// the compiled schema's PolicySurface and `surface` the surface it was compiled for. `unknown` names, by logical path, the objects
/// whose policy the caller does not know (`$` is the root): a rule that has to read one of them is unknown. An object that is not
/// expanded, a structure the schema does not allow, or an unknown path that names no object is an [`Error::Unsupported`].
///
/// # Errors
/// [`Error::Unsupported`] as above.
pub fn resolve_policy(
    object: &Node,
    root: &Shape,
    policy: &PolicySurface,
    surface: &str,
    a: &[String],
    g: Grammar,
    unknown: &[String],
) -> Result<PolicyResolution, Error> {
    let mut w = Walker {
        a,
        g,
        graph: PolicyGraph::new(),
        targets: Vec::new(),
    };
    let state = if policy.declared {
        ObjectPolicy::Declared(policy.clone())
    } else {
        ObjectPolicy::Absent
    };
    w.graph.insert(
        "$".to_string(),
        PolicyObject {
            id: "$".to_string(),
            parent: None,
            policy: state,
            carrier: None,
            carrier_path: String::new(),
        },
    );
    let fields = match &root.shape_type {
        ShapeType::Composite(fields) => fields,
        _ => return Err(unsupported("the root of a document is a composite".into())),
    };
    w.object(object, fields, &mut Vec::new(), "$")?;
    for u in unknown {
        match w.graph.get_mut(u) {
            Some(o) => o.policy = ObjectPolicy::Unknown,
            None => {
                return Err(unsupported(format!(
                    "{u:?} is marked as an object whose policy is unknown, but the object has no such object"
                )))
            }
        }
    }
    Ok(w.targets
        .iter()
        .map(|t| {
            (
                t.path.clone(),
                PolicyEntry {
                    access: resolve_rule(
                        &w.graph,
                        &t.object,
                        surface,
                        t.access.as_ref(),
                        &t.path,
                        "access",
                    ),
                    modify: resolve_rule(
                        &w.graph,
                        &t.object,
                        surface,
                        t.modify.as_ref(),
                        &t.path,
                        "modify",
                    ),
                },
            )
        })
        .collect())
}

impl Walker<'_> {
    /// Walks the mapping `n` of the object `oid`: each entry is an expanded position.
    fn object(
        &mut self,
        n: &Node,
        fields: &[(String, Shape)],
        path: &mut Path,
        oid: &str,
    ) -> Result<(), Error> {
        let entries = match &n.value {
            Value::Map(entries) => entries,
            _ => {
                return Err(unsupported(
                    "a value of another kind where a mapping is declared".into(),
                ))
            }
        };
        for e in entries {
            let shape = match fields.iter().find(|(name, _)| *name == e.key) {
                Some((_, s)) => s,
                None => {
                    return Err(unsupported(format!(
                        "{:?} is not a field the schema declares",
                        e.key
                    )))
                }
            };
            path.push(Seg::Field(e.key.clone()));
            let text = path_text(path)?;
            if !matches!(classify(&e.value, self.a), Ok(Class::Expanded)) {
                return Err(unsupported(format!(
                    "{text} is not an expanded position: normalize the document first"
                )));
            }
            let (mode, access, modify) = self.wrapper(&e.value, &text)?;
            self.targets.push(Target {
                path: text.clone(),
                object: oid.to_string(),
                access: access.clone(),
                modify: modify.clone(),
            });
            let value = match &e.value.value {
                Value::Map(es) => es.iter().find(|x| x.key == "value").map(|x| &x.value),
                _ => None,
            };
            let r = match value {
                Some(v) => self.below(v, shape, path, &text, oid, mode, access, modify),
                None => Ok(()),
            };
            path.pop();
            r?;
        }
        Ok(())
    }

    /// Reads the Access wrapper of a position: the mode and the two rule values (`None` is the symbol `inherit`).
    fn wrapper(
        &self,
        x: &Node,
        text: &str,
    ) -> Result<(Mode, Option<PolicyRule>, Option<PolicyRule>), Error> {
        let field = |key: &str| -> Result<Option<PolicyRule>, Error> {
            let n = crate::expand::entry(x, key)
                .ok_or_else(|| unsupported(format!("{text}: the position has no {key}")))?;
            match read_rule(n) {
                Ok(Rule::Symbol) => Ok(None),
                Ok(Rule::List(p)) => Ok(Some(PolicyRule::Patterns(p))),
                Err(v) => Err(unsupported(format!(
                    "{text}: {key} of the position: {}",
                    v.code
                ))),
            }
        };
        let access = field("access")?;
        let modify = field("modify")?;
        let inherit = crate::expand::entry(x, "inherit")
            .ok_or_else(|| unsupported(format!("{text}: the position has no inherit")))?;
        let mode = read_mode(inherit, self.g)
            .map_err(|v| unsupported(format!("{text}: inherit of the position: {}", v.code)))?;
        Ok((mode, access, modify))
    }

    /// Goes into what a position holds: the object of a composite, the elements of a declared list.
    #[allow(clippy::too_many_arguments)]
    fn below(
        &mut self,
        v: &Node,
        shape: &Shape,
        path: &mut Path,
        text: &str,
        parent: &str,
        mode: Mode,
        access: Option<PolicyRule>,
        modify: Option<PolicyRule>,
    ) -> Result<(), Error> {
        if matches!(v.value, Value::Null) {
            return Ok(());
        }
        match &shape.shape_type {
            ShapeType::Composite(fields) => {
                self.child(text, parent, mode, &access, &modify, text);
                self.object(v, fields, path, text)
            }
            ShapeType::DeclaredList { elements, .. } => {
                let items = match &v.value {
                    Value::Seq(items) => items,
                    _ => return Err(unsupported(format!("{text}: a value that is not a list"))),
                };
                for rec in items {
                    if !matches!(rec.value, Value::Map(_)) {
                        return Err(unsupported(format!(
                            "{text}: a list element that is not a record"
                        )));
                    }
                    let (case, id) = identify(rec, elements, self.a).map_err(|(code, _)| {
                        unsupported(format!(
                            "{text}: a list element without an identity ({code}): validate the document first"
                        ))
                    })?;
                    path.push(Seg::Item(id));
                    let etext = path_text(path)?;
                    // an element inherits from its list field and its object, never from a sibling
                    self.child(&etext, parent, mode, &access, &modify, text);
                    let r = self.object(rec, &case.fields, path, &etext);
                    path.pop();
                    r?;
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }

    fn child(
        &mut self,
        id: &str,
        parent: &str,
        mode: Mode,
        access: &Option<PolicyRule>,
        modify: &Option<PolicyRule>,
        carrier_path: &str,
    ) {
        self.graph.insert(
            id.to_string(),
            PolicyObject {
                id: id.to_string(),
                parent: Some(parent.to_string()),
                policy: ObjectPolicy::Absent,
                carrier: Some(Carrier {
                    mode,
                    access: access.clone(),
                    modify: modify.clone(),
                }),
                carrier_path: carrier_path.to_string(),
            },
        );
    }
}
