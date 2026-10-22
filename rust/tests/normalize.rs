//! The shared normalize cases (`testdata/p1/normalize.json`): `normalize` is `expand` plus the declared defaults, with the
//! origin of every value kept beside the object, and `denormalize` gives the authored document back. The Go peer is
//! `go/normalize_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, compile_schema, denormalize, emit, normalize, parse, renormalize, validate,
    CompiledSchema, Context, DefaultValue, DocOrigin, Error, Grammar, Node, NormalizationResult,
    Presence, ScalarType, SchemaInput, Shape, ShapeType, Value, VALUE_AUTHORED, VALUE_DEFAULT,
};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

struct File {
    json: J,
    compiled: CompiledSchema,
    a: Vec<String>,
}

fn load() -> File {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/normalize.json")).unwrap();
    let json: J = serde_json::from_str(&text).unwrap();
    let schema = json["schema"].as_str().unwrap();
    let sha = sha256_hex(schema.as_bytes());
    let compiled = compile_schema(&SchemaInput {
        bytes: schema.as_bytes(),
        sha256: &sha,
        grammar: json["grammar_pin"].as_str().unwrap(),
        surface: json["surface"].as_str().unwrap(),
    })
    .unwrap();
    let a = json["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    File { json, compiled, a }
}

fn bound(v: i64) -> Grammar {
    let n = Value::Int(v);
    bind_grammar(Some(&n), Some(&n)).unwrap()
}

fn origin_pairs(res: &NormalizationResult) -> Vec<(String, String)> {
    res.value_origins
        .iter()
        .map(|o| (o.path.clone(), o.origin.to_string()))
        .collect()
}

fn prov_pairs(res: &NormalizationResult) -> Vec<(String, bool)> {
    res.provenance
        .iter()
        .map(|(k, v)| (k.clone(), v.authored_expanded))
        .collect()
}

fn pairs(v: &J) -> Vec<(String, String)> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|p| {
            (
                p[0].as_str().unwrap().to_string(),
                p[1].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

#[test]
fn the_shared_normalize_cases() {
    let f = load();
    let docs = f.json["documents"].as_array().unwrap();
    assert!(!docs.is_empty());
    for d in docs {
        let id = d["id"].as_str().unwrap();
        let g = bound(d["grammar"].as_i64().unwrap());
        let doc = parse(d["yaml"].as_str().unwrap()).unwrap();
        let before = emit(&doc);
        let res = normalize(&doc, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
        assert_eq!(emit(&doc), before, "{id}: the input was modified");
        assert_eq!(origin_pairs(&res), pairs(&d["origins"]), "{id}: origins");
        let mut want_prov: Vec<(String, bool)> = d["provenance"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| (p[0].as_str().unwrap().to_string(), p[1].as_bool().unwrap()))
            .collect();
        want_prov.sort();
        assert_eq!(prov_pairs(&res), want_prov, "{id}: provenance");
        let text = emit(&res.object);
        for (key, atom) in d["inserted"].as_object().unwrap() {
            assert!(
                text.contains(atom.as_str().unwrap()),
                "{id}: the inserted {key} is not\n{atom}\nin\n{text}"
            );
        }
        // an inserted position has no provenance: the author did not write it
        for o in &res.value_origins {
            assert_eq!(
                res.provenance.contains_key(&o.path),
                o.origin == VALUE_AUTHORED,
                "{id}: {} is {} with provenance {}",
                o.path,
                o.origin,
                res.provenance.contains_key(&o.path)
            );
        }
        // the round trip: the authored document comes back, as a value
        let back = denormalize(&res, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
        assert!(
            back.same(&doc),
            "{id}: denormalize(normalize(x)) != x:\n{}\nwant\n{before}",
            emit(&back)
        );
        // idempotence is a property of the pair
        let again = renormalize(&res, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
        assert_eq!(emit(&again.object), text, "{id}");
        assert_eq!(origin_pairs(&again), pairs(&d["origins"]), "{id}");
        assert_eq!(prov_pairs(&again), prov_pairs(&res), "{id}");
        // ... and not of the object alone: normalizing the bare object calls every inserted value authored
        let bare = normalize(&res.object, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
        assert_eq!(emit(&bare.object), text, "{id}");
        assert!(
            bare.value_origins
                .iter()
                .all(|o| o.origin == VALUE_AUTHORED),
            "{id}"
        );
        // the defaulted positions are the default candidates of the validation (a short document only)
        if let Ok(v) = validate(Some(&doc), &f.compiled.root, &f.a) {
            let mut defaulted: Vec<String> = res
                .value_origins
                .iter()
                .filter(|o| o.origin == VALUE_DEFAULT)
                .map(|o| o.path.clone())
                .collect();
            let mut candidates = v.default_candidates.clone();
            defaulted.sort();
            candidates.sort();
            assert_eq!(defaulted, candidates, "{id}");
        }
    }
}

#[test]
fn normalize_is_independent_of_list_order() {
    let f = load();
    let g = bound(1);
    let run = |y: &str| {
        normalize(
            &parse(y).unwrap(),
            &f.compiled.root,
            &f.a,
            g,
            &Context::default(),
        )
        .unwrap()
    };
    let a = run("type: t\nroutes:\n  - to: b\n    metric: 0\n  - to: a\n    via: g\n");
    let b = run("type: t\nroutes:\n  - to: a\n    via: g\n  - to: b\n    metric: 0\n");
    assert_eq!(origin_pairs(&a), origin_pairs(&b));
    assert_eq!(prov_pairs(&a), prov_pairs(&b));
}

#[test]
fn normalize_refuses_what_expand_refuses() {
    let f = load();
    let doc = parse("type: t\nbogus: 1\n").unwrap();
    let r = normalize(&doc, &f.compiled.root, &f.a, bound(1), &Context::default());
    match r {
        Err(Error::Contract(vs)) => assert_eq!(vs[0].code, "UNKNOWN_FIELD"),
        other => panic!("{other:?}"),
    }
    let materialized = Context {
        origin: DocOrigin::Materialized,
        ..Context::default()
    };
    assert!(normalize(&doc, &f.compiled.root, &f.a, bound(1), &materialized).is_err());
}

#[test]
fn normalize_never_defaults_a_mandatory_field_or_absent_structure() {
    let s = |p, d: Option<&str>| Shape {
        shape_type: ShapeType::Scalar(ScalarType::String),
        presence: p,
        nullable: false,
        has_default: d.is_some(),
        default: d.map(|x| DefaultValue(Node::new(Value::Str(x.into())))),
    };
    let composite = |t, p| Shape {
        shape_type: t,
        presence: p,
        nullable: false,
        has_default: false,
        default: None,
    };
    let root = composite(
        ShapeType::Composite(vec![
            ("must".to_string(), s(Presence::Mandatory, Some("x"))), // hand-built: the compiler never produces this
            (
                "box".to_string(),
                composite(
                    ShapeType::Composite(vec![(
                        "in".to_string(),
                        s(Presence::Optional, Some("x")),
                    )]),
                    Presence::Optional,
                ),
            ),
        ]),
        Presence::Mandatory,
    );
    let doc = parse("{}\n").unwrap();
    let a: Vec<String> = [
        "value",
        "access",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .iter()
    .map(|k| (*k).to_string())
    .collect();
    let res = normalize(&doc, &root, &a, bound(1), &Context::default()).unwrap();
    assert!(res.value_origins.is_empty());
    assert_eq!(emit(&res.object), "{}\n");
}

#[test]
fn denormalize_keeps_an_attested_position_expanded() {
    let f = load();
    let g = bound(1);
    let ctx = Context {
        origin: DocOrigin::Materialized,
        attestation: Some("att".into()),
        attested_positions: Some(
            f.json["attested"]["attested"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_string())
                .collect(),
        ),
    };
    let doc = parse(f.json["attested"]["yaml"].as_str().unwrap()).unwrap();
    let res = normalize(&doc, &f.compiled.root, &f.a, g, &ctx).unwrap();
    let back = denormalize(&res, &f.compiled.root, &f.a, g, &ctx).unwrap();
    assert_eq!(
        emit(&back),
        f.json["attested"]["denormalized"].as_str().unwrap()
    );
}

#[test]
fn normalize_and_denormalize_check_the_context() {
    let f = load();
    let g = bound(1);
    let doc = parse("type: t\n").unwrap();
    let bad = Context {
        origin: DocOrigin::Materialized,
        ..Context::default()
    };
    assert!(normalize(&doc, &f.compiled.root, &f.a, g, &bad).is_err());
    let res = normalize(&doc, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
    assert!(denormalize(&res, &f.compiled.root, &f.a, g, &bad).is_err());
    assert!(renormalize(&res, &f.compiled.root, &f.a, g, &bad).is_err());
}

#[test]
fn renormalize_of_a_result_that_does_not_know_a_position_calls_it_authored() {
    let f = load();
    let g = bound(1);
    let doc = parse("type: t\n").unwrap();
    let res = normalize(&doc, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
    // a result with only the object: nothing is known about who wrote what
    let bare = NormalizationResult {
        object: res.object.clone(),
        provenance: Default::default(),
        value_origins: Vec::new(),
    };
    let again = renormalize(&bare, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
    assert!(again
        .value_origins
        .iter()
        .all(|o| o.origin == VALUE_AUTHORED));
    assert_eq!(again.provenance.len(), again.value_origins.len());
    // an object the schema does not allow is refused, not repaired
    let broken = NormalizationResult {
        object: parse("bogus: 1\n").unwrap(),
        provenance: Default::default(),
        value_origins: Vec::new(),
    };
    assert!(renormalize(&broken, &f.compiled.root, &f.a, g, &Context::default()).is_err());
}

#[test]
fn denormalize_does_not_depend_on_the_order_of_the_object() {
    let f = load();
    let g = bound(1);
    let doc = parse("type: t\nlabel: y\n").unwrap();
    let mut res = normalize(&doc, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
    // the inserted values first, the authored ones after: an entry to be removed is not the last one
    if let Value::Map(entries) = &mut res.object.value {
        entries.reverse();
    }
    let back = denormalize(&res, &f.compiled.root, &f.a, g, &Context::default()).unwrap();
    assert!(back.same(&doc), "{}", emit(&back));
}

#[test]
fn a_default_is_a_whole_value_nothing_below_it_is_defaulted_or_authored() {
    let leaf = Shape {
        shape_type: ShapeType::Scalar(ScalarType::String),
        presence: Presence::Optional,
        nullable: false,
        has_default: true,
        default: Some(DefaultValue(Node::new(Value::Str("x".into())))),
    };
    let inner = Shape {
        shape_type: ShapeType::Composite(vec![("in".to_string(), leaf)]),
        presence: Presence::Optional,
        nullable: false,
        has_default: true,
        default: Some(DefaultValue(Node::new(Value::Map(Vec::new())))),
    };
    let root = Shape {
        shape_type: ShapeType::Composite(vec![("box".to_string(), inner)]),
        presence: Presence::Mandatory,
        nullable: false,
        has_default: false,
        default: None,
    };
    let a: Vec<String> = [
        "value",
        "access",
        "modify",
        "inherit",
        "default_injection",
        "conformance",
    ]
    .iter()
    .map(|k| (*k).to_string())
    .collect();
    let res = normalize(
        &parse("{}\n").unwrap(),
        &root,
        &a,
        bound(1),
        &Context::default(),
    )
    .unwrap();
    assert_eq!(
        origin_pairs(&res),
        vec![("$.box".to_string(), "default".to_string())]
    );
    assert!(res.provenance.is_empty());
    assert!(!emit(&res.object).contains("in:"), "{}", emit(&res.object));
}
