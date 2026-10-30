//! Dumps the outcome of `materialize_flat` for the generated cases of `tools/parity/gen_materialize.py`, for the Rust/Go differential of the flat materialization (P5-2).
//! `PARITY_MATERIALIZE_IN` is a JSON-lines file ({"id", "tag", "schema", "doc", "caps", "keys", "want"}), `PARITY_MATERIALIZE_OUT` receives one JSON line per input.
//! Without the two variables this test does nothing. The Go peer is `go/parity_materialize_dump_test.go`; `tools/parity/compare_materialize.py` compares the two and both
//! against the reference, and the engine harnesses (`tools/p5/engine-harness-*`) take the same file as the third reader. `r` is
//! `["ok", expected_keys, projection, digest]` or `["err", reason, field]`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{compile_schema, materialize_flat, parse, SchemaInput};
use serde_json::{json, Value as Json};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

#[test]
fn dump_materialize_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_MATERIALIZE_IN"),
        std::env::var_os("PARITY_MATERIALIZE_OUT"),
    ) else {
        return;
    };
    let defaults: Json = serde_json::from_str(
        &fs::read_to_string(repo_root().join("testdata/p1/defaults.json")).unwrap(),
    )
    .unwrap();
    let grammar = defaults["grammar"].as_str().unwrap();
    let a: Vec<String> = defaults["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    let mut out = String::new();
    for line in fs::read_to_string(input).expect("input").lines() {
        let case: Json = serde_json::from_str(line).expect("a JSON line");
        let schema = case["schema"].as_str().unwrap();
        let compiled = compile_schema(&SchemaInput {
            bytes: schema.as_bytes(),
            sha256: &sha256_hex(schema.as_bytes()),
            grammar,
            surface: "config_surface",
        })
        .unwrap_or_else(|e| panic!("#{}: the schema does not compile: {e:?}", case["id"]));
        let doc = parse(case["doc"].as_str().unwrap())
            .unwrap_or_else(|e| panic!("#{}: the document does not parse: {e:?}", case["id"]));
        let caps: BTreeMap<String, String> = case["caps"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        let r = match materialize_flat(Some(&doc), &compiled.root, &a, &caps) {
            Ok(m) => json!(["ok", m.expected_keys, m.projection_json, m.output_digest]),
            Err(e) => json!(["err", e.reason, e.field]),
        };
        out += &json!({"id": case["id"], "r": r}).to_string();
        out.push('\n');
    }
    fs::write(output, out).expect("output");
}
