//! Dumps the outcome of `parse` for a list of inputs, for the Rust/Go differential (`tools/parity`).
//!
//! `PARITY_IN` is a file with one input per line, hex-encoded UTF-8 or arbitrary bytes; `PARITY_OUT`
//! receives one JSON line per input. Without the two variables this test does nothing, so it is part of
//! an ordinary `cargo test` run only as a compile check. The Go peer is `go/parity_dump_test.go`; the
//! two outputs are compared by `tools/parity/compare.py`.

use std::fs;

use cic_primitive_forms::{parse, Error, Location, Node, Value};
use serde_json::{json, Value as Json};

fn pos(p: Option<cic_primitive_forms::Pos>) -> Json {
    p.map_or(Json::Null, |p| json!([p.line, p.column, p.offset]))
}

fn tree(n: &Node) -> Json {
    let v = match &n.value {
        Value::Null => json!({"t": "null"}),
        Value::Bool(b) => json!({"t": "bool", "v": b}),
        Value::Int(i) => json!({"t": "int", "v": i}),
        Value::Str(s) => json!({"t": "str", "v": s}),
        Value::Seq(items) => json!({"t": "seq", "v": items.iter().map(tree).collect::<Vec<_>>()}),
        Value::Map(entries) => json!({
            "t": "map",
            "v": entries.iter().map(|e| json!([e.key, pos(e.key_pos), tree(&e.value)])).collect::<Vec<_>>()
        }),
    };
    json!({"n": v, "p": pos(n.pos)})
}

fn outcome(bytes: &[u8]) -> Json {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return json!({"err": "INVALID_UTF8"});
    };
    match parse(text) {
        Ok(n) => json!({"ok": tree(&n)}),
        Err(Error::Contract(vs)) => json!({"err": "CONTRACT", "viol": vs.iter().map(|v| json!({
            "code": v.code,
            "phase": v.phase.as_str(),
            "loc": match v.location {
                Location::At(p) => json!([p.line, p.column, p.offset]),
                Location::Unavailable => Json::Null,
            }
        })).collect::<Vec<_>>()}),
        Err(e) => json!({"err": e.code()}),
    }
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

#[test]
fn dump_parse_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_IN"),
        std::env::var_os("PARITY_OUT"),
    ) else {
        return;
    };
    let mut out = String::new();
    for line in fs::read_to_string(input).unwrap().lines() {
        out.push_str(&outcome(&unhex(line.trim())).to_string());
        out.push('\n');
    }
    fs::write(output, out).unwrap();
}

/// The shared regression corpus (`testdata/p1/parity.json`, built by `tools/parity/make_corpus.py`):
/// the outcome of `parse` for each input, pinned. The Go peer reads the same file and pins the same
/// outcomes, so a change on one side shows up as a failing pin on that side.
fn parity_corpus() -> Json {
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        std::path::PathBuf::from,
    );
    let text = fs::read_to_string(root.join("testdata/p1/parity.json")).expect("parity.json");
    serde_json::from_str(&text).expect("valid JSON")
}

#[test]
fn the_shared_regression_corpus_gives_the_pinned_outcomes() {
    let doc = parity_corpus();
    let cases = doc["cases"].as_array().expect("cases");
    assert!(cases.len() > 300, "the corpus is not empty");
    for c in cases {
        let input = c["input"].as_str().expect("input");
        let want = c.get("expect").unwrap_or(&c["rust"]);
        assert_eq!(&outcome(input.as_bytes()), want, "input {input:?}");
    }
}

/// An open difference is pinned with both outcomes and must stay a difference: when a fix makes the
/// two agree the pin fails and the case moves to `cases`.
#[test]
fn the_known_divergences_are_still_divergences() {
    let doc = parity_corpus();
    let known = doc["known_divergences"].as_array().expect("known");
    assert!(!known.is_empty());
    for k in known {
        let input = k["input"].as_str().expect("input");
        assert_eq!(outcome(input.as_bytes()), k["rust"], "input {input:?}");
        let both_refuse = k["rust"].get("err").is_some_and(|e| e != "CONTRACT")
            && k["go"].get("err").is_some_and(|e| e != "CONTRACT");
        assert!(k["rust"] != k["go"] && !both_refuse, "input {input:?}");
    }
}
