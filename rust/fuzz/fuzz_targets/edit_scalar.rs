//! Fuzzing of `edit_scalar` (P3b, step E3). The Go peer is `FuzzEditScalar` in go/fuzz_test.go.
//!
//! The input is a text and a value, separated by the first NUL byte. On every text that parses: for each scalar position of a fixed shape and a
//! value of the kind the position has, the call returns an edit or a refusal of the contract and never panics; a refusal is never
//! `EDIT_SPAN_MISMATCH` or `EDIT_INVALID_RESULT` (here a defect); an edit keeps the bytes outside the span, the result parses, and an unchanged edit
//! is the input byte for byte. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{edit_scalar, parse, source_span, Error, Node, Presence, ScalarType, Shape, ShapeType, Value};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType, nullable: bool) -> Shape {
    Shape { shape_type, presence: Presence::Optional, nullable, has_default: false, default: None }
}

fn fuzz_shape() -> Shape {
    shape(
        ShapeType::Composite(vec![
            ("a".into(), shape(ShapeType::Scalar(ScalarType::String), true)),
            ("b".into(), shape(ShapeType::Scalar(ScalarType::Integer), false)),
            (
                "c".into(),
                shape(
                    ShapeType::Composite(vec![
                        ("d".into(), shape(ShapeType::Scalar(ScalarType::String), true)),
                        ("e".into(), shape(ShapeType::Scalar(ScalarType::Boolean), false)),
                    ]),
                    false,
                ),
            ),
        ]),
        false,
    )
}

fuzz_target!(|data: &[u8]| {
    let (text_bytes, val_bytes) = data.split_at(data.iter().position(|b| *b == 0).unwrap_or(data.len()));
    let (Ok(text), Ok(val)) = (std::str::from_utf8(text_bytes), std::str::from_utf8(val_bytes.get(1..).unwrap_or_default())) else {
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
    let values = [
        ("$.a", Node::new(Value::Str(val.to_string()))),
        ("$.b", Node::new(Value::Int(i64::try_from(val.len()).unwrap_or(0) - 3))),
        ("$.c.d", Node::new(Value::Str(val.to_string()))),
        ("$.c.e", Node::new(Value::Bool(val.len() % 2 == 0))),
    ];
    for (path, value) in &values {
        let span = source_span(text, &root, path, &a);
        match edit_scalar(text, &root, path, &a, value) {
            Err(Error::Contract(vs)) => assert!(
                !matches!(vs[0].code, "EDIT_SPAN_MISMATCH" | "EDIT_INVALID_RESULT"),
                "{path}: {} for {text:?} <- {val:?}",
                vs[0].code
            ),
            Err(_) => {}
            Ok(res) => {
                let span = span.unwrap_or_else(|e| panic!("{path}: an edit where the span was refused: {e:?} in {text:?}"));
                assert!(
                    res.text.starts_with(&text[..span.start]) && res.text.ends_with(&text[span.end..]),
                    "{path}: the untouched bytes changed: {text:?} -> {:?}",
                    res.text
                );
                assert!(res.changed || res.text == text, "{path}: an unchanged edit rewrote {text:?}");
                assert!(parse(&res.text).is_ok(), "{path}: the result does not parse: {:?}", res.text);
            }
        }
    }
});
