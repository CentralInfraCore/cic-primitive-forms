//! Dumps what `parse` and `map_release` answer for the RAW BYTES of the cases of `tools/parity/gen_raw_yaml.py` (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b).
//! `PARITY_RAW_YAML_IN` is a JSON-lines file of cases ({"id", "bytes_hex", "pin", ...}); `PARITY_RAW_YAML_OUT` receives exactly one JSON line per input line, in order. Without the
//! two variables this test does nothing. The Go peer is `go/parity_raw_yaml_dump_test.go`; `tools/parity/compare_raw_yaml.py` compares the readers.
//!
//! A line is `{"id", "ok": true, "tree", "positions", "mapping"}` or `{"id", "ok": false, "class", "code", "locations"}`; `positions` lists the source position of every node and every mapping key in
//! document order, `locations` the location of every violation (semantic parity and position parity are judged apart by the compare). The bytes are decoded as UTF-8 here, because the parser takes text: a byte
//! sequence that is not UTF-8 is answered `invalid_utf8` before the parser sees it, and the line says so. Each line is written and flushed as soon as the
//! case is answered. There is NO timer in this process: a case that does not answer is killed, with its whole process, by the runner (`tools/parity/run_raw_yaml.py`), which also sets the memory
//! limit; the runner then records TIMEOUT or CRASH for that case and restarts after it.

use cic_primitive_forms::{map_release, parse, Error, Location, Node, Pos, Value, Violation};
use serde_json::{json, Value as J};
use std::collections::BTreeMap;
use std::io::Write;

fn tree(n: &Node) -> J {
    match &n.value {
        Value::Null => json!({"k": "null", "v": J::Null}),
        Value::Bool(b) => json!({"k": "bool", "v": b}),
        Value::Int(i) => json!({"k": "int", "v": i}),
        Value::Str(s) => json!({"k": "str", "v": s}),
        Value::Unsupported(s) => json!({"k": "unsupported", "v": s}),
        Value::Seq(items) => json!({"k": "seq", "v": items.iter().map(tree).collect::<Vec<_>>()}),
        Value::Map(entries) => {
            json!({"k": "map", "v": entries.iter().map(|e| json!([e.key, tree(&e.value)])).collect::<Vec<_>>()})
        }
    }
}

fn pos(p: Option<Pos>) -> J {
    p.map_or(J::Null, |p| json!([p.line, p.column, p.offset]))
}

/// The position of every node, and of every mapping key before its value, in document order.
fn positions(n: &Node, out: &mut Vec<J>) {
    out.push(pos(n.pos));
    match &n.value {
        Value::Seq(items) => items.iter().for_each(|i| positions(i, out)),
        Value::Map(entries) => {
            for e in entries {
                out.push(pos(e.key_pos));
                positions(&e.value, out);
            }
        }
        _ => {}
    }
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
        .collect()
}

fn location(v: &Violation) -> J {
    match v.location {
        Location::At(p) => json!([p.line, p.column, p.offset]),
        Location::Unavailable | Location::NotApplicable => J::Null,
    }
}

/// The class of a refusal, from the codes of the parser: `duplicate_key` when every violation is one, `syntax` for the syntax, unsupported-syntax and limit errors, else `other` with the code.
fn refusal(e: &Error) -> (String, String, Vec<J>) {
    match e {
        Error::Contract(vs) => {
            let all_dup = vs.iter().all(|v| v.code == "DUPLICATE_KEY");
            (
                if all_dup { "duplicate_key" } else { "other" }.to_string(),
                vs.iter().map(|v| v.code).collect::<Vec<_>>().join(","),
                vs.iter().map(location).collect(),
            )
        }
        Error::Unsupported(_) | Error::Syntax(_) | Error::Limit(_) => {
            ("syntax".to_string(), e.code().to_string(), vec![])
        }
        _ => ("other".to_string(), e.code().to_string(), vec![]),
    }
}

fn answer(id: &str, bytes: &[u8], pin: &J) -> J {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return json!({"id": id, "ok": false, "class": "invalid_utf8", "code": "INVALID_UTF8", "locations": []});
    };
    match parse(text) {
        Err(e) => {
            let (class, code, locations) = refusal(&e);
            json!({"id": id, "ok": false, "class": class, "code": code, "locations": locations})
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
            let mut ps = Vec::new();
            positions(&node, &mut ps);
            json!({"id": id, "ok": true, "tree": tree(&node), "positions": ps, "mapping": mapping})
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
    let mut out = std::fs::File::create(output).expect("output");
    for line in std::fs::read_to_string(input).expect("input").lines() {
        if line.trim().is_empty() {
            continue;
        }
        let c: J = serde_json::from_str(line).expect("a case");
        let id = c["id"].as_str().expect("id").to_string();
        let bytes = unhex(c["bytes_hex"].as_str().expect("bytes_hex"));
        let pin = c["pin"].clone();
        // the parser runs on a thread with a large stack and is JOINED: no timer, no abandoned work; the runner kills the process of a case that does not come back
        let result = std::thread::Builder::new()
            .stack_size(64 * 1024 * 1024)
            .spawn(move || answer(&id, &bytes, &pin))
            .expect("thread")
            .join()
            .expect("the parser thread panicked");
        writeln!(out, "{result}").expect("write");
        out.flush().expect("flush");
    }
}
