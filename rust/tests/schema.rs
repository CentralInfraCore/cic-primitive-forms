//! The shared schema cases (`testdata/p1/schema.json`) against `compile_schema` and `validate`, the Rust
//! peer of `go/schema_test.go`. A case compiles a pinned schema and validates documents against its
//! surface; the expected outcome is the same file for both languages. The cases are synthetic and written
//! here; the real registry schema is checked in `real_schema.rs`.

use cic_primitive_forms::{compile_schema, parse, sha256_hex, validate, Error, SchemaInput};
use serde_json::Value as J;

/// The repository root; `CIC_FORMS_ROOT` overrides it because the mutation and branch tools run the tests in a
/// copy of the crate (as in `corpus.rs`).
fn repo_root() -> std::path::PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    )
}

fn strings(v: &J) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().map(|s| s.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

#[test]
fn the_shared_schema_cases() {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/schema.json")).unwrap();
    let file: J = serde_json::from_str(&text).unwrap();
    let grammar = file["grammar"].as_str().unwrap();
    let access = strings(&file["access_keys"]);
    let cases = file["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let schema = case["schema"].as_str().unwrap();
        let sha = case
            .get("sha256")
            .and_then(J::as_str)
            .map_or_else(|| sha256_hex(schema.as_bytes()), str::to_string);
        let input = SchemaInput {
            bytes: schema.as_bytes(),
            sha256: &sha,
            grammar: case.get("grammar").and_then(J::as_str).unwrap_or(grammar),
            surface: case["surface"].as_str().unwrap(),
        };
        let want = &case["compile"];
        let compiled = compile_schema(&input);
        if want.get("ok").and_then(J::as_bool) != Some(true) {
            let err = compiled.expect_err(id);
            assert_eq!(err.code(), want["error"].as_str().unwrap(), "{id}");
            if let Some(problems) = want.get("problems") {
                let got: Vec<Vec<String>> = match &err {
                    Error::Schema(p) => p
                        .iter()
                        .map(|p| vec![p.code.to_string(), p.path.clone()])
                        .collect(),
                    other => panic!("{id}: {other:?}"),
                };
                let expect: Vec<Vec<String>> =
                    problems.as_array().unwrap().iter().map(strings).collect();
                assert_eq!(got, expect, "{id}");
            }
            continue;
        }
        let compiled = compiled.unwrap_or_else(|e| panic!("{id}: {e:?}"));
        assert_eq!(compiled.uses_item_choice, want["uses_item_choice"], "{id}");
        assert_eq!(compiled.kind, want["kind"].as_str().unwrap(), "{id}");
        assert_eq!(
            compiled.namespace,
            want["namespace"].as_str().unwrap(),
            "{id}"
        );
        assert_eq!(compiled.version, want["version"].as_str().unwrap(), "{id}");
        assert_eq!(compiled.digest, sha, "{id}");
        assert_eq!(compiled.grammar, grammar, "{id}");
        assert_eq!(
            compiled.unevaluated_contracts,
            strings(&want["unevaluated_contracts"]),
            "{id}"
        );
        for d in case
            .get("documents")
            .and_then(J::as_array)
            .into_iter()
            .flatten()
        {
            let did = format!("{id}/{}", d["id"].as_str().unwrap());
            let node = parse(d["yaml"].as_str().unwrap()).unwrap();
            let result = validate(Some(&node), &compiled.root, &access);
            let expect = &d["expect"];
            if expect.get("unsupported").and_then(J::as_bool) == Some(true) {
                assert!(
                    matches!(result, Err(Error::Unsupported(_))),
                    "{did}: {result:?}"
                );
                continue;
            }
            let v = result.unwrap_or_else(|e| panic!("{did}: {e:?}"));
            let got: Vec<(String, String)> = v
                .violations
                .iter()
                .map(|x| {
                    (
                        x.code.to_string(),
                        x.logical_path.clone().unwrap_or_default(),
                    )
                })
                .collect();
            let want_v: Vec<(String, String)> = expect["violations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| {
                    (
                        x["code"].as_str().unwrap().to_string(),
                        x["path"].as_str().unwrap().to_string(),
                    )
                })
                .collect();
            assert_eq!(got, want_v, "{did}");
            assert_eq!(
                v.default_candidates,
                strings(&expect["candidates"]),
                "{did}"
            );
        }
    }
}
