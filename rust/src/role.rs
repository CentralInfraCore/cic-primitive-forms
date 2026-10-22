//! Reading the Role of a schema node (cic-primitives `role.yaml` and `proposals/atom-grammar/README.md`, section 3;
//! ADR 0011). The Rust peer of `go/role.go`.
//!
//! Role is three orthogonal axes, not a flat list: authority (exactly one of `config`, `state`, `operational`;
//! `config` when not written), structural (zero or more of `key`, `reference`) and lifecycle (zero or one of
//! `derived`, `volatile`). A node is written in the short form (`role: config`, `key`, `state`, `operational`,
//! `derived`, `volatile`; `reference` has no short form) or in the long form (a mapping of the axes). Every other
//! value, and every other member, is an unknown Role: `SCHEMA_INVALID`, never read as `config`.

use crate::node::{Node, Value};
use crate::schema::{get, problem, Problem, SCHEMA_INVALID};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RoleAxes {
    authority: &'static str,
    structural: Vec<&'static str>,
    lifecycle: Option<&'static str>,
}

impl RoleAxes {
    /// ADR 0011: a schema default applies to an absent field if and only if the authority is `config` and the
    /// node has no structural and no lifecycle value. A lifecycle value excludes the authority `config` (the
    /// short forms fix it to `state`, `read_long_role` refuses the combination), so the authority covers it.
    pub(crate) fn takes_default(&self) -> bool {
        self.authority == "config" && self.structural.is_empty()
    }

    fn config() -> Self {
        RoleAxes {
            authority: "config",
            structural: Vec::new(),
            lifecycle: None,
        }
    }
}

const AUTHORITIES: [&str; 3] = ["config", "state", "operational"];
const STRUCTURALS: [&str; 2] = ["key", "reference"];
const LIFECYCLES: [&str; 2] = ["derived", "volatile"];

/// The short forms and what they stand for. `key` does not fix the authority: it adds the structural value and
/// the authority comes from the surrounding surface, which is `config` for the `config_surface` this slice reads.
fn short_role(s: &str) -> Option<RoleAxes> {
    let (authority, structural, lifecycle): (
        &'static str,
        Vec<&'static str>,
        Option<&'static str>,
    ) = match s {
        "config" => ("config", vec![], None),
        "state" => ("state", vec![], None),
        "operational" => ("operational", vec![], None),
        "key" => ("config", vec!["key"], None),
        "derived" => ("state", vec![], Some("derived")),
        "volatile" => ("state", vec![], Some("volatile")),
        _ => return None,
    };
    Some(RoleAxes {
        authority,
        structural,
        lifecycle,
    })
}

fn pick(list: &[&'static str], s: &str) -> Option<&'static str> {
    list.iter().find(|x| **x == s).copied()
}

/// Reads the `role` member of the node `n`; a node that does not write one is `config`.
pub(crate) fn read_role(n: &Node, path: &str) -> Result<RoleAxes, Problem> {
    let path = format!("{path}.role");
    let r = match get(n, "role") {
        Some(r) => r,
        None => return Ok(RoleAxes::config()),
    };
    match &r.value {
        Value::Str(s) => short_role(s).ok_or_else(|| {
            problem(
                SCHEMA_INVALID,
                &path,
                &format!(
                    "{s:?} is not a Role (reference has no short form and must be written out)"
                ),
            )
        }),
        Value::Map(_) => read_long_role(r, &path),
        _ => Err(problem(
            SCHEMA_INVALID,
            &path,
            "a Role is a string or a mapping of its axes",
        )),
    }
}

fn read_long_role(r: &Node, path: &str) -> Result<RoleAxes, Problem> {
    let mut out = RoleAxes::config();
    let entries: &[crate::node::Entry] = match &r.value {
        Value::Map(entries) => entries,
        _ => &[], // the caller matched a mapping
    };
    for e in entries {
        let p = format!("{path}.{}", e.key);
        let bad = |detail: &str| problem(SCHEMA_INVALID, &p, detail);
        match e.key.as_str() {
            "authority" => match &e.value.value {
                Value::Str(s) if pick(&AUTHORITIES, s).is_some() => {
                    out.authority = pick(&AUTHORITIES, s).unwrap_or("config");
                }
                _ => {
                    return Err(bad(
                        "authority is exactly one of config, state, operational",
                    ))
                }
            },
            "structural" => {
                let items = match &e.value.value {
                    Value::Seq(items) => items,
                    _ => return Err(bad("structural is a list of key and reference")),
                };
                for it in items {
                    let v = match &it.value {
                        Value::Str(s) => pick(&STRUCTURALS, s),
                        _ => None,
                    };
                    match v {
                        Some(v) if !out.structural.contains(&v) => out.structural.push(v),
                        _ => {
                            return Err(bad(
                                "structural lists key and reference, each at most once",
                            ))
                        }
                    }
                }
            }
            "lifecycle" => match &e.value.value {
                Value::Str(s) if pick(&LIFECYCLES, s).is_some() => {
                    out.lifecycle = pick(&LIFECYCLES, s);
                }
                _ => return Err(bad("lifecycle is derived or volatile")),
            },
            _ => return Err(bad("not an axis of Role")),
        }
    }
    // The combination rules of the atom grammar: derived and volatile exclude config, and a key is neither.
    if out.lifecycle.is_some() && out.authority == "config" {
        return Err(problem(
            SCHEMA_INVALID,
            path,
            "derived and volatile exclude the authority config",
        ));
    }
    if out.lifecycle.is_some() && out.structural.contains(&"key") {
        return Err(problem(
            SCHEMA_INVALID,
            path,
            "a key is neither derived nor volatile",
        ));
    }
    Ok(out)
}
