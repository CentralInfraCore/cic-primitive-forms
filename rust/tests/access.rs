//! Reading `access-policy/1` values (cic-primitives D-018): the shared vectors
//! (`testdata/p1/access.json`, read by the Go peer too) and the behaviour around them.

use cic_primitive_forms::{
    bind_grammar, is_cert_pattern, parse, pinned_grammar_version, read_mode, read_rule, sha256_hex,
    write_mode, Entry, Error, Grammar, Mode, Node, Rule, Value, SUPPORTED_GRAMMARS,
};
use serde_json::Value as Json;
use std::{fs, path::PathBuf};

fn vectors() -> Json {
    let root = std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    );
    serde_json::from_str(&fs::read_to_string(root.join("testdata/p1/access.json")).unwrap())
        .unwrap()
}

fn value_of(j: &Json) -> Value {
    let v = &j["v"];
    match j["t"].as_str().unwrap() {
        "null" => Value::Null,
        "bool" => Value::Bool(v.as_bool().unwrap()),
        "int" => Value::Int(v.as_i64().unwrap()),
        "str" => Value::Str(v.as_str().unwrap().to_string()),
        "seq" => Value::Seq(
            v.as_array()
                .unwrap()
                .iter()
                .map(|x| Node::new(value_of(x)))
                .collect(),
        ),
        "map" => Value::Map(
            v.as_array()
                .unwrap()
                .iter()
                .map(|p| Entry {
                    key: p[0].as_str().unwrap().to_string(),
                    key_pos: None,
                    value: Node::new(value_of(&p[1])),
                })
                .collect(),
        ),
        other => panic!("{other}"),
    }
}

fn optional(j: &Json) -> Option<Value> {
    if j == "missing" {
        None
    } else {
        Some(value_of(j))
    }
}

fn g(v: i64) -> Grammar {
    bind_grammar(Some(&Value::Int(v)), Some(&Value::Int(v))).unwrap()
}

fn each<'a>(j: &'a Json, group: &str) -> &'a Vec<Json> {
    let items = j[group].as_array().expect(group);
    assert!(!items.is_empty(), "{group} is empty");
    items
}

#[test]
fn read_mode_vectors() {
    for v in each(&vectors(), "read_mode") {
        let name = v["name"].as_str().unwrap();
        let node = Node::new(value_of(&v["value"]));
        let grammar = g(v["grammar"].as_i64().unwrap());
        let want = v["expect"].as_str().unwrap();
        match read_mode(&node, grammar) {
            Ok(mode) => {
                let got = match mode {
                    Mode::Inherit => "inherit",
                    Mode::NoInherit => "no-inherit",
                    Mode::Reset => "reset",
                };
                assert_eq!(got, want, "{name}");
                // The round trip keeps the spelling, to the type.
                assert!(
                    write_mode(mode, grammar).same(&node),
                    "{name}: spelling lost"
                );
            }
            Err(e) => assert_eq!(e.code, want, "{name}"),
        }
    }
}

#[test]
fn read_rule_vectors() {
    for v in each(&vectors(), "read_rule") {
        let name = v["name"].as_str().unwrap();
        let node = Node::new(value_of(&v["value"]));
        let want = v["expect"].as_str().unwrap();
        let got = match read_rule(&node) {
            Ok(Rule::Symbol) => "symbol".to_string(),
            Ok(Rule::List(items)) => format!("list:{}", items.join("|")),
            Err(e) => e.code.to_string(),
        };
        assert_eq!(got, want, "{name}");
    }
}

#[test]
fn cert_pattern_vectors() {
    for v in each(&vectors(), "cert_pattern") {
        let name = v["name"].as_str().unwrap();
        assert_eq!(
            is_cert_pattern(v["pattern"].as_str().unwrap()),
            v["valid"].as_bool().unwrap(),
            "{name}"
        );
    }
}

#[test]
fn bind_vectors() {
    for v in each(&vectors(), "bind") {
        let name = v["name"].as_str().unwrap();
        let (c, p) = (optional(&v["context"]), optional(&v["pinned"]));
        let want = v["expect"].as_str().unwrap();
        match bind_grammar(c.as_ref(), p.as_ref()) {
            Ok(grammar) => assert_eq!(grammar.version().to_string(), want, "{name}"),
            Err(e) => assert_eq!(e.code(), want, "{name}"),
        }
    }
}

#[test]
fn violations_are_anchored_at_the_value_and_name_the_phase() {
    let n = parse("a: [x]\n").unwrap();
    let Value::Map(entries) = &n.value else {
        panic!()
    };
    let list = &entries[0].value;
    let e = read_rule(list).unwrap_err();
    assert_eq!(
        (e.code, e.phase.as_str()),
        ("INVALID_CERT_PATTERN", "validate")
    );
    assert_eq!(e.logical_path, None);
    let at = match e.location {
        cic_primitive_forms::Location::At(p) => (p.line, p.column, p.offset),
        cic_primitive_forms::Location::Unavailable => panic!("a parsed value has a position"),
    };
    assert_eq!(at, (1, 4, 3));
    let m = parse("m: 1\n").unwrap();
    let Value::Map(entries) = &m.value else {
        panic!()
    };
    assert_eq!(
        read_mode(&entries[0].value, g(1)).unwrap_err().code,
        "INVALID_INHERIT"
    );
    // No text: no coordinates.
    let hand = Node::new(Value::Int(1));
    assert_eq!(
        read_mode(&hand, g(1)).unwrap_err().location,
        cic_primitive_forms::Location::Unavailable
    );
}

#[test]
fn false_and_zero_are_two_values_in_every_direction() {
    let f = Node::new(Value::Bool(false));
    let zero = Node::new(Value::Int(0));
    assert!(!f.same(&zero));
    assert_eq!(read_mode(&f, g(1)).unwrap(), Mode::NoInherit);
    assert_eq!(read_mode(&zero, g(1)).unwrap(), Mode::Reset);
    // Parsed from text they stay different, `-0` included.
    for (text, mode) in [
        ("false", Mode::NoInherit),
        ("0", Mode::Reset),
        ("true", Mode::Inherit),
    ] {
        assert_eq!(
            read_mode(&parse(text).unwrap(), g(1)).unwrap(),
            mode,
            "{text}"
        );
    }
    // A float spelling never reaches the reading: the YAML subset refuses it.
    assert!(matches!(parse("0.0"), Err(Error::Unsupported(_))));
}

#[test]
fn the_empty_list_is_nobody_and_never_the_symbol() {
    assert_eq!(
        read_rule(&parse("[]").unwrap()).unwrap(),
        Rule::List(Vec::new())
    );
    assert_eq!(read_rule(&parse("inherit").unwrap()).unwrap(), Rule::Symbol);
    assert_ne!(Rule::List(Vec::new()), Rule::Symbol);
}

#[test]
fn write_mode_is_a_bijection_per_version() {
    for version in SUPPORTED_GRAMMARS {
        let grammar = g(version);
        let all = [Mode::Inherit, Mode::NoInherit, Mode::Reset];
        let written: Vec<Node> = all.iter().map(|m| write_mode(*m, grammar)).collect();
        for (i, m) in all.iter().enumerate() {
            assert_eq!(read_mode(&written[i], grammar).unwrap(), *m);
            for (j, other) in written.iter().enumerate() {
                assert_eq!(written[i].same(other), i == j, "three distinct spellings");
            }
        }
    }
    // The two spellings never cross.
    assert!(read_mode(&write_mode(Mode::Reset, g(1)), g(2)).is_err());
    assert!(read_mode(&write_mode(Mode::Reset, g(2)), g(1)).is_err());
}

#[test]
fn the_digest_is_the_sha256_of_the_bytes() {
    assert_eq!(
        sha256_hex(b"abc"),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
    assert_eq!(
        sha256_hex(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
}

fn schema(version_line: &str) -> Vec<u8> {
    format!("metadata:\n  name: Access\nspec:\n  kind: AtomicPrimitive\n  access_grammar:\n{version_line}    supported: [1, 2]\n")
        .into_bytes()
}

#[test]
fn the_pinned_version_comes_from_the_verified_bytes_only() {
    let ok = schema("    version: 1\n");
    let pinned = pinned_grammar_version(&ok, &sha256_hex(&ok)).unwrap();
    assert!(matches!(pinned, Some(Value::Int(1))));
    // A changed byte is a different pin, whatever it says.
    let other = schema("    version: 2\n");
    assert_eq!(
        pinned_grammar_version(&other, &sha256_hex(&ok))
            .unwrap_err()
            .code(),
        "GRAMMAR_DIGEST_MISMATCH"
    );
    // A verified schema with no version: the caller gets "missing", never a default.
    let none = b"spec:\n  kind: AtomicPrimitive\n".to_vec();
    let v = pinned_grammar_version(&none, &sha256_hex(&none)).unwrap();
    assert!(v.is_none());
    assert_eq!(
        bind_grammar(Some(&Value::Int(1)), v.as_ref())
            .unwrap_err()
            .code(),
        "GRAMMAR_VERSION_MISSING"
    );
    for tree in [
        "spec: 5\n",
        "spec: {access_grammar: 5}\n",
        "spec: [1]\n",
        "metadata: {}\n",
    ] {
        let b = tree.as_bytes().to_vec();
        assert!(
            pinned_grammar_version(&b, &sha256_hex(&b))
                .unwrap()
                .is_none(),
            "{tree}"
        );
    }
    // A version that is not an integer is carried as it is and refused by the binding.
    let text = schema("    version: '1'\n");
    let v = pinned_grammar_version(&text, &sha256_hex(&text)).unwrap();
    assert_eq!(
        bind_grammar(Some(&Value::Int(1)), v.as_ref())
            .unwrap_err()
            .code(),
        "UNKNOWN_GRAMMAR_VERSION"
    );
    // Text the YAML subset refuses, and bytes that are not text, are errors, not a default.
    let bad = b"spec: &a 1\n".to_vec();
    assert!(matches!(
        pinned_grammar_version(&bad, &sha256_hex(&bad)),
        Err(Error::Unsupported(_))
    ));
    let bytes = vec![0xff, 0xfe];
    assert!(matches!(
        pinned_grammar_version(&bytes, &sha256_hex(&bytes)),
        Err(Error::Syntax(_))
    ));
}

#[test]
fn binding_errors_carry_their_upstream_code() {
    for code in [
        "GRAMMAR_VERSION_MISSING",
        "UNKNOWN_GRAMMAR_VERSION",
        "GRAMMAR_VERSION_CONFLICT",
        "GRAMMAR_DIGEST_MISMATCH",
    ] {
        assert_eq!(Error::Binding(code).code(), code);
    }
}

#[test]
fn reading_leaves_the_input_alone() {
    let list = parse("[a=b, c=d]").unwrap();
    let before = list.clone();
    let _ = read_rule(&list);
    let _ = read_mode(&list, g(1));
    assert!(list.same(&before));
    assert_eq!(list.pos, before.pos);
}
