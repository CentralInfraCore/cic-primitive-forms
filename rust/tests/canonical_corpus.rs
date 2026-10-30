//! The canonical bytes (P5-1) against the shared table testdata/p5/canonical-vectors.tsv, which tools/p5/engine_vectors.py checks against the pinned
//! cic-primitive-engine and against a reference written apart. The Go peer is go/canonical_corpus_test.go.

use std::fs;
use std::path::PathBuf;

use cic_primitive_forms::{
    canonical_bytes, canonical_digest, parse, Entry, Node, Value, CANONICAL_DUPLICATE_KEY,
    CANONICAL_UNSUPPORTED_VALUE,
};

struct Row {
    name: String,
    in_range: bool,
    input: String,
    canonical: Option<String>,
    digest: String,
}

fn rows() -> Vec<Row> {
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    );
    let text = fs::read_to_string(root.join("testdata/p5/canonical-vectors.tsv")).expect("table");
    text.lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            assert_eq!(f.len(), 7, "{l}");
            let lit = |s: &str| serde_json::from_str::<String>(s).expect("literal");
            Row {
                name: f[1].to_string(),
                in_range: f[2] == "in_range",
                input: lit(f[3]),
                canonical: (f[4] != "-").then(|| lit(f[4])),
                digest: f[5].to_string(),
            }
        })
        .collect()
}

#[test]
fn every_in_range_vector_gives_its_bytes_and_digest_and_every_other_is_outside_the_value() {
    let rows = rows();
    let (mut ranged, mut outside) = (0, 0);
    for r in &rows {
        let parsed = parse(&r.input);
        if r.in_range {
            let node = parsed.unwrap_or_else(|e| panic!("{}: {e:?}", r.name));
            let bytes = canonical_bytes(&node).unwrap_or_else(|e| panic!("{}: {e}", r.name));
            assert_eq!(
                String::from_utf8(bytes.clone()).unwrap(),
                r.canonical.clone().unwrap(),
                "{}",
                r.name
            );
            assert_eq!(canonical_digest(&bytes), r.digest, "{}", r.name);
            ranged += 1;
        } else {
            assert!(parsed.is_err(), "{}: the value holds it after all", r.name);
            outside += 1;
        }
    }
    assert_eq!((ranged, outside), (21, 6));
}

#[test]
fn the_digest_of_the_empty_mapping_is_the_one_the_engine_pins() {
    let bytes = canonical_bytes(&Node::map(vec![])).unwrap();
    assert_eq!(bytes, b"{}");
    assert_eq!(
        canonical_digest(&bytes),
        "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    );
}

#[test]
fn a_float_or_a_large_integer_is_refused_and_so_is_a_repeated_key() {
    let unsupported = Node::new(Value::Unsupported("1.5".to_string()));
    assert_eq!(
        canonical_bytes(&unsupported).unwrap_err().reason,
        CANONICAL_UNSUPPORTED_VALUE
    );
    let nested = Node::new(Value::Seq(vec![Node::map(vec![("a", unsupported)])]));
    assert_eq!(
        canonical_bytes(&nested).unwrap_err().reason,
        CANONICAL_UNSUPPORTED_VALUE
    );
    let entry = |k: &str, v: i64| Entry {
        key: k.to_string(),
        key_pos: None,
        value: Node::new(Value::Int(v)),
    };
    let dup = Node::new(Value::Map(vec![
        entry("a", 1),
        entry("b", 2),
        entry("a", 3),
    ]));
    assert_eq!(
        canonical_bytes(&dup).unwrap_err().reason,
        CANONICAL_DUPLICATE_KEY
    );
    assert_eq!(
        canonical_bytes(&dup).unwrap_err().to_string(),
        "no canonical bytes: duplicate_key"
    );
}

#[test]
fn the_order_of_the_entries_given_does_not_change_the_bytes_but_the_order_of_a_sequence_does() {
    let a = Node::map(vec![
        ("y", Node::new(Value::Int(1))),
        ("x", Node::new(Value::Int(2))),
    ]);
    let b = Node::map(vec![
        ("x", Node::new(Value::Int(2))),
        ("y", Node::new(Value::Int(1))),
    ]);
    assert_eq!(canonical_bytes(&a).unwrap(), canonical_bytes(&b).unwrap());
    let s = |v: [i64; 2]| {
        Node::new(Value::Seq(
            v.iter().map(|i| Node::new(Value::Int(*i))).collect(),
        ))
    };
    assert_ne!(
        canonical_bytes(&s([1, 2])).unwrap(),
        canonical_bytes(&s([2, 1])).unwrap()
    );
}
