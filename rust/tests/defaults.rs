//! The shared default cases (`testdata/p1/defaults.json`): `compile_schema` refuses what ADR 0011 says it must, and
//! `apply_defaults` inserts the declared default of an absent optional field and of nothing else. The Go peer is
//! `go/defaults_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    apply_defaults, compile_schema, emit, parse, validate, CompiledSchema, Error, Presence,
    ScalarType, SchemaInput, Shape, ShapeType, VALUE_AUTHORED, VALUE_DEFAULT,
};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn load() -> J {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/defaults.json")).unwrap();
    serde_json::from_str(&text).unwrap()
}

fn pairs(v: &J) -> Vec<Vec<String>> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|p| {
            p.as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_string())
                .collect()
        })
        .collect()
}

fn compile(schema: &str, grammar: &str, surface: &str) -> Result<CompiledSchema, Error> {
    let sha = sha256_hex(schema.as_bytes());
    compile_schema(&SchemaInput {
        bytes: schema.as_bytes(),
        sha256: &sha,
        grammar,
        surface,
    })
}

#[test]
fn the_shared_default_refusals() {
    let file = load();
    let grammar = file["grammar"].as_str().unwrap();
    let refusals = file["refusals"].as_array().unwrap();
    assert!(!refusals.is_empty());
    for c in refusals {
        let id = c["id"].as_str().unwrap();
        let err = compile(
            c["schema"].as_str().unwrap(),
            grammar,
            c["surface"].as_str().unwrap(),
        )
        .expect_err(id);
        let Error::Schema(problems) = &err else {
            panic!("{id}: {err:?}")
        };
        let got: Vec<Vec<String>> = problems
            .iter()
            .map(|p| vec![p.code.to_string(), p.path.clone()])
            .collect();
        assert_eq!(got, pairs(&c["problems"]), "{id}");
    }
}

#[test]
fn the_shared_accepted_roles() {
    let file = load();
    let grammar = file["grammar"].as_str().unwrap();
    let accepted = file["accepted"].as_array().unwrap();
    assert!(!accepted.is_empty());
    for c in accepted {
        let id = c["id"].as_str().unwrap();
        compile(
            c["schema"].as_str().unwrap(),
            grammar,
            c["surface"].as_str().unwrap(),
        )
        .unwrap_or_else(|e| panic!("{id}: {e:?}"));
    }
}

#[test]
fn apply_defaults_refuses_what_the_validation_does_not_support() {
    let file = load();
    let case = &file["cases"][0];
    let compiled = compile(
        case["schema"].as_str().unwrap(),
        file["grammar"].as_str().unwrap(),
        case["surface"].as_str().unwrap(),
    )
    .unwrap();
    let a: Vec<String> = file["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    // An expanded Access node inside a validated document is P2's, not a document this slice defaults.
    let doc = parse("type: t\nenabled:\n  value: true\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n").unwrap();
    let r = apply_defaults(Some(&doc), &compiled.root, &a);
    assert!(matches!(r, Err(Error::Unsupported(_))), "{r:?}");
}

#[test]
fn an_absent_optional_root_has_no_document() {
    let root = Shape {
        shape_type: ShapeType::Composite(vec![(
            "a".to_string(),
            Shape {
                shape_type: ShapeType::Scalar(ScalarType::String),
                presence: Presence::Optional,
                nullable: false,
                has_default: false,
                default: None,
            },
        )]),
        presence: Presence::Optional,
        nullable: false,
        has_default: false,
        default: None,
    };
    let res = apply_defaults(None, &root, &[]).unwrap();
    assert!(res.document.is_none() && res.violations.is_empty() && res.origins.is_empty());
}

#[test]
fn the_shared_apply_defaults_cases() {
    let file = load();
    let grammar = file["grammar"].as_str().unwrap();
    let a: Vec<String> = file["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    for c in file["cases"].as_array().unwrap() {
        let cid = c["id"].as_str().unwrap();
        let compiled = compile(
            c["schema"].as_str().unwrap(),
            grammar,
            c["surface"].as_str().unwrap(),
        )
        .unwrap_or_else(|e| panic!("{cid}: {e:?}"));
        for d in c["documents"].as_array().unwrap() {
            let id = format!("{cid}/{}", d["id"].as_str().unwrap());
            let doc = parse(d["yaml"].as_str().unwrap()).unwrap();
            let before = emit(&doc);
            let res = apply_defaults(Some(&doc), &compiled.root, &a).unwrap();
            assert_eq!(emit(&doc), before, "{id}: the input was modified");
            let expect = &d["expect"];
            let Some(want_doc) = expect.get("document").and_then(J::as_str) else {
                let got: Vec<Vec<String>> = res
                    .violations
                    .iter()
                    .map(|v| {
                        vec![
                            v.code.to_string(),
                            v.logical_path.clone().unwrap_or_default(),
                        ]
                    })
                    .collect();
                assert!(res.document.is_none(), "{id}");
                assert_eq!(got, pairs(&expect["violations"]), "{id}");
                continue;
            };
            assert!(res.violations.is_empty(), "{id}: {:?}", res.violations);
            let out = res.document.as_ref().unwrap();
            assert_eq!(emit(out), want_doc, "{id}");
            let origins: Vec<Vec<String>> = res
                .origins
                .iter()
                .map(|o| vec![o.path.clone(), o.origin.to_string()])
                .collect();
            assert_eq!(origins, pairs(&expect["origins"]), "{id}");
            // The defaulted positions are exactly the default candidates of the validation of the input.
            let v = validate(Some(&doc), &compiled.root, &a).unwrap();
            let mut defaulted: Vec<String> = res
                .origins
                .iter()
                .filter(|o| o.origin == VALUE_DEFAULT)
                .map(|o| o.path.clone())
                .collect();
            let mut candidates = v.default_candidates.clone();
            defaulted.sort();
            candidates.sort();
            assert_eq!(defaulted, candidates, "{id}");
            // Idempotence: the result needs no further defaults, and every value of it is written in it.
            let again = apply_defaults(Some(out), &compiled.root, &a).unwrap();
            assert_eq!(emit(again.document.as_ref().unwrap()), want_doc, "{id}");
            assert!(
                again.origins.iter().all(|o| o.origin == VALUE_AUTHORED),
                "{id}"
            );
        }
    }
}
