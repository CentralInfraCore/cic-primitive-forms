//! Dumps the outcome of `canonical_bytes` for the generated values of `tools/parity/gen_canonical.py`, for the Rust/Go differential of the canonical bytes (P5-1).
//! `PARITY_CANONICAL_IN` is a JSON-lines file ({"id", "tag", "spec", "want"}), `PARITY_CANONICAL_OUT` receives one JSON line per input. Without the two variables this
//! test does nothing. The Go peer is `go/parity_canonical_dump_test.go`; `tools/parity/compare_canonical.py` compares the two and both against the reference.
//!
//! The spec form (see the generator): null, a bool and a string are themselves; `{"i": "123"}` an integer; `{"u": "1.5"}` a value the library does not hold; a list a
//! sequence; `{"m": [[key, value], ...]}` a map with its entries in the given order. `r` is `["ok", the bytes as text, the digest]` or `["err", reason]`.

use cic_primitive_forms::{canonical_bytes, canonical_digest, Entry, Node, Value};
use serde_json::{json, Value as Json};
use std::fs;

fn build(spec: &Json) -> Node {
    Node::new(match spec {
        Json::Null => Value::Null,
        Json::Bool(b) => Value::Bool(*b),
        Json::String(s) => Value::Str(s.clone()),
        Json::Array(items) => Value::Seq(items.iter().map(build).collect()),
        Json::Object(o) => {
            if let Some(i) = o.get("i") {
                Value::Int(i.as_str().expect("digits").parse().expect("an i64"))
            } else if let Some(u) = o.get("u") {
                Value::Unsupported(u.as_str().expect("spelling").to_string())
            } else {
                Value::Map(
                    o["m"]
                        .as_array()
                        .expect("entries")
                        .iter()
                        .map(|e| Entry {
                            key: e[0].as_str().expect("key").to_string(),
                            key_pos: None,
                            value: build(&e[1]),
                        })
                        .collect(),
                )
            }
        }
        Json::Number(_) => panic!("a bare number is not in the spec form"),
    })
}

#[test]
fn dump_canonical_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_CANONICAL_IN"),
        std::env::var_os("PARITY_CANONICAL_OUT"),
    ) else {
        return;
    };
    let mut out = String::new();
    for line in fs::read_to_string(input).expect("input").lines() {
        let case: Json = serde_json::from_str(line).expect("a JSON line");
        let r = match canonical_bytes(&build(&case["spec"])) {
            Ok(b) => json!([
                "ok",
                String::from_utf8(b.clone()).expect("UTF-8"),
                canonical_digest(&b)
            ]),
            Err(e) => json!(["err", e.reason]),
        };
        out += &json!({"id": case["id"], "r": r}).to_string();
        out.push('\n');
    }
    fs::write(output, out).expect("output");
}
