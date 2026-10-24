//! `normalize_with_policy` (`docs/P3-NORMALIZE-DESIGN.md` section 2): the normalization and the resolution of its object, made from one
//! compiled schema. The Go peer is `go/normalizewithpolicy_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, compile_schema, denormalize, expand, normalize, normalize_with_policy, parse,
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

/// `normalize`, `resolve_policy` and `normalize_with_policy` start with the validation (#88): a value of the wrong type, also
/// inside an expanded wrapper, and two list elements of one identity are refused as `validate` refuses them, and nothing is made;
/// a key set without access, modify or inherit is an error. The schema is that of a shared resolution case (a mandatory type, an
/// integer metric, routes keyed by to). The Go peer is `TestTheBoundariesValidateFirst` in go/normalizewithpolicy_test.go.
#[test]
fn the_boundaries_validate_first() {
    let (f, a) = load();
    let pin = f["grammar_pin"].as_str().unwrap();
    let schema = f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "an-absent-field-gets-no-entry-and-an-inserted-default-does")
        .unwrap()["schema"]
        .as_str()
        .unwrap();
    let cs = compile(schema, pin);
    let g = bound(1);
    let ctx = Context::default();
    let wrapper = "{value: abc, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}";
    for (yaml, want) in [
        (
            "type: t\nroutes:\n  - to: a\n    metric: abc\n".to_string(),
            "value where",
        ),
        (
            format!("type: t\nroutes:\n  - to: a\n    metric: {wrapper}\n"),
            "value where",
        ),
        (
            "type: t\nroutes:\n  - to: a\n  - to: a\n".to_string(),
            "PATH_DUPLICATE_IDENTITY",
        ),
        ("routes: []\n".to_string(), "MISSING_MANDATORY"),
    ] {
        let doc = parse(&yaml).unwrap();
        let says = |e: &cic_primitive_forms::Error| format!("{e:?}").contains(want);
        match normalize(&doc, &cs.root, &a, g, &ctx) {
            Err(e) => assert!(says(&e), "{yaml:?}, normalize: {e:?}"),
            Ok(_) => panic!("{yaml:?}: normalize made a result"),
        }
        // resolve_policy reads an expanded object: expand makes one and checks no values
        let (object, _) = expand(&doc, &cs.root, &a, g, &ctx).expect("expand");
        match resolve_policy(&object, &cs.root, &cs.policy, &cs.surface, &a, g, &[]) {
            Err(e) => assert!(says(&e), "{yaml:?}, resolve_policy: {e:?}"),
            Ok(_) => panic!("{yaml:?}: resolve_policy made a result"),
        }
        match normalize_with_policy(&doc, &cs, &a, g, &ctx, &[]) {
            Err(e) => assert!(says(&e), "{yaml:?}, normalize_with_policy: {e:?}"),
            Ok(_) => panic!("{yaml:?}: normalize_with_policy made a result"),
        }
    }
    // a key set without access: the wrapper the normalization makes has none, and the resolution refuses it
    let no_access: Vec<String> = [
        "value",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .iter()
    .map(|k| (*k).to_string())
    .collect();
    let doc = parse("type: t\n").unwrap();
    match normalize_with_policy(&doc, &cs, &no_access, g, &ctx, &[]) {
        Err(cic_primitive_forms::Error::Unsupported(m)) => {
            assert!(m.contains("the position has no access"), "{m}");
        }
        other => panic!("{other:?}"),
    }
}

/// `normalize` reads the rule members of an authored expanded wrapper under the bound grammar, as the resolution does (#90):
/// a member it cannot read is refused with the resolution's message, and a wrapper it can read is normalized as before. The Go
/// peer is `TestNormalizeReadsTheRuleMembersOfAnAuthoredWrapper` in go/normalizewithpolicy_test.go.
#[test]
fn normalize_reads_the_rule_members_of_an_authored_wrapper() {
    let (f, a) = load();
    let pin = f["grammar_pin"].as_str().unwrap();
    let schema = f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "an-absent-field-gets-no-entry-and-an-inserted-default-does")
        .unwrap()["schema"]
        .as_str()
        .unwrap();
    let cs = compile(schema, pin);
    let g = bound(1);
    let ctx = Context::default();
    let wrapper = |access: &str, modify: &str, inherit: &str| {
        format!("type: t\nlabel:\n  value: hello\n  access: {access}\n  modify: {modify}\n  inherit: {inherit}\n  default_injection: null\n  conformance: implemented\n")
    };
    for (yaml, want) in [
        (
            wrapper("123", "inherit", "true"),
            "$.label: access of the position: INVALID_ACCESS_VALUE",
        ),
        (
            wrapper("inherit", "5", "true"),
            "$.label: modify of the position: INVALID_ACCESS_VALUE",
        ),
        (
            wrapper("inherit", "inherit", "maybe"),
            "$.label: inherit of the position: INVALID_INHERIT",
        ),
    ] {
        let doc = parse(&yaml).unwrap();
        match normalize(&doc, &cs.root, &a, g, &ctx) {
            Err(cic_primitive_forms::Error::Unsupported(m)) => {
                assert!(m.contains(want), "{yaml:?}: {m}")
            }
            other => panic!("{yaml:?}: want {want:?}, got {other:?}"),
        }
    }
    let doc = parse(&wrapper("['O=r']", "inherit", "false")).unwrap();
    normalize(&doc, &cs.root, &a, g, &ctx).expect("a wrapper the grammar reads");
}

/// `expand` does not read the rule members of an authored wrapper: only `normalize` does (#90). An `access` the grammar cannot
/// read is kept as authored. The Go peer is `TestExpandDoesNotReadTheRuleMembersOfAnAuthoredWrapper`.
#[test]
fn expand_does_not_read_the_rule_members_of_an_authored_wrapper() {
    let (f, a) = load();
    let pin = f["grammar_pin"].as_str().unwrap();
    let schema = f["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == "an-absent-field-gets-no-entry-and-an-inserted-default-does")
        .unwrap()["schema"]
        .as_str()
        .unwrap();
    let cs = compile(schema, pin);
    let doc = parse("type: t\nlabel:\n  value: hello\n  access: 123\n  modify: inherit\n  inherit: true\n  default_injection: null\n  conformance: implemented\n").unwrap();
    expand(&doc, &cs.root, &a, bound(1), &Context::default())
        .expect("expand keeps an unreadable member as authored");
    assert!(
        normalize(&doc, &cs.root, &a, bound(1), &Context::default()).is_err(),
        "normalize reads it"
    );
}
