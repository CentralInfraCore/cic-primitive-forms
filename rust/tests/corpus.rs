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

/// code, phase, location, logical path (empty when the response leaves it out)
type Wire = (String, String, String, String);

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
    (
        v.code.to_string(),
        phase.to_string(),
        loc,
        v.logical_path.clone().unwrap_or_default(),
    )
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
            ["code", "phase", "source_location", "logical_path"].contains(k),
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
        get(e, "logical_path").map_or_else(String::new, |p| text(p, "logical_path").to_string()),
    )
}

/// Whether a `validate` case is run by a runner below: a presence case (`schema.synthetic.shape`) or an
/// inheritance case (`slot` inherit_key, inherit_policy or access_modify).
fn has_runner(case: &YamlOwned) -> bool {
    let synthetic = get(case, "schema").and_then(|s| get(s, "synthetic"));
    let slot = synthetic
        .and_then(|s| get(s, "slot"))
        .and_then(YamlOwned::as_str);
    synthetic.and_then(|s| get(s, "shape")).is_some()
        || ["inherit_key", "inherit_policy", "access_modify"].contains(&slot.unwrap_or(""))
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
                // The validate cases that have a runner of their own are counted there.
                if !(kind == "validate" && has_runner(case)) {
                    *not_run.entry(kind.to_string()).or_default() += 1;
                }
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

// ---------------------------------------------------------------------------
// kind `validate` against `schema.synthetic.shape` (the presence suite, ADR 0003).
//
// The fixture notation is bound here to the members of the Shape: `type` string/integer/boolean to
// `shape_type: scalar` with a `scalar_type`, `list` to a collection with `min_items`, `composite` or a
// bare `fields` to a composite, and a child written as just `mandatory`/`optional` to an opaque
// field. No schema of its own is defined; the library takes the Shape members only.
// ---------------------------------------------------------------------------

use cic_primitive_forms::{validate, Presence, ScalarType, Shape, ShapeType};

fn presence_of(y: &YamlOwned) -> Presence {
    match text(y, "presence") {
        "mandatory" => Presence::Mandatory,
        "optional" => Presence::Optional,
        other => panic!("unknown presence {other:?}"),
    }
}

fn shape_of(y: &YamlOwned) -> Shape {
    if let Some(p) = y.as_str() {
        // A child written as just its presence: any value, nothing below it.
        return Shape {
            shape_type: ShapeType::Opaque,
            presence: presence_of(&YamlOwned::Value(ScalarOwned::String(p.to_string()))),
            nullable: false,
            has_default: false,
        };
    }
    let shape_type = match get(y, "type").map(|t| text(t, "type")) {
        Some("string") => ShapeType::Scalar(ScalarType::String),
        Some("integer") => ShapeType::Scalar(ScalarType::Integer),
        Some("boolean") => ShapeType::Scalar(ScalarType::Boolean),
        Some("list") => ShapeType::List {
            min_items: get(y, "min_items")
                .and_then(YamlOwned::as_integer)
                .map(|n| usize::try_from(n).expect("min_items is not negative")),
        },
        Some("composite") | None if get(y, "fields").is_some() => {
            let mut fields: Vec<(String, Shape)> = get(y, "fields")
                .and_then(YamlOwned::as_mapping)
                .expect("fields: a mapping")
                .iter()
                .map(|(k, v)| (text(k, "field name").to_string(), shape_of(v)))
                .collect();
            fields.sort_by(|a, b| a.0.cmp(&b.0));
            ShapeType::Composite(fields)
        }
        None => ShapeType::Opaque,
        Some(other) => panic!("unsupported fixture type {other:?}"),
    };
    Shape {
        shape_type,
        presence: presence_of(get(y, "presence").expect("presence")),
        nullable: get(y, "nullable")
            .and_then(YamlOwned::as_bool)
            .unwrap_or(false),
        has_default: get(y, "default").is_some(),
    }
}

#[test]
fn presence_validate_cases_match_the_corpus() {
    let mut ran = 0;
    let mut not_run: BTreeMap<String, usize> = BTreeMap::new();
    let mut failures = Vec::new();
    for (_, suite) in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let synthetic = get(get(case, "schema").expect("schema"), "synthetic");
            let shape_fixture = synthetic.and_then(|s| get(s, "shape"));
            if text(get(case, "kind").expect("kind"), "kind") != "validate" {
                continue;
            }
            let Some(shape_fixture) = shape_fixture else {
                *not_run
                    .entry("validate (not a presence case, see the accounting test)".to_string())
                    .or_default() += 1;
                continue;
            };
            ran += 1;
            let shape = shape_of(shape_fixture);
            let parsed;
            let node = if let Some(src) = get(case, "input_yaml") {
                parsed = parse(text(src, "input_yaml")).unwrap_or_else(|e| panic!("{id}: {e:?}"));
                Some(&parsed)
            } else if get(case, "absent").is_some() {
                None
            } else {
                parsed = node_of(get(case, "input").expect("input"));
                Some(&parsed)
            };
            let before = node.cloned();
            let got = validate(node, &shape, &a()).unwrap_or_else(|e| panic!("{id}: {e:?}"));
            if let (Some(n), Some(b)) = (node, before) {
                if !n.same(&b) || n.pos != b.pos {
                    failures.push(format!("{id}: validation changed its input"));
                }
            }
            let expect = get(case, "expect").expect("expect");
            let want: Vec<Wire> = get(expect, "errors")
                .map(|es| {
                    es.as_vec()
                        .expect("errors")
                        .iter()
                        .map(expected_wire)
                        .collect()
                })
                .unwrap_or_default();
            let have: Vec<Wire> = got.violations.iter().map(wire).collect();
            if want != have {
                failures.push(format!("{id}: errors: got {have:?}, want {want:?}"));
            }
            if let Some(v) = get(expect, "valid").and_then(YamlOwned::as_bool) {
                if v != got.violations.is_empty() {
                    failures.push(format!(
                        "{id}: valid: got {}, want {v}",
                        got.violations.is_empty()
                    ));
                }
            }
            if let Some(want_candidate) =
                get(expect, "default_candidate").and_then(YamlOwned::as_bool)
            {
                let have_candidate = got.default_candidates.iter().any(|p| p == "$");
                if have_candidate != want_candidate {
                    failures.push(format!(
                        "{id}: default_candidate: got {have_candidate}, want {want_candidate}"
                    ));
                }
            }
            if get(expect, "stays_absent").is_some()
                && !(node.is_none() && got.violations.is_empty())
            {
                failures.push(format!("{id}: stays_absent expected"));
            }
            for key in ["kind", "is_inherit", "preserved", "upstream_conflict"] {
                assert!(
                    get(expect, key).is_none(),
                    "{id}: expectation {key} is not a presence one"
                );
            }
        }
    }
    println!("presence validate cases run: {ran}");
    for (kind, n) in &not_run {
        println!("NOT RUN: {kind}: {n} cases");
    }
    assert_eq!(
        ran, 29,
        "the presence suite has 29 shape-based validate cases"
    );
    assert!(
        failures.is_empty(),
        "{} failed:\n  {}",
        failures.len(),
        failures.join("\n  ")
    );
}

// ---------------------------------------------------------------------------
// kind `validate` against `schema.synthetic.slot` (the inherit suite, ADR 0004, cic-primitives D-018).
//
// The Access grammar version is derived from the VERIFIED pinned schema: the bytes of
// `schemas/atomic/access.yaml` come from a checkout of cic-primitives at the pinned commit
// (`CIC_PRIMITIVES_ROOT`), their SHA-256 must equal the one in `corpus/pins.yaml`, and only then is
// `spec.access_grammar.version` read. The processing context declares the grammar the corpus is written
// in. A missing checkout is a failure, not a skip: nothing silently falls back to a default version.
// ---------------------------------------------------------------------------

use cic_primitive_forms::{
    bind_grammar, pinned_grammar_version, read_mode, read_rule, write_mode, Grammar, Rule,
};

/// The grammar the inherit vectors are written in (`true | false | 0`).
const CORPUS_GRAMMAR: i64 = 1;

const ACCESS_SCHEMA: &str = "schemas/atomic/access.yaml";

fn pinned_access_sha256() -> String {
    let pins = load(&repo_root().join("corpus/pins.yaml"));
    let grammar = get(&pins, "grammar")
        .and_then(YamlOwned::as_mapping)
        .expect("grammar pins");
    assert_eq!(grammar.len(), 1, "exactly one grammar pin");
    let (_, pin) = grammar.iter().next().unwrap();
    get(pin, "files")
        .and_then(YamlOwned::as_vec)
        .expect("files")
        .iter()
        .find(|f| get(f, "path").and_then(YamlOwned::as_str) == Some(ACCESS_SCHEMA))
        .map(|f| text(get(f, "sha256").expect("sha256"), "sha256").to_string())
        .expect("the pin lists access.yaml")
}

fn bound_grammar() -> Grammar {
    let root = std::env::var_os("CIC_PRIMITIVES_ROOT").expect(
        "CIC_PRIMITIVES_ROOT must point at a checkout of CentralInfraCore/cic-primitives at the pinned commit \
         (python3 tools/grammar_identity.py verify --pins corpus/pins.yaml --repo DIR checks it)",
    );
    let bytes =
        fs::read(PathBuf::from(root).join(ACCESS_SCHEMA)).expect("access.yaml in the checkout");
    let pinned = pinned_grammar_version(&bytes, &pinned_access_sha256())
        .expect("the pinned schema verifies");
    bind_grammar(Some(&Value::Int(CORPUS_GRAMMAR)), pinned.as_ref())
        .expect("the context agrees with the pin")
}

fn typed(node: &Node) -> (&'static str, String) {
    match &node.value {
        Value::Null => ("null", "null".into()),
        Value::Bool(b) => ("boolean", b.to_string()),
        Value::Int(i) => ("integer", i.to_string()),
        Value::Str(s) => ("string", s.clone()),
        Value::Seq(_) => ("list", "[]".into()),
        Value::Map(_) => ("map", "{}".into()),
    }
}

fn expected_typed(y: &YamlOwned) -> (String, String) {
    let t = text(get(y, "type").expect("type"), "type").to_string();
    let v = get(y, "value").expect("value");
    let rendered = match v {
        YamlOwned::Value(ScalarOwned::Boolean(b)) => b.to_string(),
        YamlOwned::Value(ScalarOwned::Integer(i)) => i.to_string(),
        YamlOwned::Value(ScalarOwned::String(s)) => s.clone(),
        YamlOwned::Value(ScalarOwned::Null) => "null".into(),
        YamlOwned::Sequence(_) => "[]".into(),
        other => panic!("unsupported typed value {other:?}"),
    };
    (t, rendered)
}

#[test]
fn inherit_validate_cases_match_the_corpus() {
    let grammar = bound_grammar();
    assert_eq!(grammar.version(), CORPUS_GRAMMAR);
    let mut ran = 0;
    let mut failures = Vec::new();
    for (_, suite) in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let synthetic = get(get(case, "schema").expect("schema"), "synthetic");
            let slot = synthetic
                .and_then(|s| get(s, "slot"))
                .map(|s| text(s, "slot"));
            if text(get(case, "kind").expect("kind"), "kind") != "validate" || slot.is_none() {
                continue;
            }
            let slot = slot.unwrap();
            if !["inherit_key", "inherit_policy", "access_modify"].contains(&slot) {
                continue; // a `conformance` slot is ADR 0010, not this slice
            }
            ran += 1;
            let node = node_of(get(case, "input").expect("input"));
            let before = node.clone();
            let expect = get(case, "expect").expect("expect");
            let mut errors: Vec<Wire> = Vec::new();
            let mut preserved = None;
            let mut kind = None;
            let mut is_inherit = None;
            if slot == "access_modify" {
                match read_rule(&node) {
                    Ok(Rule::Symbol) => {
                        kind = Some("symbol");
                        is_inherit = Some(true);
                    }
                    Ok(Rule::List(_)) => {
                        kind = Some("list");
                        is_inherit = Some(false);
                    }
                    Err(v) => errors.push(wire(&v)),
                }
            } else {
                match read_mode(&node, grammar) {
                    Ok(mode) => preserved = Some(typed(&write_mode(mode, grammar))),
                    Err(v) => errors.push(wire(&v)),
                }
            }
            if !node.same(&before) || node.pos != before.pos {
                failures.push(format!("{id}: reading changed its input"));
            }
            let want: Vec<Wire> = get(expect, "errors")
                .map(|es| {
                    es.as_vec()
                        .expect("errors")
                        .iter()
                        .map(expected_wire)
                        .collect()
                })
                .unwrap_or_default();
            if want != errors {
                failures.push(format!("{id}: errors: got {errors:?}, want {want:?}"));
            }
            if let Some(v) = get(expect, "valid").and_then(YamlOwned::as_bool) {
                if v != errors.is_empty() {
                    failures.push(format!("{id}: valid: got {}, want {v}", errors.is_empty()));
                }
            }
            if let Some(p) = get(expect, "preserved") {
                let (t, v) = expected_typed(p);
                if preserved.as_ref().map(|(a, b)| (a.to_string(), b.clone()))
                    != Some((t.clone(), v.clone()))
                {
                    failures.push(format!("{id}: preserved: got {preserved:?}, want {t}/{v}"));
                }
            }
            if let Some(k) = get(expect, "kind") {
                if kind != Some(text(k, "kind")) {
                    failures.push(format!("{id}: kind: got {kind:?}"));
                }
            }
            if let Some(i) = get(expect, "is_inherit").and_then(YamlOwned::as_bool) {
                if is_inherit != Some(i) {
                    failures.push(format!("{id}: is_inherit: got {is_inherit:?}, want {i}"));
                }
            }
            assert!(
                get(expect, "upstream_conflict").is_none(),
                "{id}: D-018 removed the upstream conflict; the expectation must not return"
            );
        }
    }
    println!("inherit validate cases run: {ran}");
    assert_eq!(
        ran, 21,
        "the inherit suite has 21 slot-based validate cases of the access-policy kind"
    );
    assert!(
        failures.is_empty(),
        "{} failed:\n  {}",
        failures.len(),
        failures.join("\n  ")
    );
}

/// The accounting is exact: every case of the corpus is either run by one of the runners of this file or
/// counted as NOT RUN, and the numbers are the ones in `docs/P1-SUPPORTED-SUBSET.md`.
#[test]
fn the_run_and_not_run_accounting_is_exact() {
    let mut total = 0;
    let mut run: BTreeMap<String, usize> = BTreeMap::new();
    let mut not_run: BTreeMap<String, usize> = BTreeMap::new();
    for (suite, doc) in suites() {
        for case in get(&doc, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            total += 1;
            let kind = text(get(case, "kind").expect("kind"), "kind");
            let key = format!("{suite}/{kind}");
            if kind == "recognize" || (kind == "validate" && has_runner(case)) {
                *run.entry(key).or_default() += 1;
            } else {
                *not_run.entry(key).or_default() += 1;
            }
        }
    }
    for (k, n) in &run {
        println!("RUN     {k}: {n}");
    }
    for (k, n) in &not_run {
        println!("NOT RUN {k}: {n}");
    }
    let ran: usize = run.values().sum();
    let skipped: usize = not_run.values().sum();
    assert_eq!(ran + skipped, total, "every case is accounted for");
    assert_eq!(
        (ran, skipped),
        (81, 258),
        "31 recognize + 29 presence validate + 21 inherit validate run"
    );
    assert_eq!(run.get("inherit/validate"), Some(&21));
    assert_eq!(run.get("presence/validate"), Some(&29));
    assert_eq!(
        run.get("recognition/recognize").copied().unwrap_or(0)
            + run.get("presence/recognize").copied().unwrap_or(0),
        31
    );
}
