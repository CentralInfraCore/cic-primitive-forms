//! The shared policy cases (`testdata/p1/policy.json`): `compile_schema` reads `spec.policy_surface` and
//! `identity.base` the same way as the Go peer (`go/policy_test.go`).

use cic_primitive_forms::access::{sha256_hex, Mode};
use cic_primitive_forms::{compile_schema, Error, PolicyRule, PolicySurface, SchemaInput};
use serde_json::{json, Value as J};
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn rule_json(r: &Option<PolicyRule>) -> J {
    match r {
        None => J::Null,
        Some(PolicyRule::Symbol(s)) => json!({ "symbol": s }),
        Some(PolicyRule::Patterns(p)) => json!({ "patterns": p }),
    }
}

fn policy_json(p: &PolicySurface) -> J {
    let rules: Vec<J> = p
        .surface_rules
        .iter()
        .map(|r| {
            json!({ "surface": r.surface, "access": rule_json(&r.access), "modify": rule_json(&r.modify) })
        })
        .collect();
    let mode = match p.inherit_policy {
        Mode::Inherit => "inherit",
        Mode::NoInherit => "no-inherit",
        Mode::Reset => "reset",
    };
    json!({
        "declared": p.declared,
        "default_access": rule_json(&p.default_access),
        "default_modify": rule_json(&p.default_modify),
        "surface_rules": rules,
        "inherit_policy": mode,
        "inherit_policy_declared": p.inherit_policy_declared,
    })
}

#[test]
fn the_shared_policy_cases() {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/policy.json")).unwrap();
    let file: J = serde_json::from_str(&text).unwrap();
    let grammar = file["grammar"].as_str().unwrap();
    let cases = file["cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    for case in cases {
        let id = case["id"].as_str().unwrap();
        let schema = case["schema"].as_str().unwrap();
        let sha = sha256_hex(schema.as_bytes());
        let input = SchemaInput {
            bytes: schema.as_bytes(),
            sha256: &sha,
            grammar,
            surface: case["surface"].as_str().unwrap(),
        };
        let want = &case["compile"];
        let compiled = compile_schema(&input);
        if want["ok"] != true {
            let err = compiled.expect_err(id);
            assert_eq!(err.code(), want["error"].as_str().unwrap(), "{id}");
            let Error::Schema(problems) = &err else {
                panic!("{id}: {err:?}")
            };
            let got: Vec<Vec<String>> = problems
                .iter()
                .map(|p| vec![p.code.to_string(), p.path.clone()])
                .collect();
            let expect: Vec<Vec<String>> = want["problems"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| {
                    p.as_array()
                        .unwrap()
                        .iter()
                        .map(|s| s.as_str().unwrap().to_string())
                        .collect()
                })
                .collect();
            assert_eq!(got, expect, "{id}");
            for (i, d) in want
                .get("details")
                .and_then(J::as_array)
                .into_iter()
                .flatten()
                .enumerate()
            {
                let d = d.as_str().unwrap();
                assert!(
                    problems[i].detail.contains(d),
                    "{id}: problem {i} detail {:?} does not contain {d:?}",
                    problems[i].detail
                );
            }
            continue;
        }
        let compiled = compiled.unwrap_or_else(|e| panic!("{id}: {e:?}"));
        assert_eq!(compiled.base, want["base"].as_str().unwrap(), "{id}");
        assert_eq!(policy_json(&compiled.policy), want["policy"], "{id}");
    }
}
