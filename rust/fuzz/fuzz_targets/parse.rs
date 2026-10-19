//! Parser fuzzing (PHASES.md: required from P1). The Go peer is `FuzzParse` in go/fuzz_test.go.
//!
//! On every input: `parse` does not panic, a document that parses survives `emit` then `parse`
//! with the same value, and its positions lie inside the text.
//!
//! `CIC_FUZZ_CANARY` is the proof that the fuzz job can fail: with it set, an input starting
//! with `CANARY` panics, and CI requires that run to be red. The product has no such switch.

#![no_main]

use cic_primitive_forms::{emit, parse, Node, Value};
use libfuzzer_sys::fuzz_target;

fn check_positions(n: &Node, len: usize) {
    if let Some(p) = n.pos {
        assert!(p.line >= 1 && p.column >= 1 && p.offset <= len, "{p:?}");
    }
    match &n.value {
        Value::Seq(items) => items.iter().for_each(|i| check_positions(i, len)),
        Value::Map(entries) => entries.iter().for_each(|e| check_positions(&e.value, len)),
        _ => {}
    }
}

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && text.starts_with("CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    if let Ok(n) = parse(text) {
        check_positions(&n, text.len());
        let back = parse(&emit(&n)).expect("emitted text parses");
        assert!(back.same(&n), "emit/parse changed the value of {text:?}");
    }
});
