//! The shared P1 subset vectors (`testdata/p1/subset.json`): what `parse` reads, what it
//! refuses and what `emit` writes. The Go peer reads the same file.

use std::fs;
use std::path::PathBuf;

use cic_primitive_forms::{emit, parse, Entry, Error, Node, Value};
use serde_json::Value as Json;

fn vectors() -> Json {
    // `CIC_FORMS_ROOT` overrides the location: see tests/corpus.rs.
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    );
    let path = root.join("testdata/p1/subset.json");
    serde_json::from_str(&fs::read_to_string(path).expect("subset.json")).expect("valid JSON")
}

/// `{"t": "int", "v": 1}` and friends, with maps as ordered `[key, node]` pairs.
fn node_of(j: &Json) -> Node {
    let v = &j["v"];
    Node::new(match j["t"].as_str().expect("t") {
        "null" => Value::Null,
        "bool" => Value::Bool(v.as_bool().expect("bool")),
        "int" => Value::Int(v.as_i64().expect("int")),
        "str" => Value::Str(v.as_str().expect("str").to_string()),
        "seq" => Value::Seq(v.as_array().expect("seq").iter().map(node_of).collect()),
        "map" => Value::Map(
            v.as_array()
                .expect("map")
                .iter()
                .map(|p| Entry {
                    key: p[0].as_str().expect("key").to_string(),
                    key_pos: None,
                    value: node_of(&p[1]),
                })
                .collect(),
        ),
        other => panic!("unknown node type {other}"),
    })
}

fn each<'a>(j: &'a Json, group: &str) -> &'a Vec<Json> {
    let items = j[group].as_array().expect(group);
    assert!(!items.is_empty(), "{group} has no vectors");
    items
}

#[test]
fn parse_reads_the_supported_subset() {
    for v in each(&vectors(), "parse_ok") {
        let name = v["name"].as_str().unwrap();
        let got = parse(v["yaml"].as_str().unwrap()).unwrap_or_else(|e| panic!("{name}: {e:?}"));
        assert!(got.same(&node_of(&v["node"])), "{name}: got {got:?}");
    }
}

#[test]
fn parse_refuses_what_is_outside_the_subset() {
    for group in ["unsupported", "syntax"] {
        for v in each(&vectors(), group) {
            let name = v["name"].as_str().unwrap();
            match parse(v["yaml"].as_str().unwrap()) {
                Err(e @ (Error::Unsupported(_) | Error::Syntax(_))) => {
                    assert_eq!(e.code(), v["code"].as_str().unwrap(), "{name}");
                }
                other => panic!("{name}: expected a refusal, got {other:?}"),
            }
        }
    }
}

#[test]
fn emit_writes_the_pinned_text_and_it_reads_back() {
    for v in each(&vectors(), "emit") {
        let name = v["name"].as_str().unwrap();
        let node = node_of(&v["node"]);
        let text = emit(&node);
        assert_eq!(text, v["yaml"].as_str().unwrap(), "{name}");
        let back = parse(&text).unwrap_or_else(|e| panic!("{name}: {e:?}"));
        assert!(back.same(&node), "{name}: did not read back");
    }
}
