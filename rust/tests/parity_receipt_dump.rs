//! Dumps the outcome of `receipt_flat_fixture` for the generated cases of `tools/parity/gen_materialize.py`, for the Rust/Go differential of the flat receipt (P5-3).
//! `PARITY_RECEIPT_IN` is the same JSON-lines file as the materialization differential ({"id", "tag", "schema", "doc", "caps", "keys", "want"}), `PARITY_RECEIPT_OUT`
//! receives one JSON line per input. Without the two variables this test does nothing. The Go peer is `go/parity_receipt_dump_test.go`;
//! `tools/parity/compare_receipt.py` compares the two and both against a reference computed in Python, and the engine harnesses take the same files: the engine's own
//! `verify_receipt` is the third reader. `r` is `["ok", document, authored_input, binding, expected_keys, projection]` or `["err", reason, field]`. Only the release
//! and the engine are fixture data (the file `testdata/p5/receipt.json` holds them): an authentic receipt is not what this produces.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    parse, receipt_flat_fixture, EngineIdentity, ReceiptError, ReleaseFixture, SchemaInput,
};
use serde_json::{json, Value as Json};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

const IDENTITY: &str = "cic-primitive-forms-rust";

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn read(path: &str) -> Json {
    serde_json::from_str(&fs::read_to_string(repo_root().join(path)).unwrap()).unwrap()
}

#[test]
fn dump_receipt_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_RECEIPT_IN"),
        std::env::var_os("PARITY_RECEIPT_OUT"),
    ) else {
        return;
    };
    let defaults = read("testdata/p1/defaults.json");
    let grammar = defaults["grammar"].as_str().unwrap();
    let a: Vec<String> = defaults["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    let fixture = read("testdata/p5/receipt.json");
    let text = |k: &str| fixture["release"][k].as_str().unwrap().to_string();
    let release = ReleaseFixture {
        primitive_release: text("primitive_release"),
        primitive_source_commit: text("primitive_source_commit"),
        primitive_build_hash: text("primitive_build_hash"),
        grammar_sha256: text("grammar_sha256"),
        grammar_schema_sha256: text("grammar_schema_sha256"),
    };
    let engine = EngineIdentity {
        identity: IDENTITY.to_string(),
        version: fixture["engine_version"].as_str().unwrap().to_string(),
    };
    let mut out = String::new();
    for line in fs::read_to_string(input).expect("input").lines() {
        let case: Json = serde_json::from_str(line).expect("a JSON line");
        let schema = case["schema"].as_str().unwrap();
        let doc = parse(case["doc"].as_str().unwrap())
            .unwrap_or_else(|e| panic!("#{}: the document does not parse: {e:?}", case["id"]));
        let caps: BTreeMap<String, String> = case["caps"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        let input = SchemaInput {
            bytes: schema.as_bytes(),
            sha256: &sha256_hex(schema.as_bytes()),
            grammar,
            surface: "config_surface",
        };
        let r = match receipt_flat_fixture(&input, Some(&doc), &a, &caps, &release, &engine) {
            Ok(r) => json!([
                "ok",
                r.document,
                r.authored_input,
                r.binding,
                r.materialized.expected_keys,
                r.materialized.projection_json
            ]),
            Err(ReceiptError::Materialize(e)) => json!(["err", e.reason, e.field]),
            Err(ReceiptError::Refused { reason, field }) => json!(["err", reason, field]),
            Err(e) => panic!(
                "#{}: an error outside the contract of the cases: {e:?}",
                case["id"]
            ),
        };
        out += &json!({"id": case["id"], "r": r}).to_string();
        out.push('\n');
    }
    fs::write(output, out).expect("output");
}
