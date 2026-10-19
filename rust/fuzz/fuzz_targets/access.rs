//! Fuzzing of the reading of access-policy/1 values. The Go peer is `FuzzAccess` in go/fuzz_test.go.
//!
//! On every input that parses: reading an `access`/`modify` value and an InheritanceMode under both
//! grammars does not panic, leaves the document alone, answers with a known code, writes back the
//! spelling it read, and accepts only valid CertPatterns. `CIC_FUZZ_CANARY` works as in `parse`.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, is_cert_pattern, parse, read_mode, read_rule, write_mode, Rule, Value,
    SUPPORTED_GRAMMARS,
};
use libfuzzer_sys::fuzz_target;

const KNOWN: [&str; 5] = [
    "INVALID_INHERIT",
    "INHERIT_ENUM_IN_GRAMMAR_1",
    "LEGACY_INHERIT_IN_GRAMMAR_2",
    "INVALID_ACCESS_VALUE",
    "INVALID_CERT_PATTERN",
];

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && text.starts_with("CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let Ok(n) = parse(text) else {
        return;
    };
    let before = n.clone();
    match read_rule(&n) {
        Ok(Rule::List(items)) => assert!(items.iter().all(|p| is_cert_pattern(p)), "{text:?}"),
        Ok(Rule::Symbol) => {}
        Err(v) => assert!(KNOWN.contains(&v.code), "{} for {text:?}", v.code),
    }
    for version in SUPPORTED_GRAMMARS {
        let g = bind_grammar(Some(&Value::Int(version)), Some(&Value::Int(version))).unwrap();
        match read_mode(&n, g) {
            Ok(mode) => assert!(write_mode(mode, g).same(&n), "{text:?} lost its spelling"),
            Err(v) => assert!(KNOWN.contains(&v.code), "{} for {text:?}", v.code),
        }
    }
    assert!(n.same(&before), "reading changed its input {text:?}");
});
