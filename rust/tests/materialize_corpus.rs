//! The shared flat-materialization cases (`testdata/p5/materialize.json`, written by `tools/p5/materialize_cases.py`, whose expected projections and digests
//! are computed by Python and not by this crate). The Go peer is `go/materialize_corpus_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    compile_schema, materialize_flat, parse, SchemaInput, MATERIALIZE_CAPABILITY_MISSING,
    MATERIALIZE_NOT_FLAT, MATERIALIZE_OMITTED_OPTIONAL, MATERIALIZE_SET,
};
use serde_json::Value as J;
use std::collections::BTreeMap;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn load() -> J {
    let text = std::fs::read_to_string(repo_root().join("testdata/p5/materialize.json")).unwrap();
    serde_json::from_str(&text).unwrap()
}

fn strings(v: &J) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect()
}

#[test]
fn the_shared_materialize_cases() {
    let file = load();
    let grammar = file["grammar"].as_str().unwrap();
    let a = strings(&file["access_keys"]);
    let mut accepted = 0;
    let mut refused = 0;
    for c in file["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let text = file["schemas"][c["schema"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let compiled = compile_schema(&SchemaInput {
            bytes: text.as_bytes(),
            sha256: &sha256_hex(text.as_bytes()),
            grammar,
            surface: "config_surface",
        })
        .unwrap_or_else(|e| panic!("{id}: {e:?}"));
        let doc = parse(c["yaml"].as_str().unwrap()).unwrap();
        let caps: BTreeMap<String, String> = c["capabilities"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        let got = materialize_flat(Some(&doc), &compiled.root, &a, &caps);
        let expect = &c["expect"];
        if let Some(r) = expect.get("refused") {
            refused += 1;
            let err = got.expect_err(id);
            assert_eq!(
                vec![err.reason.to_string(), err.field.clone()],
                strings(r),
                "{id}"
            );
        } else {
            accepted += 1;
            let m = got.unwrap_or_else(|e| panic!("{id}: {e:?}"));
            assert_eq!(m.expected_keys, strings(&expect["expected_keys"]), "{id}");
            assert_eq!(
                m.projection_json,
                expect["projection"].as_str().unwrap(),
                "{id}"
            );
            assert_eq!(
                m.output_digest,
                expect["output_digest"].as_str().unwrap(),
                "{id}"
            );
        }
    }
    assert_eq!((accepted, refused), (7, 13));
}

#[test]
fn no_document_is_refused_and_the_reasons_are_the_named_ones() {
    let file = load();
    let text = file["schemas"]["flat"].as_str().unwrap();
    let compiled = compile_schema(&SchemaInput {
        bytes: text.as_bytes(),
        sha256: &sha256_hex(text.as_bytes()),
        grammar: file["grammar"].as_str().unwrap(),
        surface: "config_surface",
    })
    .unwrap();
    let caps: BTreeMap<String, String> = ["type", "enabled", "mtu", "label", "maybe"]
        .iter()
        .map(|k| ((*k).to_string(), "implemented".to_string()))
        .collect();
    let err = materialize_flat(None, &compiled.root, &[], &caps).unwrap_err();
    assert_eq!((err.reason, err.field.as_str()), ("no_document", ""));
    assert_eq!(err.to_string(), "not materialized: no_document ()");
    assert_eq!(MATERIALIZE_NOT_FLAT, "not_flat");
    assert_eq!(MATERIALIZE_SET, "set");
    assert_eq!(MATERIALIZE_OMITTED_OPTIONAL, "omitted_optional");
    assert_eq!(MATERIALIZE_CAPABILITY_MISSING, "capability_missing");
}

mod hand_built {
    use cic_primitive_forms::{
        materialize_flat, Entry, Node, Presence, ScalarType, Shape, ShapeType, Value,
    };
    use std::collections::BTreeMap;

    // a key set A that no document below uses, so that a mapping is a value and not an expanded Access atom
    const A: [&str; 1] = ["__a"];

    fn shape(shape_type: ShapeType) -> Shape {
        Shape {
            shape_type,
            presence: Presence::Mandatory,
            nullable: false,
            has_default: false,
            default: None,
        }
    }

    fn caps(names: &[&str]) -> BTreeMap<String, String> {
        names
            .iter()
            .map(|k| ((*k).to_string(), "implemented".to_string()))
            .collect()
    }

    fn root_of(shape_type: ShapeType) -> Shape {
        shape(ShapeType::Composite(vec![(
            "v".to_string(),
            shape(shape_type),
        )]))
    }

    fn doc(value: Value) -> Node {
        Node::new(Value::Map(vec![Entry {
            key: "v".to_string(),
            key_pos: None,
            value: Node::new(value),
        }]))
    }

    fn reason(root: &Shape, document: Option<&Node>) -> (&'static str, String) {
        let e = materialize_flat(document, root, &A.map(String::from), &caps(&["v"])).unwrap_err();
        (e.reason, e.field)
    }

    #[test]
    fn a_root_that_is_not_a_mapping_is_not_flat() {
        let root = shape(ShapeType::Scalar(ScalarType::String));
        assert_eq!(reason(&root, None), ("not_flat", String::new()));
    }

    #[test]
    fn an_opaque_value_that_is_a_mapping_or_a_sequence_is_not_flat_and_a_scalar_is_materialized() {
        let root = root_of(ShapeType::Opaque);
        let map = doc(Value::Map(vec![Entry {
            key: "k".to_string(),
            key_pos: None,
            value: Node::new(Value::Int(1)),
        }]));
        assert_eq!(reason(&root, Some(&map)), ("not_flat", "v".to_string()));
        let seq = doc(Value::Seq(vec![Node::new(Value::Int(1))]));
        assert_eq!(reason(&root, Some(&seq)), ("not_flat", "v".to_string()));
        let int = doc(Value::Int(7));
        let m = materialize_flat(Some(&int), &root, &A.map(String::from), &caps(&["v"])).unwrap();
        assert_eq!(
            m.projection_json,
            r#"{"fields":[{"capability":"implemented","key":"v","provenance":"authored","value":7}]}"#
        );
    }

    #[test]
    fn a_number_the_canonical_form_cannot_hold_is_refused_with_its_reason() {
        let root = root_of(ShapeType::Opaque);
        let float = doc(Value::Unsupported("1.5".to_string()));
        assert_eq!(
            reason(&root, Some(&float)),
            ("unsupported_value", String::new())
        );
    }
}

mod unwritable_name {
    use cic_primitive_forms::{
        materialize_flat, Entry, Node, Presence, ScalarType, Shape, ShapeType, Value,
    };
    use std::collections::BTreeMap;

    // A field whose name cannot be written as a path (here the empty name) has no origin of its own: two such fields would share the key "" and the provenance
    // of the second would be read from the first. It is refused, with the field named.
    #[test]
    fn a_field_name_that_cannot_be_a_path_is_refused() {
        let leaf = Shape {
            shape_type: ShapeType::Scalar(ScalarType::String),
            presence: Presence::Mandatory,
            nullable: false,
            has_default: false,
            default: None,
        };
        let root = Shape {
            shape_type: ShapeType::Composite(vec![(String::new(), leaf.clone())]),
            ..leaf
        };
        let doc = Node::new(Value::Map(vec![Entry {
            key: String::new(),
            key_pos: None,
            value: Node::new(Value::Str("v".to_string())),
        }]));
        let caps = BTreeMap::from([(String::new(), "implemented".to_string())]);
        let e = materialize_flat(Some(&doc), &root, &["__a".to_string()], &caps).unwrap_err();
        assert_eq!((e.reason, e.field.as_str()), ("unsupported", ""));
    }
}
