//! Fuzzing of `patch` (P4, step P4-3). The Go peer is `FuzzPatch` in go/fuzz_test.go.
//!
//! The input is two texts, separated by the first NUL byte. When both parse, the diff of A to B is applied to the text of A: the patch is a result or a refusal of the
//! contract and never a panic, never a conflict or an absence (the diff was made from the same text); a result parses, the diff of the result to B has no changes
//! (ADR 0013, S+), the number of replaced scalars is the number of events, the same diff on the result is refused, and the empty diff gives A back.
//! `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use cic_primitive_forms::{
    bind_grammar, diff, parse, patch, Case, Context, DiffContext, DiffView, Elements, Error, PatchDiff, PatchEvent, Presence, ScalarType, Shape,
    ShapeType, Value,
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
    let ctx = DiffContext::default();
    let view = |doc| DiffView { doc, context: DiffContext::default(), trust: Context::default() };
    let Ok(d) = diff(&view(&da), &view(&db), &root, &keys, g, None) else {
        return;
    };
    let pd = PatchDiff { version: 1, changes: d.changes.iter().map(PatchEvent::from).collect() };
    let res = match patch(a_text, &root, &keys, g, &ctx, &ctx, &pd) {
        Ok(r) => r,
        Err(Error::Contract(vs)) => {
            assert!(
                !matches!(vs[0].code, "PATCH_CONFLICT" | "PATCH_TARGET_ABSENT" | "PATCH_CONTEXT_MISMATCH" | "EDIT_SPAN_MISMATCH" | "EDIT_INVALID_RESULT"),
                "{} for a diff made from the same text: {a_text:?} -> {b_text:?}",
                vs[0].code
            );
            return;
        }
        Err(_) => return,
    };
    let patched = parse(&res.text).unwrap_or_else(|e| panic!("the result does not parse: {e:?} in {:?}", res.text));
    let rt = diff(&view(&patched), &view(&db), &root, &keys, g, None).unwrap_or_else(|e| panic!("the diff of the result to B fails: {e:?}"));
    assert!(rt.changes.is_empty(), "the diff of the result to B has changes: {a_text:?} -> {b_text:?} gives {:?}", res.text);
    assert_eq!(res.replaced.len(), d.changes.len(), "replaced {:?}", res.replaced);
    assert!(d.changes.is_empty() || patch(&res.text, &root, &keys, g, &ctx, &ctx, &pd).is_err(), "a replay was accepted: {:?}", res.text);
    let empty = patch(a_text, &root, &keys, g, &ctx, &ctx, &PatchDiff { version: 1, changes: vec![] }).expect("the empty diff");
    assert_eq!(empty.text, a_text, "the empty diff did not give A back");
});
