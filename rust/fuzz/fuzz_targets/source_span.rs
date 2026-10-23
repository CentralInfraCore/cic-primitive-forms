//! Fuzzing of `source_span` (P3b, step E2). The Go peer is `FuzzSourceSpan` in go/fuzz_test.go.
//!
//! On every input that parses: for each scalar position of a fixed shape the call returns a span or a refusal of the contract and never panics;
//! a span lies inside the text, on character boundaries, holds no line break and reads back to a scalar; and the read-back never has to
//! refuse (`EDIT_SPAN_MISMATCH` is a defect of the scanner). `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{parse, source_span, Error, Presence, ScalarType, Shape, ShapeType};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType) -> Shape {
    Shape { shape_type, presence: Presence::Optional, nullable: false, has_default: false, default: None }
}

fn fuzz_shape() -> Shape {
    shape(ShapeType::Composite(vec![
        ("a".into(), shape(ShapeType::Scalar(ScalarType::String))),
        ("b".into(), shape(ShapeType::Scalar(ScalarType::Integer))),
        (
            "c".into(),
            shape(ShapeType::Composite(vec![
                ("d".into(), shape(ShapeType::Scalar(ScalarType::String))),
                ("e".into(), shape(ShapeType::Scalar(ScalarType::Boolean))),
            ])),
        ),
    ]))
}

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && text.starts_with("CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    if parse(text).is_err() {
        return;
    }
    let a: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"].iter().map(|k| (*k).to_string()).collect();
    let root = fuzz_shape();
    for path in ["$.a", "$.b", "$.c", "$.c.d", "$.c.e"] {
        match source_span(text, &root, path, &a) {
            Ok(span) => {
                assert!(span.start < span.end && span.end <= text.len(), "{path}: a span outside the text in {text:?}");
                assert!(text.is_char_boundary(span.start) && text.is_char_boundary(span.end), "{path}: not on character boundaries in {text:?}");
                let token = &text[span.start..span.end];
                assert!(!token.contains('\n') && !token.contains('\r'), "{path}: a line break in the span {token:?}");
                assert!(parse(token).is_ok(), "{path}: the span {token:?} does not parse");
            }
            Err(Error::Contract(vs)) => assert_ne!(vs[0].code, "EDIT_SPAN_MISMATCH", "{path}: the read-back refused in {text:?}"),
            Err(_) => {}
        }
    }
});
