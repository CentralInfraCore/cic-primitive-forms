//! Dumps the outcome of `receipt_digest_of` / `map_release` for the input set of the digest mapping differential (`docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md`, section 3).
//! `PARITY_DIGEST_MAPPING_IN` is a JSON-lines file of cases ({"id", "kind": "convert" | "map", "input" | "record", "pin", ...}); `PARITY_DIGEST_MAPPING_OUT` receives
//! exactly one JSON line per input line, in order: {"id", "ok": true, "value"} or {"id", "ok": false, "reason", "field"}. Without the two variables this test does
//! nothing. The Go peer is `go/parity_digest_mapping_dump_test.go`; `tools/parity/compare_digest_mapping.py` compares the three readers.

use cic_primitive_forms::{map_release, receipt_digest_of, Entry, Node, Value};
use serde_json::{json, Value as J};
use std::collections::BTreeMap;

/// A JSON value as a [`Node`]. A number is an integer only when its spelling is one; every other number (3.0, 1e2) is `Unsupported`, as the document model has it.
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

#[test]
fn dump_digest_mapping_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_DIGEST_MAPPING_IN"),
        std::env::var_os("PARITY_DIGEST_MAPPING_OUT"),
    ) else {
        return;
    };
    let mut out = String::new();
    for line in std::fs::read_to_string(input).expect("input").lines() {
        if line.trim().is_empty() {
            continue;
        }
        let c: J = serde_json::from_str(line).expect("a case");
        let id = c["id"].as_str().expect("id");
        let outcome: Result<J, cic_primitive_forms::MappingError> =
            match c["kind"].as_str().expect("kind") {
                "convert" => receipt_digest_of(&node_of(&c["input"]), "").map(J::String),
                "map" => {
                    let pin: BTreeMap<String, String> = c["pin"]
                        .as_object()
                        .expect("pin")
                        .iter()
                        .map(|(k, v)| (k.clone(), v.as_str().expect("pin value").to_string()))
                        .collect();
                    map_release(&node_of(&c["record"]), &pin).map(|m| {
                        json!({
                            "primitive_build_hash": m.primitive_build_hash,
                            "grammar_sha256": m.grammar_sha256,
                            "grammar_schema_sha256": m.grammar_schema_sha256,
                        })
                    })
                }
                other => panic!("unknown kind {other}"),
            };
        let result = match outcome {
            Ok(value) => json!({"id": id, "ok": true, "value": value}),
            Err(e) => json!({
                "id": id,
                "ok": false,
                "reason": e.reason,
                "field": if e.field.is_empty() { J::Null } else { J::String(e.field) },
            }),
        };
        out.push_str(&result.to_string());
        out.push('\n');
    }
    std::fs::write(output, out).expect("write dump");
}
