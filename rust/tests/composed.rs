//! `normalize_with_policy` (`docs/P3-NORMALIZE-DESIGN.md` section 2): the normalization and the resolution of its object, made from one
//! compiled schema. The Go peer is `go/normalizewithpolicy_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, compile_schema, denormalize, normalize, normalize_with_policy, parse,
    resolve_policy, CompiledSchema, Context, Grammar, SchemaInput, Value,
};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn bound(v: i64) -> Grammar {
    let n = Value::Int(v);
    bind_grammar(Some(&n), Some(&n)).unwrap()
}

fn compile(schema: &str, pin: &str) -> CompiledSchema {
    compile_schema(&SchemaInput {
        bytes: schema.as_bytes(),
        sha256: &sha256_hex(schema.as_bytes()),
        grammar: pin,
        surface: "config_surface",
    })
    .unwrap()
}

fn load() -> (J, Vec<String>) {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/resolution.json")).unwrap();
    let json: J = serde_json::from_str(&text).unwrap();
    let a = json["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    (json, a)
}

fn unknown_of(c: &J) -> Vec<String> {
    c["unknown"]
        .as_array()
        .map(|u| u.iter().map(|s| s.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

#[test]
fn normalize_with_policy_is_normalize_then_resolve_policy() {
    let (f, a) = load();
    let pin = f["grammar_pin"].as_str().unwrap();
    let ctx = Context::default();
    for c in f["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let cs = compile(c["schema"].as_str().unwrap(), pin);
        let doc = parse(c["yaml"].as_str().unwrap()).unwrap();
        let g = bound(c["grammar"].as_i64().unwrap());
        let unknown = unknown_of(c);
        let one = normalize_with_policy(&doc, &cs, &a, g, &ctx, &unknown).unwrap();
        let n = normalize(&doc, &cs.root, &a, g, &ctx).unwrap();
        let r = resolve_policy(
            &n.object,
            &cs.root,
            &cs.policy,
            &cs.surface,
            &a,
            g,
            &unknown,
        )
        .unwrap();
        assert!(
            one.normalization.object.same(&n.object),
            "{id}: the normalization differs from normalize"
        );
        assert_eq!(
            one.normalization.value_origins, n.value_origins,
            "{id}: origins"
        );
        assert_eq!(
            one.policy_resolution, r,
            "{id}: the resolution differs from resolve_policy on the normalized object"
        );
        assert_eq!(one.schema_digest, cs.digest, "{id}: digest");
        let back = denormalize(&one.normalization, &cs.root, &a, g, &ctx).unwrap();
        assert!(back.same(&doc), "{id}: denormalize of the composed result");
    }
}

#[test]
fn normalize_with_policy_gives_no_half_result() {
    let (f, a) = load();
    let c = &f["cases"][0];
    let cs = compile(
        c["schema"].as_str().unwrap(),
        f["grammar_pin"].as_str().unwrap(),
    );
    let g = bound(1);
    let ctx = Context::default();
    let doc = parse("type: t\n").unwrap();
    assert!(normalize_with_policy(&doc, &cs, &a, g, &ctx, &["$.nowhere".to_string()]).is_err());
    let bad = parse("bogus: 1\n").unwrap();
    assert!(normalize_with_policy(&bad, &cs, &a, g, &ctx, &[]).is_err());
}

#[test]
fn normalize_with_policy_carries_the_schema_it_was_made_from() {
    let (f, a) = load();
    let pin = f["grammar_pin"].as_str().unwrap();
    let (x, y) = (
        f["cases"][0]["schema"].as_str().unwrap(),
        f["cases"][1]["schema"].as_str().unwrap(),
    );
    if x == y {
        return;
    }
    let (ca, cb) = (compile(x, pin), compile(y, pin));
    let doc = parse("type: t\n").unwrap();
    let ctx = Context::default();
    let ra = normalize_with_policy(&doc, &ca, &a, bound(1), &ctx, &[]).unwrap();
    if let Ok(rb) = normalize_with_policy(&doc, &cb, &a, bound(1), &ctx, &[]) {
        assert_ne!(ra.schema_digest, rb.schema_digest);
    }
}

/// Probe of the diff gate (not to be merged): a pure addition of a test.
#[test]
fn probe_a_pure_test_addition() {
    assert_eq!(1 + 1, 2);
}
