//! Dumps the outcome of `normalize` + `resolve_policy` for the generated inputs of `tools/parity/gen_p3.py`, for the Rust/Go differential
//! of P3 (G3 of the P3 exit-criteria check). `PARITY_P3_IN` is a JSON-lines file, `PARITY_P3_OUT` receives one JSON line per input.
//! Without the two variables this test does nothing. The Go peer is `go/parity_p3_dump_test.go`; `tools/parity/compare_p3.py` compares.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, compile_schema, emit, normalize, parse, resolve_policy, EffectiveRule, RuleKind,
    SchemaInput, Value,
};
use serde_json::{json, Value as Json};
use std::fs;

fn rule(r: &EffectiveRule) -> Json {
    let kind = match r.kind {
        RuleKind::List => "list",
        RuleKind::Open => "open",
        RuleKind::Owner => "owner",
        RuleKind::None => "none",
        RuleKind::Unknown => "unknown",
    };
    json!([
        kind,
        r.layer,
        r.source_path,
        r.reason,
        if r.kind == RuleKind::List {
            json!(r.patterns)
        } else {
            Json::Null
        }
    ])
}

fn run(case: &Json) -> Json {
    let id = &case["id"];
    let stage = |s: &str| json!({"id": id, "stage": s});
    let schema = case["schema"].as_str().unwrap();
    let Ok(cs) = compile_schema(&SchemaInput {
        bytes: schema.as_bytes(),
        sha256: &sha256_hex(schema.as_bytes()),
        grammar: case["pin"].as_str().unwrap(),
        surface: "config_surface",
    }) else {
        return stage("compile");
    };
    let Ok(doc) = parse(case["yaml"].as_str().unwrap()) else {
        return stage("parse");
    };
    let one = Value::Int(case["grammar"].as_i64().unwrap());
    let g = bind_grammar(Some(&one), Some(&one)).unwrap();
    let a: Vec<String> = case["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    let unknown: Vec<String> = case["unknown"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    let Ok(n) = normalize(
        &doc,
        &cs.root,
        &a,
        g,
        &cic_primitive_forms::Context::default(),
    ) else {
        return stage("normalize");
    };
    let Ok(r) = resolve_policy(
        &n.object,
        &cs.root,
        &cs.policy,
        &cs.surface,
        &a,
        g,
        &unknown,
    ) else {
        return stage("resolve");
    };
    let rules: serde_json::Map<String, Json> = r
        .iter()
        .map(|(p, e)| {
            (
                p.clone(),
                json!({"access": rule(&e.access), "modify": rule(&e.modify)}),
            )
        })
        .collect();
    let origins: Vec<Json> = n
        .value_origins
        .iter()
        .map(|o| json!([o.path, o.origin.to_string()]))
        .collect();
    json!({"id": id, "stage": "ok", "object": emit(&n.object), "origins": origins, "rules": rules})
}

#[test]
fn dump_p3_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_P3_IN"),
        std::env::var_os("PARITY_P3_OUT"),
    ) else {
        return;
    };
    let mut out = String::new();
    for line in fs::read_to_string(input).unwrap().lines() {
        out.push_str(&run(&serde_json::from_str(line).unwrap()).to_string());
        out.push('\n');
    }
    fs::write(output, out).unwrap();
}
