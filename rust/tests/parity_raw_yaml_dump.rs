//! Dumps what `parse` and `map_release` answer for the RAW BYTES of the cases of `tools/parity/gen_raw_yaml.py` (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b).
//! `PARITY_RAW_YAML_IN` is a JSON-lines file of cases ({"id", "bytes_hex", "pin", ...}); `PARITY_RAW_YAML_OUT` receives exactly one JSON line per input line, in order. Without the
//! two variables this test does nothing. The Go peer is `go/parity_raw_yaml_dump_test.go`; `tools/parity/compare_raw_yaml.py` compares the readers.
//!
//! A line is `{"id", "ok": true, "tree", "mapping"}` or `{"id", "ok": false, "class", "code"}`. The bytes are decoded as UTF-8 here, because the parser takes text: a byte
//! sequence that is not UTF-8 is answered `invalid_utf8` before the parser sees it, and the line says so. Every case runs in a thread with a wall-clock limit (`PARITY_RAW_YAML_TIMEOUT`,
//! seconds, default 10): a case that does not answer in time is written as `TIMEOUT` and the run goes on. The memory limit is set by the runner on the whole process.

use cic_primitive_forms::{map_release, parse, Entry, Error, Node, Value};
use serde_json::{json, Value as J};
use std::collections::BTreeMap;
use std::sync::mpsc;
use std::time::Duration;

fn tree(n: &Node) -> J {
    match &n.value {
        Value::Null => json!({"k": "null", "v": J::Null}),
        Value::Bool(b) => json!({"k": "bool", "v": b}),
        Value::Int(i) => json!({"k": "int", "v": i}),
        Value::Str(s) => json!({"k": "str", "v": s}),
        Value::Unsupported(s) => json!({"k": "unsupported", "v": s}),
        Value::Seq(items) => json!({"k": "seq", "v": items.iter().map(tree).collect::<Vec<_>>()}),
        Value::Map(entries) => {
            json!({"k": "map", "v": entries.iter().map(|e: &Entry| json!([e.key, tree(&e.value)])).collect::<Vec<_>>()})
        }
    }
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
        .collect()
}

/// The class of a refusal, from the codes of the parser: `duplicate_key` when every violation is one, `syntax` for the syntax, unsupported-syntax and limit errors, else `other` with the code.
fn refusal(e: &Error) -> (String, String) {
    match e {
        Error::Contract(vs) => {
            let all_dup = vs.iter().all(|v| v.code == "DUPLICATE_KEY");
            (
                if all_dup { "duplicate_key" } else { "other" }.to_string(),
                vs.iter().map(|v| v.code).collect::<Vec<_>>().join(","),
            )
        }
        Error::Unsupported(_) | Error::Syntax(_) | Error::Limit(_) => {
            ("syntax".to_string(), e.code().to_string())
        }
        _ => ("other".to_string(), e.code().to_string()),
    }
}

fn answer(id: &str, bytes: &[u8], pin: &J) -> J {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return json!({"id": id, "ok": false, "class": "invalid_utf8", "code": "INVALID_UTF8"});
    };
    match parse(text) {
        Err(e) => {
            let (class, code) = refusal(&e);
            json!({"id": id, "ok": false, "class": class, "code": code})
        }
        Ok(node) => {
            let mapping = if pin.is_object() {
                let pin: BTreeMap<String, String> = pin
                    .as_object()
                    .expect("pin")
                    .iter()
                    .map(|(k, v)| (k.clone(), v.as_str().expect("pin value").to_string()))
                    .collect();
                match map_release(&node, &pin) {
                    Ok(m) => {
                        json!({"ok": {"primitive_build_hash": m.primitive_build_hash, "grammar_sha256": m.grammar_sha256, "grammar_schema_sha256": m.grammar_schema_sha256}})
                    }
                    Err(e) => {
                        json!({"refuse": e.reason, "field": if e.field.is_empty() { J::Null } else { J::String(e.field) }})
                    }
                }
            } else {
                J::Null
            };
            json!({"id": id, "ok": true, "tree": tree(&node), "mapping": mapping})
        }
    }
}

#[test]
fn dump_raw_yaml_outcomes() {
    let (Some(input), Some(output)) = (
        std::env::var_os("PARITY_RAW_YAML_IN"),
        std::env::var_os("PARITY_RAW_YAML_OUT"),
    ) else {
        return;
    };
    let limit = Duration::from_secs(
        std::env::var("PARITY_RAW_YAML_TIMEOUT")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(10),
    );
    let mut out = String::new();
    for line in std::fs::read_to_string(input).expect("input").lines() {
        if line.trim().is_empty() {
            continue;
        }
        let c: J = serde_json::from_str(line).expect("a case");
        let id = c["id"].as_str().expect("id").to_string();
        let bytes = unhex(c["bytes_hex"].as_str().expect("bytes_hex"));
        let pin = c["pin"].clone();
        let (tx, rx) = mpsc::channel();
        let worker_id = id.clone();
        std::thread::Builder::new()
            .stack_size(64 * 1024 * 1024)
            .spawn(move || {
                let _ = tx.send(answer(&worker_id, &bytes, &pin));
            })
            .expect("thread");
        let result = rx.recv_timeout(limit).unwrap_or_else(
            |_| json!({"id": id, "ok": false, "class": "TIMEOUT", "code": "TIMEOUT"}),
        );
        out.push_str(&result.to_string());
        out.push('\n');
    }
    std::fs::write(output, out).expect("write dump");
}
