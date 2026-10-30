//! Fuzzing of `canonical_bytes` (P5-1). The Go peer is `FuzzCanonical` in go/fuzz_test.go.
//!
//! The input is YAML text. When `parse` reads it, the value is within the range of the canonical bytes (the parser refuses a float, an integer beyond 64 bits and a
//! repeated key), so `canonical_bytes` must not refuse it; the bytes are valid UTF-8, hold no raw newline, carriage return or tab (a string escapes them), and carry the digest of
//! A7; the same value written as YAML (`emit`) and read again has the same bytes. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{canonical_bytes, canonical_digest, emit, parse};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && data.starts_with(b"CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let Ok(node) = parse(text) else {
        return;
    };
    let bytes = canonical_bytes(&node).expect("a parsed value has canonical bytes");
    let written = std::str::from_utf8(&bytes).expect("canonical bytes are UTF-8");
    assert!(!written.contains(['\n', '\r', '\t']), "white space in the canonical bytes");
    assert!(canonical_digest(&bytes).starts_with("sha256:"));
    assert_eq!(canonical_digest(&bytes).len(), 7 + 64);
    let again = parse(&emit(&node)).expect("an emitted document reads again");
    assert_eq!(canonical_bytes(&again).expect("canonical"), bytes, "emit and parse changed the value");
});
