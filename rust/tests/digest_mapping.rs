//! The release record to receipt digest mapping (P5-3, condition 8; `docs/P5-DIGEST-MAPPING-CONTRACT.md`): the shared cases of `testdata/p5/digest-mapping.json`,
//! written by `tools/p5/digest_mapping_cases.py` (expected values computed outside this code: raw bytes, openssl, the pin). The Go peer is
//! `go/digestmapping_test.go` and the Python reference `tools/p5/digest_mapping.py`.

use cic_primitive_forms::{
    map_release, receipt_digest_of, Entry, MappingError, Node, Value, MAPPING_ARTIFACT_ABSENT,
    MAPPING_ARTIFACT_DUPLICATE, MAPPING_BASE64_LENGTH, MAPPING_BASE64_NOT_CANONICAL,
    MAPPING_BASE64_SHAPE, MAPPING_DIGEST_FORM, MAPPING_ENVELOPE_NOT_3, MAPPING_FIELD_MISSING,
    MAPPING_FIELD_TYPE, MAPPING_PROVENANCE_ARTIFACTS_DISAGREE, MAPPING_RECORD_PIN_DISAGREE,
};
use serde_json::Value as J;
use std::collections::BTreeMap;
use std::path::PathBuf;

fn root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

/// A JSON value as a [`Node`]. A number is an integer only when its spelling is one; every other number (3.0, 1e2) is `Unsupported`, as the document model has
/// it, so that the float 3.0 is not the integer 3.
fn node_of(v: &J) -> Node {
    Node::new(match v {
        J::Null => Value::Null,
        J::Bool(b) => Value::Bool(*b),
        J::String(s) => Value::Str(s.clone()),
        J::Number(n) => n
            .as_i64()
            .map_or_else(|| Value::Unsupported(n.to_string()), Value::Int),
        J::Array(items) => Value::Seq(items.iter().map(node_of).collect()),
        J::Object(map) => Value::Map(
            map.iter()
                .map(|(k, v)| Entry {
                    key: k.clone(),
                    key_pos: None,
                    value: node_of(v),
                })
                .collect(),
        ),
    })
}

fn cases() -> (Vec<String>, Vec<J>) {
    let text = std::fs::read_to_string(root().join("testdata/p5/digest-mapping.json"))
        .expect("testdata/p5/digest-mapping.json");
    let doc: J = serde_json::from_str(&text).expect("JSON");
    let reasons = doc["reasons"]
        .as_array()
        .expect("reasons")
        .iter()
        .map(|r| r.as_str().expect("reason").to_string())
        .collect();
    (reasons, doc["cases"].as_array().expect("cases").clone())
}

#[test]
fn every_shared_case_gets_the_expected_answer() {
    let (_, cases) = cases();
    assert!(
        cases.len() >= 80,
        "the shared file has {} cases: not the file this test was written for",
        cases.len()
    );
    let mut wrong = Vec::new();
    for c in &cases {
        let id = c["id"].as_str().expect("id");
        let refuse = c["refuse"].as_str();
        let outcome: Result<BTreeMap<&str, String>, MappingError> =
            match c["kind"].as_str().expect("kind") {
                "convert" => receipt_digest_of(&node_of(&c["input"]), "")
                    .map(|d| BTreeMap::from([("digest", d)])),
                "map" => {
                    let pin: BTreeMap<String, String> = c["pin"]
                        .as_object()
                        .expect("pin")
                        .iter()
                        .map(|(k, v)| (k.clone(), v.as_str().expect("pin value").to_string()))
                        .collect();
                    map_release(&node_of(&c["record"]), &pin).map(|m| {
                        BTreeMap::from([
                            ("primitive_build_hash", m.primitive_build_hash),
                            ("grammar_sha256", m.grammar_sha256),
                            ("grammar_schema_sha256", m.grammar_schema_sha256),
                        ])
                    })
                }
                other => panic!("unknown kind {other}"),
            };
        match (refuse, outcome) {
            (None, Ok(got)) => {
                let want: BTreeMap<String, String> = if c["kind"] == "convert" {
                    BTreeMap::from([(
                        "digest".to_string(),
                        c["expect"].as_str().expect("expect").to_string(),
                    )])
                } else {
                    c["expect"]
                        .as_object()
                        .expect("expect")
                        .iter()
                        .map(|(k, v)| (k.clone(), v.as_str().expect("digest").to_string()))
                        .collect()
                };
                for (k, v) in &want {
                    if got.get(k.as_str()) != Some(v) {
                        wrong.push(format!("{id}: {k} got {:?}, want {v}", got.get(k.as_str())));
                    }
                }
            }
            (None, Err(e)) => wrong.push(format!("{id}: refused ({e}), expected a value")),
            (Some(reason), Ok(_)) => wrong.push(format!("{id}: a value, expected {reason}")),
            (Some(reason), Err(e)) => {
                if e.reason != reason {
                    wrong.push(format!(
                        "{id}: reason {} (field {}), want {reason}",
                        e.reason, e.field
                    ));
                } else if let Some(field) = c["field"].as_str() {
                    if e.field != field {
                        wrong.push(format!("{id}: field {}, want {field} ({reason})", e.field));
                    }
                }
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "{} wrong answers:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

#[test]
fn the_reasons_are_the_contracts() {
    let (reasons, _) = cases();
    let mut ours = vec![
        MAPPING_ENVELOPE_NOT_3,
        MAPPING_FIELD_MISSING,
        MAPPING_FIELD_TYPE,
        MAPPING_BASE64_SHAPE,
        MAPPING_BASE64_NOT_CANONICAL,
        MAPPING_BASE64_LENGTH,
        MAPPING_DIGEST_FORM,
        MAPPING_ARTIFACT_ABSENT,
        MAPPING_ARTIFACT_DUPLICATE,
        MAPPING_PROVENANCE_ARTIFACTS_DISAGREE,
        MAPPING_RECORD_PIN_DISAGREE,
    ];
    let mut theirs: Vec<&str> = reasons.iter().map(String::as_str).collect();
    ours.sort_unstable();
    theirs.sort_unstable();
    assert_eq!(ours, theirs);
}

#[test]
fn a_non_string_node_is_a_type_error_never_a_panic() {
    for v in [
        Value::Null,
        Value::Bool(true),
        Value::Int(7),
        Value::Seq(vec![]),
        Value::Map(vec![]),
        Value::Unsupported("3.0".to_string()),
    ] {
        let e = receipt_digest_of(&Node::new(v), "x").unwrap_err();
        assert_eq!((e.reason, e.field.as_str()), (MAPPING_FIELD_TYPE, "x"));
    }
}
