//! Reading `spec.policy_surface` of a pinned schema (P3). The Rust peer of `go/policy.go`; the shared
//! cases are `testdata/p1/policy.json`.
//!
//! The compiler records what the schema declares and nothing more: no rule is evaluated, no requestor,
//! certificate, owner or snapshot is looked at, and no system default is written in. A member that is
//! not declared stays undeclared (`None`): that is the system layer of the resolution, never a
//! materialized `open` or `owner`. A member that is declared but invalid is a `SCHEMA_INVALID`
//! refusal that names the node: a policy the walk cannot read is never read as a more permissive one.

use crate::access::{read_mode, read_rule, Grammar, Mode, Rule};
use crate::node::{Node, Value};
use crate::schema::{get, problem, str_member, Problem, SCHEMA_INVALID};

/// The declared value of `default_access`, `default_modify` or a surface rule member.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PolicyRule {
    /// The symbol the schema wrote: `open` (`default_access`) or `owner` (`default_modify`).
    Symbol(&'static str),
    /// A list of CertPatterns. The empty list is an explicit "nobody".
    Patterns(Vec<String>),
}

/// One entry of `surface_rules`: finer than the object default, coarser than a field's Access.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SurfaceRule {
    pub surface: String,
    /// `None` when the entry declares no rule for that operation (absent or `inherit`).
    pub access: Option<PolicyRule>,
    pub modify: Option<PolicyRule>,
}

/// What a schema declares as the object-level access policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolicySurface {
    /// `spec.policy_surface` is present. An absent policy and `{}` are both legal and transparent:
    /// the chain goes on to the ancestors.
    pub declared: bool,
    /// `None` when not declared (the system layer).
    pub default_access: Option<PolicyRule>,
    pub default_modify: Option<PolicyRule>,
    /// In schema order.
    pub surface_rules: Vec<SurfaceRule>,
    /// The object-scope `InheritanceMode`; `Mode::Inherit` when not declared.
    pub inherit_policy: Mode,
    pub inherit_policy_declared: bool,
}

impl PolicySurface {
    fn undeclared() -> Self {
        PolicySurface {
            declared: false,
            default_access: None,
            default_modify: None,
            surface_rules: Vec::new(),
            inherit_policy: Mode::Inherit,
            inherit_policy_declared: false,
        }
    }
}

const POLICY_PATH: &str = "spec.policy_surface";

fn invalid(path: &str, detail: &str) -> Problem {
    problem(SCHEMA_INVALID, path, detail)
}

/// Reads `spec.policy_surface`. Every refusal is recorded and the walk goes on, so one compile names
/// all of them.
pub(crate) fn read_policy(
    spec: &Node,
    grammar: Grammar,
    problems: &mut Vec<Problem>,
) -> PolicySurface {
    let mut out = PolicySurface::undeclared();
    let n = match get(spec, "policy_surface") {
        Some(n) => n,
        None => return out,
    };
    out.declared = true;
    let entries = match &n.value {
        Value::Map(entries) => entries,
        _ => {
            problems.push(invalid(POLICY_PATH, "a mapping is required"));
            return out;
        }
    };
    for e in entries {
        let path = format!("{POLICY_PATH}.{}", e.key);
        match e.key.as_str() {
            "default_access" => {
                out.default_access = default_rule(&e.value, "open", &path, problems)
            }
            "default_modify" => {
                out.default_modify = default_rule(&e.value, "owner", &path, problems)
            }
            "surface_rules" => out.surface_rules = surface_rules(&e.value, &path, problems),
            "inherit_policy" => match read_mode(&e.value, grammar) {
                Ok(mode) => {
                    out.inherit_policy = mode;
                    out.inherit_policy_declared = true;
                }
                Err(v) => problems.push(invalid(&path, v.code)),
            },
            _ => problems.push(invalid(&path, "not a member of PolicySurface")),
        }
    }
    out
}

/// `default_access` (symbol `open`) or `default_modify` (symbol `owner`): a list of CertPatterns or
/// that one symbol. The other symbol and `inherit` are refused.
fn default_rule(
    n: &Node,
    symbol: &'static str,
    path: &str,
    problems: &mut Vec<Problem>,
) -> Option<PolicyRule> {
    match &n.value {
        Value::Str(s) if s == symbol => return Some(PolicyRule::Symbol(symbol)),
        _ => {}
    }
    match read_rule(n) {
        Err(v) => problems.push(invalid(path, v.code)),
        Ok(Rule::Symbol) => problems.push(invalid(
            path,
            &format!("the symbol inherit is not a value here: a list of CertPatterns or {symbol}"),
        )),
        Ok(Rule::List(patterns)) => return Some(PolicyRule::Patterns(patterns)),
    }
    None
}

fn surface_rules(n: &Node, path: &str, problems: &mut Vec<Problem>) -> Vec<SurfaceRule> {
    let items = match &n.value {
        Value::Seq(items) => items,
        _ => {
            problems.push(invalid(path, "a list is required"));
            return Vec::new();
        }
    };
    let mut rules = Vec::new();
    let mut declared: Vec<(String, &str)> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let ipath = format!("{path}[{i}]");
        let entries = match &item.value {
            Value::Map(entries) => entries,
            _ => {
                problems.push(invalid(&ipath, "a mapping is required"));
                continue;
            }
        };
        let mut rule = SurfaceRule {
            surface: str_member(item, "surface").to_string(),
            access: None,
            modify: None,
        };
        if rule.surface.is_empty() {
            problems.push(invalid(
                &format!("{ipath}.surface"),
                "a non-empty string is required",
            ));
        }
        for e in entries {
            let mpath = format!("{ipath}.{}", e.key);
            let op = match e.key.as_str() {
                "surface" => continue,
                "access" => "access",
                "modify" => "modify",
                _ => {
                    problems.push(invalid(&mpath, "not a member of SurfaceRule"));
                    continue;
                }
            };
            let patterns = match read_rule(&e.value) {
                Err(v) => {
                    problems.push(invalid(&mpath, v.code));
                    continue;
                }
                Ok(Rule::Symbol) => continue, // inherit declares no rule for this operation
                Ok(Rule::List(p)) => p,
            };
            if declared.iter().any(|(s, o)| *s == rule.surface && *o == op) {
                problems.push(invalid(
                    &mpath,
                    &format!(
                        "SURFACE_RULE_CONFLICT: {op} of {:?} is declared twice",
                        rule.surface
                    ),
                ));
                continue;
            }
            declared.push((rule.surface.clone(), op));
            let pr = Some(PolicyRule::Patterns(patterns));
            if op == "access" {
                rule.access = pr;
            } else {
                rule.modify = pr;
            }
        }
        rules.push(rule);
    }
    rules
}
