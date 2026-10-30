//! Fuzzing of `materialize_flat` (P5-2). The Go peer is `FuzzMaterialize` in go/fuzz_test.go.
//!
//! The input is YAML text, the document; the schema (a flat one: a mandatory string, optional fields with and without a default, one nullable) and the capability map are
//! fixed and complete, so no refusal of the schema or of the capabilities can happen. On every input that parses: the input is left alone; a refusal is one of the three
//! the document can cause (`invalid_document`, `unsupported`, `omitted_optional`), and `omitted_optional` names a declared optional field with no default that the document
//! does not write; an acceptance has one field per declared name, in byte order, the expected keys are the declared names in byte order, a field is `authored` exactly when
//! the document writes it (with its value) and `schema_default` with the declared default when it does not, the projection is one line that holds every key and the digest is
//! that of its bytes; the same document gives the same answer twice, and the same document written as YAML (`emit`) and read again gives the same answer.
//! `CIC_FUZZ_CANARY` works as in the `parse` target.

#![no_main]

use std::collections::BTreeMap;

use cic_primitive_forms::{
    canonical_digest, emit, materialize_flat, parse, DefaultValue, Node, Presence, ScalarType, Shape, ShapeType, Value,
};
use libfuzzer_sys::fuzz_target;

fn scalar(t: ScalarType, presence: Presence, nullable: bool, default: Option<Value>) -> Shape {
    Shape {
        shape_type: ShapeType::Scalar(t),
        presence,
        nullable,
        has_default: default.is_some(),
        default: default.map(|d| DefaultValue(Node::new(d))),
    }
}

/// The declared fields, in schema order, with their shape. The schema order is NOT the byte order of the names (U+1F600, U+E9, d, U+E000, a, c, b), so a materializer
/// that forgets to sort the fields, or the expected keys, is seen.
fn fields() -> Vec<(String, Shape)> {
    vec![
        ("\u{1f600}".into(), scalar(ScalarType::Integer, Presence::Optional, false, Some(Value::Int(-1)))),
        ("\u{e9}".into(), scalar(ScalarType::String, Presence::Optional, false, Some(Value::Str("\u{fc}".into())))),
        ("d".into(), scalar(ScalarType::String, Presence::Optional, false, None)),
        ("\u{e000}".into(), scalar(ScalarType::String, Presence::Optional, true, Some(Value::Str("<&>".into())))),
        ("a".into(), scalar(ScalarType::String, Presence::Mandatory, false, None)),
        ("c".into(), scalar(ScalarType::Boolean, Presence::Optional, false, Some(Value::Bool(true)))),
        ("b".into(), scalar(ScalarType::Integer, Presence::Optional, true, Some(Value::Int(7)))),
    ]
}

const REFUSALS: [&str; 3] = ["invalid_document", "unsupported", "omitted_optional"];

type Answer = Result<(Vec<String>, String, String), (String, String)>;

fn answer(doc: &Node, root: &Shape, a: &[String], caps: &BTreeMap<String, String>) -> Answer {
    match materialize_flat(Some(doc), root, a, caps) {
        Ok(m) => Ok((m.expected_keys, m.projection_json, m.output_digest)),
        Err(e) => Err((e.reason.to_string(), e.field)),
    }
}

fuzz_target!(|data: &[u8]| {
    if std::env::var_os("CIC_FUZZ_CANARY").is_some() && data.starts_with(b"CANARY") {
        panic!("canary: the fuzz job reports a failure");
    }
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let Ok(doc) = parse(text) else {
        return;
    };
    let before = doc.clone();
    let declared = fields();
    let root = Shape {
        shape_type: ShapeType::Composite(declared.clone()),
        presence: Presence::Optional,
        nullable: false,
        has_default: false,
        default: None,
    };
    let a: Vec<String> = ["value", "access", "modify", "inherit", "default_injection", "conformance"]
        .iter()
        .map(|k| (*k).to_string())
        .collect();
    let caps: BTreeMap<String, String> = declared.iter().map(|(n, _)| (n.clone(), "implemented".to_string())).collect();
    let first = answer(&doc, &root, &a, &caps);
    assert!(doc.same(&before), "materialize_flat changed its input {text:?}");
    assert_eq!(first, answer(&doc, &root, &a, &caps), "the same document gave another answer {text:?}");
    let again = parse(&emit(&doc)).expect("an emitted document reads again");
    assert_eq!(first, answer(&again, &root, &a, &caps), "emit and parse changed the answer {text:?}");
    let written: BTreeMap<&str, &Node> = match &doc.value {
        Value::Map(entries) => entries.iter().map(|e| (e.key.as_str(), &e.value)).collect(),
        _ => BTreeMap::new(),
    };
    match first {
        Err((reason, field)) => {
            assert!(REFUSALS.contains(&reason.as_str()), "a refusal that a fixed schema cannot cause: {reason} {text:?}");
            if reason == "omitted_optional" {
                let (_, shape) = declared.iter().find(|(n, _)| *n == field).expect("a declared field");
                assert!(!shape.has_default && shape.presence == Presence::Optional, "omitted_optional names {field:?} {text:?}");
                assert!(!written.contains_key(field.as_str()), "omitted_optional names a field the document writes {text:?}");
            }
        }
        Ok((keys, projection, digest)) => {
            let mut names: Vec<String> = declared.iter().map(|(n, _)| n.clone()).collect();
            names.sort();
            assert_eq!(keys, names, "the expected keys are the declared names in byte order {text:?}");
            assert!(!projection.contains(['\n', '\r', '\t']), "white space in the projection {text:?}");
            assert_eq!(digest, canonical_digest(projection.as_bytes()), "the digest is not that of the projection {text:?}");
            let m = materialize_flat(Some(&doc), &root, &a, &caps).expect("accepted twice");
            assert_eq!(m.fields.len(), declared.len());
            for (field, name) in m.fields.iter().zip(&names) {
                assert_eq!(&field.key, name, "the fields are in byte order of the key {text:?}");
                assert!(projection.contains(&format!("\"key\":\"{name}\"")), "a key is missing from the projection {text:?}");
                let shape = &declared.iter().find(|(n, _)| n == name).expect("declared").1;
                match written.get(name.as_str()) {
                    Some(v) => {
                        assert_eq!(field.provenance, "authored", "{name:?} {text:?}");
                        assert!(field.value.same(v), "an authored value was changed {name:?} {text:?}");
                    }
                    None => {
                        assert_eq!(field.provenance, "schema_default", "{name:?} {text:?}");
                        let want = &shape.default.as_ref().expect("a field the document leaves out has a default").0;
                        assert!(field.value.same(want), "a default was changed {name:?} {text:?}");
                    }
                }
            }
        }
    }
});
