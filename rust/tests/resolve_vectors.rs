//! The golden vectors of the resolution of access-policy/1 (cic-primitives, `access-vectors/resolution.yaml`, in the grammar pin) as the
//! cross-check of `resolve_rule`. The Go peer is `go/resolve_vectors_test.go`.
//!
//! The vectors are request-bound: each ends in allow, deny or error for a requestor. The library decides nothing, so this test carries a
//! TEST-ONLY port of the reference decision (`_decide`, `check_request`): it applies the vector's request to the effective rule
//! `resolve_rule` found, and the outcome, the code and the layer must equal the vector's expectation. The Python reference is never
//! executed: only the bytes of the vector file are read, after their SHA-256 matched the pin.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, parse, read_mode, read_policy_surface, read_rule, resolve_rule, Carrier,
    EffectiveRule, Grammar, Mode, Node, ObjectPolicy, PolicyGraph, PolicyObject, PolicyRule, Rule,
    RuleKind, Value, LAYER_OBJECT_DEFAULT, LAYER_SURFACE_RULE,
};
use regex::Regex;
use std::path::PathBuf;

const VECTORS: &str = "proposals/atom-grammar/access-vectors/resolution.yaml";

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn member<'a>(n: Option<&'a Node>, key: &str) -> Option<&'a Node> {
    match n.map(|n| &n.value) {
        Some(Value::Map(es)) => es.iter().find(|e| e.key == key).map(|e| &e.value),
        _ => None,
    }
}

fn str_of(n: Option<&Node>) -> &str {
    match n.map(|n| &n.value) {
        Some(Value::Str(s)) => s,
        _ => "",
    }
}

fn bool_of(n: Option<&Node>) -> bool {
    matches!(n.map(|n| &n.value), Some(Value::Bool(true)))
}

fn pinned_sha256(path: &str) -> String {
    let pins =
        parse(&std::fs::read_to_string(repo_root().join("corpus/pins.yaml")).unwrap()).unwrap();
    let Some(Value::Map(grammar)) = member(Some(&pins), "grammar").map(|n| &n.value) else {
        panic!("no grammar pins")
    };
    for pin in grammar {
        if let Some(Value::Seq(files)) = member(Some(&pin.value), "files").map(|n| &n.value) {
            for f in files {
                if str_of(member(Some(f), "path")) == path {
                    return str_of(member(Some(f), "sha256")).to_string();
                }
            }
        }
    }
    panic!("the pin does not list {path}")
}

fn upstream_file(path: &str) -> String {
    let root = std::env::var_os("CIC_PRIMITIVES_ROOT").expect(
        "CIC_PRIMITIVES_ROOT must point at a checkout of CentralInfraCore/cic-primitives at the pinned commit",
    );
    let bytes = std::fs::read(PathBuf::from(root).join(path)).expect("the file in the checkout");
    assert_eq!(
        sha256_hex(&bytes),
        pinned_sha256(path),
        "{path} does not match the pin: not evidence"
    );
    String::from_utf8(bytes).unwrap()
}

fn read_rule_node(n: Option<&Node>) -> Option<PolicyRule> {
    let n = n?;
    match read_rule(n) {
        Ok(Rule::Symbol) => None,
        Ok(Rule::List(p)) => Some(PolicyRule::Patterns(p)),
        Err(v) => panic!("a vector rule does not read: {}", v.code),
    }
}

fn vector_graph(ctx: Option<&Node>, g: Grammar) -> PolicyGraph {
    let mut graph = PolicyGraph::new();
    let Some(Value::Map(objects)) = member(ctx, "objects").map(|n| &n.value) else {
        panic!("no objects in a vector")
    };
    for e in objects {
        let parent = match member(Some(&e.value), "parent").map(|n| &n.value) {
            Some(Value::Str(s)) => Some(s.clone()),
            _ => None,
        };
        let pol = member(Some(&e.value), "policy").expect("policy");
        let policy = match &pol.value {
            Value::Str(s) if s == "absent" => ObjectPolicy::Absent,
            Value::Str(s) if s == "unknown" => ObjectPolicy::Unknown,
            _ => ObjectPolicy::Declared(
                read_policy_surface(pol, g).unwrap_or_else(|err| panic!("{}: {err:?}", e.key)),
            ),
        };
        let carrier = member(Some(&e.value), "carrier").map(|car| Carrier {
            mode: member(Some(car), "inherit").map_or(Mode::Inherit, |m| {
                read_mode(m, g).unwrap_or_else(|v| panic!("{} carrier: {}", e.key, v.code))
            }),
            access: read_rule_node(member(Some(car), "access")),
            modify: read_rule_node(member(Some(car), "modify")),
        });
        graph.insert(
            e.key.clone(),
            PolicyObject {
                id: e.key.clone(),
                parent,
                policy,
                carrier,
                carrier_path: e.key.clone(),
            },
        );
    }
    graph
}

// ---- the TEST-ONLY port of the reference decision ----------------------------------------------------------------------------

#[derive(Debug, PartialEq, Eq)]
struct Outcome {
    outcome: String,
    code: String,
    layer: String,
}

fn out(o: &str, c: &str, l: &str) -> Outcome {
    Outcome {
        outcome: o.into(),
        code: c.into(),
        layer: l.into(),
    }
}

fn pairs(s: &str) -> Vec<(&str, &str)> {
    s.split(',')
        .map(|p| p.split_once('=').unwrap_or((p, "")))
        .collect()
}

fn pattern_matches(pattern: &str, subject: &str) -> bool {
    let have = pairs(subject);
    pairs(pattern).iter().all(|(attr, want)| {
        let parts: Vec<String> = want.split('*').map(regex::escape).collect();
        let rx = Regex::new(&format!("^{}$", parts.join(".*"))).unwrap();
        have.iter().any(|(a, v)| a == attr && rx.is_match(v))
    })
}

fn valid_identity(n: Option<&Node>) -> bool {
    matches!(n.map(|n| &n.value), Some(Value::Str(s)) if !s.is_empty() && s == s.trim())
}

fn decide(rule: &EffectiveRule, req: &Node) -> Outcome {
    let r = member(Some(req), "requestor");
    let layer = rule.layer;
    match rule.kind {
        RuleKind::None => return out("deny", rule.reason, layer),
        RuleKind::Unknown => return out("error", rule.reason, layer),
        _ => {}
    }
    if !bool_of(member(r, "authenticated")) {
        return out("deny", "UNAUTHENTICATED", layer);
    }
    match rule.kind {
        RuleKind::List => {
            if rule.patterns.is_empty() {
                return out("deny", "EXPLICIT_EMPTY", layer);
            }
            let subject = str_of(member(r, "subject"));
            if rule.patterns.iter().any(|p| pattern_matches(p, subject)) {
                return out("allow", "", layer);
            }
            return out("deny", "NO_PATTERN_MATCH", layer);
        }
        RuleKind::Open => return out("allow", "", layer),
        _ => {}
    }
    let owner = member(Some(req), "owner");
    if !matches!(owner.map(|n| &n.value), Some(Value::Map(_)))
        || str_of(member(owner, "trust")) != "trusted"
    {
        return out("deny", "OWNER_NOT_TRUSTED", layer);
    }
    if !valid_identity(member(owner, "identity")) {
        return out("deny", "OWNER_IDENTITY_INVALID", layer);
    }
    if !valid_identity(member(r, "identity")) {
        return out("deny", "REQUESTOR_IDENTITY_INVALID", layer);
    }
    if str_of(member(owner, "identity")) != str_of(member(r, "identity")) {
        return out("deny", "OWNER_MISMATCH", layer);
    }
    out("allow", "", layer)
}

fn request_valid(req: &Node) -> bool {
    let op = member(Some(req), "op");
    let r = member(Some(req), "requestor");
    let op_ok =
        matches!(op.map(|n| &n.value), Some(Value::Str(s)) if s == "access" || s == "modify");
    if !op_ok || !matches!(r.map(|n| &n.value), Some(Value::Map(_))) {
        return false;
    }
    let Some(Value::Bool(auth)) = member(r, "authenticated").map(|n| &n.value) else {
        return false;
    };
    if *auth {
        let whole = Regex::new(
            r"^[A-Za-z][A-Za-z0-9.-]*=[^=,\s][^=,]*(,[A-Za-z][A-Za-z0-9.-]*=[^=,\s][^=,]*)*$",
        )
        .unwrap();
        match member(r, "subject").map(|n| &n.value) {
            Some(Value::Str(s)) if whole.is_match(s) && !s.contains('*') => {}
            _ => return false,
        }
    }
    if let Some(o) = member(Some(req), "owner") {
        match &o.value {
            Value::Null => {}
            Value::Map(_) => {
                let trust = str_of(member(Some(o), "trust"));
                if trust != "trusted" && trust != "untrusted" {
                    return false;
                }
            }
            _ => return false,
        }
    }
    true
}

fn mapped_layer(l: &str) -> String {
    match l {
        "surface_rules" => LAYER_SURFACE_RULE.into(),
        "default" => LAYER_OBJECT_DEFAULT.into(),
        other => other.into(),
    }
}

#[test]
fn resolve_rule_against_the_golden_vectors() {
    let doc = parse(&upstream_file(VECTORS)).expect("the vector file parses");
    let Some(Value::Seq(cases)) = member(Some(&doc), "cases").map(|n| &n.value) else {
        panic!("no cases")
    };
    assert!(
        cases.len() >= 80,
        "expected the resolution vectors, found {}",
        cases.len()
    );
    for c in cases {
        let id = str_of(member(Some(c), "id"));
        let input = member(Some(c), "input");
        let Some(Value::Int(gv)) = member(input, "grammar").map(|n| &n.value) else {
            panic!("{id}: no grammar")
        };
        let gn = Value::Int(*gv);
        let g = bind_grammar(Some(&gn), Some(&gn)).unwrap();
        let exp = member(Some(c), "expect");
        let want = Outcome {
            outcome: str_of(member(exp, "outcome")).into(),
            code: str_of(member(exp, "code")).into(),
            layer: mapped_layer(str_of(member(exp, "layer"))),
        };
        let req = member(input, "req").expect("req");
        let got = if request_valid(req) {
            let graph = vector_graph(member(input, "ctx"), g);
            let target = member(input, "target");
            let op = str_of(member(Some(req), "op"));
            let field = read_rule_node(member(member(target, "field"), op));
            let rule = resolve_rule(
                &graph,
                str_of(member(target, "object")),
                str_of(member(target, "surface")),
                field.as_ref(),
                "$.target",
                op,
            );
            decide(&rule, req)
        } else {
            out("error", "INVALID_REQUEST_CONTEXT", "none")
        };
        assert_eq!(got, want, "{id}");
    }
}
