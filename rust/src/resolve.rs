//! The effective access / modify rule of a position (P3, fourth unit; `docs/P3-UNIT4-POLICY-RESOLUTION.md`). The Rust peer of
//! `go/resolve.go`.
//!
//! This is the port of the reference resolution of `access-policy/1` (cic-primitives D-018, `access_policy.py` `resolve`) up to, and
//! not including, its decision: the walk finds WHICH rule applies, in which layer and where it was declared, and stops. It knows no
//! requestor, certificate, owner or channel, and it says neither allow nor deny; the Relay decides, from this rule and a request
//! context. A rule that is not known is [`RuleKind::Unknown`], a barrier with no local rule is [`RuleKind::None`], and neither is ever
//! turned into a permission.
//!
//! The order is the reference's: the position's own list (`field`); the containing object's `surface_rules` (`surface_rule`) and
//! declared defaults (`object_default`); the rule a carrier passes down (`field_inherited`); the ancestors' PolicySurface
//! (`policy_inherited`); the documented system default (`system`), not after a reset barrier (`none`). The reference's layer names
//! `surface_rules` and `default` are `surface_rule` and `object_default` here.

use std::collections::{BTreeMap, BTreeSet};

use crate::access::Mode;
use crate::policy::{PolicyRule, PolicySurface};

/// The kinds of an effective rule: a list of CertPatterns (possibly empty: a terminal "nobody"), the symbols `open` and `owner`,
/// none (a reset barrier with no local rule) and unknown (the walk had to read something that is not known).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleKind {
    List,
    Open,
    Owner,
    None,
    Unknown,
}

pub const LAYER_FIELD: &str = "field";
pub const LAYER_SURFACE_RULE: &str = "surface_rule";
pub const LAYER_OBJECT_DEFAULT: &str = "object_default";
pub const LAYER_FIELD_INHERITED: &str = "field_inherited";
pub const LAYER_POLICY_INHERITED: &str = "policy_inherited";
pub const LAYER_SYSTEM: &str = "system";
pub const LAYER_NONE: &str = "none";

pub const REASON_RESET_NO_LOCAL_RULE: &str = "RESET_NO_LOCAL_RULE";
pub const REASON_POLICY_CONTEXT_UNKNOWN: &str = "POLICY_CONTEXT_UNKNOWN";
pub const REASON_CONTEXT_INCOMPLETE: &str = "CONTEXT_INCOMPLETE";

/// An effective rule is data, not a decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectiveRule {
    pub kind: RuleKind,
    /// The CertPatterns when `kind` is [`RuleKind::List`]; the empty list is an explicit "nobody".
    pub patterns: Vec<String>,
    /// Where the rule was found; [`LAYER_NONE`] for none and unknown.
    pub layer: &'static str,
    /// The logical path of what declared it: the target position (`field`), the carrier that passed it down (`field_inherited`), the
    /// declaring object (`surface_rule`, `object_default`, `policy_inherited`); `""` for system, none and unknown.
    pub source_path: String,
    /// Set for none and unknown, one of the `REASON_*` constants.
    pub reason: &'static str,
}

/// What is known about an object's PolicySurface.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ObjectPolicy {
    /// Known to have none: transparent.
    Absent,
    /// Not known: an error where the walk has to read it.
    Unknown,
    Declared(PolicySurface),
}

/// The Access of the parent field that holds an object: its `InheritanceMode` and the access and modify values it passes down
/// (`None` is the symbol `inherit`: no rule).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Carrier {
    pub mode: Mode,
    pub access: Option<PolicyRule>,
    pub modify: Option<PolicyRule>,
}

/// One node of the object graph a resolution walks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolicyObject {
    /// The logical path of the object (`$` for the root): the source path of what the object declares.
    pub id: String,
    /// The id of the containing object; `None` for the root.
    pub parent: Option<String>,
    pub policy: ObjectPolicy,
    /// `None` for an object that no field holds (the root).
    pub carrier: Option<Carrier>,
    /// The logical path of the position whose Access is the carrier (the source path of a `field_inherited` rule).
    pub carrier_path: String,
}

/// Maps an object id to the object.
pub type PolicyGraph = BTreeMap<String, PolicyObject>;

fn unknown_rule(reason: &'static str) -> EffectiveRule {
    EffectiveRule {
        kind: RuleKind::Unknown,
        patterns: Vec::new(),
        layer: LAYER_NONE,
        source_path: String::new(),
        reason,
    }
}

fn from_rule(r: &PolicyRule, layer: &'static str, source: &str) -> EffectiveRule {
    let (kind, patterns) = match r {
        PolicyRule::Symbol("open") => (RuleKind::Open, Vec::new()),
        PolicyRule::Symbol(_) => (RuleKind::Owner, Vec::new()),
        PolicyRule::Patterns(p) => (RuleKind::List, p.clone()),
    };
    EffectiveRule {
        kind,
        patterns,
        layer,
        source_path: source.to_string(),
        reason: "",
    }
}

fn is_modify(op: &str) -> bool {
    op == "modify"
}

/// Layers 2 and 3 of one object: its declared surface rule for the surface, then its declared default.
fn local_rule(o: &PolicyObject, surface: &str, op: &str) -> Option<EffectiveRule> {
    let p = match &o.policy {
        ObjectPolicy::Declared(p) => p,
        _ => return None,
    };
    for sr in &p.surface_rules {
        let r = if is_modify(op) {
            &sr.modify
        } else {
            &sr.access
        };
        if let (true, Some(r)) = (sr.surface == surface, r) {
            return Some(from_rule(r, LAYER_SURFACE_RULE, &o.id));
        }
    }
    let d = if is_modify(op) {
        &p.default_modify
    } else {
        &p.default_access
    };
    d.as_ref()
        .map(|d| from_rule(d, LAYER_OBJECT_DEFAULT, &o.id))
}

/// The object's own `inherit_policy` when it declares one.
fn policy_mode(o: &PolicyObject) -> Option<Mode> {
    match &o.policy {
        ObjectPolicy::Declared(p) if p.inherit_policy_declared => Some(p.inherit_policy),
        _ => None,
    }
}

/// Finds the effective rule of one operation (`"access"` or `"modify"`) for a field of an object of the graph. `field` is the field's
/// own value for the operation (`None` for the symbol `inherit`) and `field_path` the path of the position, for the source of a field
/// rule. Same input, same output: no clock, no I/O.
#[must_use]
pub fn resolve_rule(
    graph: &PolicyGraph,
    object: &str,
    surface: &str,
    field: Option<&PolicyRule>,
    field_path: &str,
    op: &str,
) -> EffectiveRule {
    let cur = match graph.get(object) {
        Some(cur) => cur,
        None => return unknown_rule(REASON_CONTEXT_INCOMPLETE),
    };
    // layer 1: the explicit rule of the target field itself
    if let Some(f) = field {
        return from_rule(f, LAYER_FIELD, field_path);
    }
    // layers 2-3: the object's own surface rules and defaults
    if cur.policy == ObjectPolicy::Unknown {
        return unknown_rule(REASON_POLICY_CONTEXT_UNKNOWN);
    }
    if let Some(hit) = local_rule(cur, surface, op) {
        return hit;
    }
    // layer 4a: the rule a carrier passes down (nearest explicit list wins); a no-inherit carrier stops the field rules above it, a
    // reset carrier stops them and the chain too
    let (mut barrier, mut field_open) = (false, true);
    let mut seen: BTreeSet<&str> = BTreeSet::from([object]);
    let mut o = cur;
    loop {
        if policy_mode(o) == Some(Mode::Reset) {
            barrier = true; // an object-level reset cuts what the object takes from above, the carrier's rule included
            break;
        }
        let carrier = match &o.carrier {
            Some(carrier) => carrier,
            None => break,
        };
        if o.policy == ObjectPolicy::Unknown && o.id != cur.id {
            return unknown_rule(REASON_POLICY_CONTEXT_UNKNOWN);
        }
        match carrier.mode {
            Mode::Reset => {
                barrier = true;
                break;
            }
            Mode::NoInherit => field_open = false,
            Mode::Inherit if field_open => {
                let r = if is_modify(op) {
                    &carrier.modify
                } else {
                    &carrier.access
                };
                if let Some(r) = r {
                    return from_rule(r, LAYER_FIELD_INHERITED, &o.carrier_path);
                }
            }
            Mode::Inherit => {}
        }
        match o.parent.as_deref().and_then(|p| graph.get(p)) {
            Some(next) if !seen.contains(next.id.as_str()) => {
                seen.insert(next.id.as_str());
                o = next;
            }
            _ => return unknown_rule(REASON_CONTEXT_INCOMPLETE),
        }
    }
    // layer 4b: the ancestors' PolicySurface, only where each step permits it
    let mut seen: BTreeSet<&str> = BTreeSet::from([object]);
    let mut o = cur;
    loop {
        match policy_mode(o) {
            Some(Mode::Reset) => {
                barrier = true;
                break;
            }
            Some(Mode::NoInherit) => break,
            _ => {}
        }
        if o.carrier.as_ref().is_some_and(|c| c.mode == Mode::Reset) {
            barrier = true;
            break;
        }
        let pid = match &o.parent {
            Some(pid) => pid,
            None => break,
        };
        match graph.get(pid) {
            Some(next) if !seen.contains(next.id.as_str()) => {
                seen.insert(next.id.as_str());
                o = next;
            }
            _ => return unknown_rule(REASON_CONTEXT_INCOMPLETE),
        }
        if o.policy == ObjectPolicy::Unknown {
            return unknown_rule(REASON_POLICY_CONTEXT_UNKNOWN);
        }
        if let Some(mut hit) = local_rule(o, surface, op) {
            hit.layer = LAYER_POLICY_INHERITED;
            return hit;
        }
    }
    // layer 5: the documented system default, never after a reset barrier
    if barrier {
        return EffectiveRule {
            kind: RuleKind::None,
            patterns: Vec::new(),
            layer: LAYER_NONE,
            source_path: String::new(),
            reason: REASON_RESET_NO_LOCAL_RULE,
        };
    }
    EffectiveRule {
        kind: if is_modify(op) {
            RuleKind::Owner
        } else {
            RuleKind::Open
        },
        patterns: Vec::new(),
        layer: LAYER_SYSTEM,
        source_path: String::new(),
        reason: "",
    }
}
