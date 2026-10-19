//! Runs the shared conformance corpus (`corpus/*/vectors.yaml`, ADR 0005) against the
//! product code. The corpus is loaded with an independent YAML library (`saphyr`), not
//! with the code under test.
//!
//! Implemented here: every case of kind `recognize`. Every other kind is counted and
//! printed as not run, so a green run cannot be read as covering it; the supported
//! subset is `docs/P1-SUPPORTED-SUBSET.md`.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use cic_primitive_forms::{
    emit, parse, recognize, Class, Error, Location, Node, Phase, Position, Value, Violation,
};
use saphyr::{LoadableYamlNode, ScalarOwned, YamlOwned};

/// The expanded key set `A`, read from the grammar pin by the corpus header. The library
/// takes it as an argument; `a_matches_the_corpus` ties this constant to the vectors.
const A: [&str; 6] = [
    "value",
    "access",
    "modify",
    "inherit",
    "default_injection",
    "conformance",
];

fn a() -> Vec<String> {
    A.iter().map(|k| (*k).to_string()).collect()
}

/// The repository root. The branch coverage tool runs the tests in a copy of the crate, so
/// the path cannot come from the manifest directory there: `CIC_FORMS_ROOT` overrides it.
fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn load(path: &PathBuf) -> YamlOwned {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    YamlOwned::load_from_str(&text)
        .unwrap_or_else(|e| panic!("{}: {e}", path.display()))
        .remove(0)
}

fn suites() -> Vec<(String, YamlOwned)> {
    let root = repo_root().join("corpus");
    let mut dirs: Vec<PathBuf> = fs::read_dir(&root)
        .expect("corpus directory")
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.join("vectors.yaml").is_file())
        .collect();
    dirs.sort();
    dirs.iter()
        .map(|d| {
            (
                d.file_name().unwrap().to_string_lossy().to_string(),
                load(&d.join("vectors.yaml")),
            )
        })
        .collect()
}

fn get<'a>(y: &'a YamlOwned, key: &str) -> Option<&'a YamlOwned> {
    y.as_mapping_get(key)
}

fn text<'a>(y: &'a YamlOwned, what: &str) -> &'a str {
    y.as_str()
        .unwrap_or_else(|| panic!("{what}: expected a string"))
}

/// A corpus value as a position-less [`Node`].
fn node_of(y: &YamlOwned) -> Node {
    match y {
        YamlOwned::Value(ScalarOwned::Null) => Node::new(Value::Null),
        YamlOwned::Value(ScalarOwned::Boolean(b)) => Node::new(Value::Bool(*b)),
        YamlOwned::Value(ScalarOwned::Integer(i)) => Node::new(Value::Int(*i)),
        YamlOwned::Value(ScalarOwned::String(s)) => Node::new(Value::Str(s.clone())),
        YamlOwned::Sequence(items) => Node::new(Value::Seq(items.iter().map(node_of).collect())),
        YamlOwned::Mapping(m) => Node::new(Value::Map(
            m.iter()
                .map(|(k, v)| cic_primitive_forms::Entry {
                    key: text(k, "mapping key").to_string(),
                    key_pos: None,
                    value: node_of(v),
                })
                .collect(),
        )),
        other => panic!("unsupported corpus value {other:?}"),
    }
}

fn position_of(y: &YamlOwned) -> Position {
    if let Some(s) = y.as_str() {
        return match s {
            "scalar" => Position::Scalar,
            "opaque" => Position::Opaque,
            other => panic!("unknown position {other:?}"),
        };
    }
    if let Some(fields) = get(y, "structured") {
        return Position::Structured(
            fields
                .as_vec()
                .expect("structured: a list")
                .iter()
                .map(|f| text(f, "structured field").to_string())
                .collect(),
        );
    }
    let record = get(get(y, "list_of").expect("list_of"), "record").expect("record");
    Position::ListOf(
        record
            .as_mapping()
            .expect("record: a mapping")
            .iter()
            .map(|(k, v)| (text(k, "record field").to_string(), position_of(v)))
            .collect(),
    )
}

fn class_name(c: Class) -> &'static str {
    match c {
        Class::Short => "short",
        Class::Expanded => "expanded",
        Class::Error => "error",
    }
}

type Wire = (String, String, String);

/// A violation as the corpus spells it: code, phase, location.
fn wire(v: &Violation) -> Wire {
    let phase = match v.phase {
        Phase::Parse => "parse",
        Phase::Recognize => "recognize",
        Phase::Validate => "validate",
    };
    let loc = match v.location {
        Location::Unavailable => "unavailable".to_string(),
        Location::At(p) => format!("{}:{}:{}", p.line, p.column, p.offset),
    };
    (v.code.to_string(), phase.to_string(), loc)
}

fn expected_wire(e: &YamlOwned) -> Wire {
    let keys: Vec<&str> = e
        .as_mapping()
        .expect("error: a mapping")
        .keys()
        .map(|k| text(k, "error key"))
        .collect();
    for k in &keys {
        assert!(
            ["code", "phase", "source_location"].contains(k),
            "an error field this slice does not produce: {k}"
        );
    }
    let loc = get(e, "source_location").expect("source_location");
    let loc = if let Some(state) = get(loc, "state") {
        assert_eq!(text(state, "state"), "unavailable");
        "unavailable".to_string()
    } else {
        let n = |k: &str| get(loc, k).and_then(YamlOwned::as_integer).expect(k);
        format!("{}:{}:{}", n("line"), n("column"), n("offset"))
    };
    (
        text(get(e, "code").expect("code"), "code").to_string(),
        text(get(e, "phase").expect("phase"), "phase").to_string(),
        loc,
    )
}

struct Run {
    class: Option<Class>,
    elements: Option<Vec<BTreeMap<String, Class>>>,
    opaque_value: bool,
    errors: Vec<Wire>,
}

fn run_case(case: &YamlOwned, id: &str) -> Run {
    let synthetic = get(get(case, "schema").expect("schema"), "synthetic").expect("synthetic");
    let position = position_of(get(synthetic, "position").expect("position"));
    let node = if let Some(src) = get(case, "input_yaml") {
        match parse(text(src, "input_yaml")) {
            Ok(n) => n,
            Err(Error::Contract(vs)) => {
                return Run {
                    class: None,
                    elements: None,
                    opaque_value: false,
                    errors: vs.iter().map(wire).collect(),
                }
            }
            Err(e) => panic!("{id}: outside the contract: {e:?}"),
        }
    } else {
        node_of(get(case, "input").expect("input"))
    };
    let out = recognize(&node, &position, &a()).unwrap_or_else(|e| panic!("{id}: {e:?}"));
    Run {
        class: Some(out.class),
        elements: out.elements,
        opaque_value: out.opaque_value,
        errors: out.violations.iter().map(wire).collect(),
    }
}

#[test]
fn recognize_cases_match_the_corpus() {
    let mut ran = 0;
    let mut not_run: BTreeMap<String, usize> = BTreeMap::new();
    let mut failures = Vec::new();
    for (_, suite) in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let kind = text(get(case, "kind").expect("kind"), "kind");
            if kind != "recognize" {
                *not_run.entry(kind.to_string()).or_default() += 1;
                continue;
            }
            ran += 1;
            let got = run_case(case, id);
            let expect = get(case, "expect").expect("expect");

            if let Some(class) = get(expect, "class") {
                let want = text(class, "class");
                let have = got.class.map_or("none", class_name);
                if want != have {
                    failures.push(format!("{id}: class: got {have}, want {want}"));
                }
            }
            if get(expect, "opaque_value").is_some() && !got.opaque_value {
                failures.push(format!("{id}: opaque_value expected"));
            }
            if let Some(elements) = get(expect, "elements") {
                let want: Vec<BTreeMap<String, String>> = elements
                    .as_vec()
                    .expect("elements")
                    .iter()
                    .map(|el| {
                        el.as_mapping()
                            .expect("element")
                            .iter()
                            .map(|(k, v)| {
                                (text(k, "field").to_string(), text(v, "class").to_string())
                            })
                            .collect()
                    })
                    .collect();
                let have: Vec<BTreeMap<String, String>> = got
                    .elements
                    .clone()
                    .expect("a list position gives elements")
                    .iter()
                    .map(|el| {
                        el.iter()
                            .map(|(k, c)| (k.clone(), class_name(*c).to_string()))
                            .collect()
                    })
                    .collect();
                if want != have {
                    failures.push(format!("{id}: elements: got {have:?}, want {want:?}"));
                }
            }
            let want_errors: Vec<Wire> = get(expect, "errors")
                .map(|es| {
                    es.as_vec()
                        .expect("errors")
                        .iter()
                        .map(expected_wire)
                        .collect()
                })
                .unwrap_or_default();
            if want_errors != got.errors {
                failures.push(format!(
                    "{id}: errors: got {:?}, want {want_errors:?}",
                    got.errors
                ));
            }
        }
    }
    println!("recognize cases run: {ran}");
    for (kind, n) in &not_run {
        println!("NOT RUN: kind {kind}: {n} cases");
    }
    assert!(
        ran >= 30,
        "the corpus has at least 30 recognize cases, ran {ran}"
    );
    assert!(
        failures.is_empty(),
        "{} failed:\n  {}",
        failures.len(),
        failures.join("\n  ")
    );
}

#[test]
fn a_matches_the_corpus() {
    let (_, suite) = suites()
        .into_iter()
        .find(|(name, _)| name == "recognition")
        .expect("recognition suite");
    let case = get(&suite, "cases")
        .and_then(YamlOwned::as_vec)
        .unwrap()
        .iter()
        .find(|c| {
            get(c, "id").and_then(YamlOwned::as_str) == Some("recognition.full-key-set-expanded")
        })
        .expect("the vector that spells A");
    let mut keys: Vec<String> = get(case, "input")
        .and_then(YamlOwned::as_mapping)
        .unwrap()
        .keys()
        .map(|k| text(k, "key").to_string())
        .collect();
    keys.sort();
    let mut mine = a();
    mine.sort();
    assert_eq!(keys, mine);
}

/// Every document the recognize cases feed in survives `emit` then `parse`.
#[test]
fn corpus_documents_survive_emit_and_parse() {
    let mut checked = 0;
    for (_, suite) in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            if text(get(case, "kind").expect("kind"), "kind") != "recognize" {
                continue;
            }
            let id = text(get(case, "id").expect("id"), "id");
            let node = match (get(case, "input"), get(case, "input_yaml")) {
                (Some(v), _) => node_of(v),
                (None, Some(src)) => match parse(text(src, "input_yaml")) {
                    Ok(n) => n,
                    Err(_) => continue, // a parse-phase vector has no document
                },
                (None, None) => panic!("{id}: no input"),
            };
            let back = parse(&emit(&node)).unwrap_or_else(|e| panic!("{id}: {e:?}"));
            assert!(back.same(&node), "{id}: emit/parse changed the value");
            checked += 1;
        }
    }
    assert!(checked >= 25, "checked only {checked}");
}
