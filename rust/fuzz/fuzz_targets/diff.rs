//! Fuzzing of `diff` (P4, step D3b). The Go peer is `FuzzDiff` in go/fuzz_test.go.
//!
//! The input is two texts, separated by the first NUL byte. When both parse, the diff of the pair is a result or a refusal of the contract and never a
//! panic, never `DIFF_UNSUPPORTED_VALUE` (a parsed text holds no such number); when it is a result, B to A is a result with as many events, A against A
//! is empty, and the bytes are the canonical writing. `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, diff, parse, Case, Context, DiffContext, DiffView, Elements, Error, Node, Presence, ScalarType, Shape, ShapeType,
    Value,
};
use libfuzzer_sys::fuzz_target;

fn shape(shape_type: ShapeType, nullable: bool, mandatory: bool) -> Shape {
    Shape {
        shape_type,
        presence: if mandatory { Presence::Mandatory } else { Presence::Optional },
        nullable,
        has_default: false,
        default: None,
    }
}

fn fuzz_shape() -> Shape {
    let string = |nullable| shape(ShapeType::Scalar(ScalarType::String), nullable, false);
    shape(
        ShapeType::Composite(vec![
            ("a".into(), string(true)),
            ("b".into(), shape(ShapeType::Scalar(ScalarType::Integer), false, false)),
            (
                "c".into(),
                shape(
                    ShapeType::Composite(vec![
                        ("d".into(), string(true)),
                        ("e".into(), shape(ShapeType::Scalar(ScalarType::Boolean), false, false)),
                    ]),
                    false,
                    false,
                ),
            ),
            ("s".into(), shape(ShapeType::Set { item: ScalarType::String, min_items: None }, false, false)),
            (
                "l".into(),
                shape(
                    ShapeType::DeclaredList {
                        min_items: None,
                        elements: Elements {
                            choice: false,
                            cases: vec![Case {
                                name: "e".into(),
                                key: vec!["k".into()],
                                fields: vec![
                                    ("k".into(), shape(ShapeType::Scalar(ScalarType::String), false, true)),
                                    ("v".into(), shape(ShapeType::Scalar(ScalarType::Integer), false, false)),
                                ],
                            }],
                        },
                    },
                    false,
                    false,
                ),
            ),
        ]),
        false,
        true,
    )
}

fuzz_target!(|data: &[u8]| {
    let (a_bytes, b_bytes) = data.split_at(data.iter().position(|b| *b == 0).unwrap_or(data.len()));
    let (Ok(a_text), Ok(b_text)) = (std::str::from_utf8(a_bytes), std::str::from_utf8(b_bytes.get(1..).unwrap_or_default())) else {
        return;
    };
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && a_text.starts_with("CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let (Ok(da), Ok(db)) = (parse(a_text), parse(b_text)) else {
        return;
    };
    let keys: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"].iter().map(|k| (*k).to_string()).collect();
    let one = Value::Int(1);
    let g = bind_grammar(Some(&one), Some(&one)).expect("grammar 1");
    let root = fuzz_shape();
    let run = |x: &Node, y: &Node| {
        let view = |doc| DiffView { doc, context: DiffContext::default(), trust: Context::default() };
        diff(&view(x), &view(y), &root, &keys, g, None)
    };
    let ab = match run(&da, &db) {
        Ok(r) => r,
        Err(Error::Contract(vs)) => {
            assert!(vs[0].code != "DIFF_UNSUPPORTED_VALUE", "an unsupported number from a parsed text: {a_text:?} {b_text:?}");
            return;
        }
        Err(_) => return,
    };
    let ba = run(&db, &da).unwrap_or_else(|e| panic!("A to B was a result and B to A is {e:?}: {a_text:?} {b_text:?}"));
    assert!(
        ab.changes.len() == ba.changes.len() && ab.representation.len() == ba.representation.len(),
        "the diff turned round has other events: {a_text:?} {b_text:?}"
    );
    let aa = run(&da, &da).unwrap_or_else(|e| panic!("A against A: {e:?} {a_text:?}"));
    assert!(aa.changes.is_empty() && aa.representation.is_empty(), "A against A is not empty: {a_text:?}");
    let bytes = ab.canonical();
    assert!(bytes.starts_with("{\"changes\":[") && bytes.ends_with("\"version\":1}"), "not the canonical writing: {bytes:?}");
});
