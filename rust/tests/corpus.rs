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
    collapse_position, edit_parsed, edit_scalar, emit, expand_position, format_path, parse,
    parse_path, path_of, recognize, resolve_path, source_span, Case, Class, Elements, Error,
    KeyComp, KeyValue, Location, Node, PathSeg, Phase, Position, Presence, ScalarType, Shape,
    ShapeType, Step, Value, Violation,
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
        Phase::PathOf => "path_of",
        Phase::PathParse => "path_parse",
        Phase::PathFormat => "path_format",
        Phase::PathResolve => "path_resolve",
        Phase::Edit => "edit",
    };
    let loc = match v.location {
        Location::Unavailable => "unavailable".to_string(),
        Location::NotApplicable => "not_applicable".to_string(),
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
        let state = text(state, "state");
        assert!(
            ["unavailable", "not_applicable"].contains(&state),
            "state {state}"
        );
        state.to_string()
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

use cic_primitive_forms::validate;

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
            default: None,
        };
    }
    let shape_type = match get(y, "type").map(|t| text(t, "type")) {
        Some("string") => ShapeType::Scalar(ScalarType::String),
        Some("integer") => ShapeType::Scalar(ScalarType::Integer),
        Some("boolean") => ShapeType::Scalar(ScalarType::Boolean),
        Some("list") => {
            let min_items = get(y, "min_items")
                .and_then(YamlOwned::as_integer)
                .map(|n| usize::try_from(n).expect("min_items is not negative"));
            match get(y, "elements") {
                Some(el) => ShapeType::DeclaredList {
                    min_items,
                    elements: elements_of(el),
                },
                None => ShapeType::List { min_items },
            }
        }
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
        default: None,
    }
}

/// The fixture notation of the element structure of a list: one case (key, fields) or cases.
fn elements_of(el: &YamlOwned) -> Elements {
    let read = |name: &str, c: &YamlOwned| -> Case {
        let mut fields: Vec<(String, Shape)> = get(c, "fields")
            .and_then(YamlOwned::as_mapping)
            .expect("fields: a mapping")
            .iter()
            .map(|(k, v)| (text(k, "field name").to_string(), shape_of(v)))
            .collect();
        fields.sort_by(|a, b| a.0.cmp(&b.0));
        Case {
            name: name.to_string(),
            fields,
            key: get(c, "key")
                .and_then(YamlOwned::as_vec)
                .expect("key")
                .iter()
                .map(|k| text(k, "key").to_string())
                .collect(),
        }
    };
    match get(el, "cases").and_then(YamlOwned::as_vec) {
        Some(cases) => Elements {
            choice: true,
            cases: cases
                .iter()
                .map(|c| read(text(get(c, "name").expect("name"), "name"), c))
                .collect(),
        },
        None => Elements {
            choice: false,
            cases: vec![read("", el)],
        },
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
        ran, 37,
        "the presence and lists suites have 37 shape-based validate cases"
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
            if kind == "recognize"
                || (kind == "validate" && has_runner(case))
                || is_path_run(kind)
                || is_p2_run(kind)
                || kind == "source_span"
                || kind == "edit"
            {
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
        (312, 107),
        "31 recognize + 29 presence validate + 21 inherit validate + 8 lists validate + 18 path_of + 7 path_stability + 28 source_span + 44 edit run"
    );
    assert_eq!(run.get("edit/source_span"), Some(&28));
    assert_eq!(run.get("edit/edit"), Some(&44));
    assert_eq!(not_run.get("edit/edit"), None);
    assert_eq!(run.get("lists/validate"), Some(&8));
    assert_eq!(run.get("paths/path_parse"), Some(&57));
    assert_eq!(run.get("paths/path_format"), Some(&9));
    assert_eq!(run.get("paths/path_resolve"), Some(&34));
    assert_eq!(run.get("paths/path_of"), Some(&18));
    assert_eq!(run.get("paths/path_stability"), Some(&7));
    assert_eq!(run.get("inherit/validate"), Some(&21));
    assert_eq!(run.get("presence/validate"), Some(&29));
    assert_eq!(
        run.get("recognition/recognize").copied().unwrap_or(0)
            + run.get("presence/recognize").copied().unwrap_or(0),
        31
    );
}

// ---------------------------------------------------------------------------
// path_of and path_stability (ADR 0006): the identity of a list element in a path.

/// The four P2 kinds, run at one primitive position.
fn is_p2_run(kind: &str) -> bool {
    matches!(kind, "expand" | "collapse" | "roundtrip" | "equality")
}

fn is_path_run(kind: &str) -> bool {
    matches!(
        kind,
        "path_of" | "path_stability" | "path_parse" | "path_format" | "path_resolve"
    )
}

fn path_shape(shape_type: ShapeType, mandatory: bool) -> Shape {
    Shape {
        shape_type,
        presence: if mandatory {
            Presence::Mandatory
        } else {
            Presence::Optional
        },
        nullable: false,
        has_default: false,
        default: None,
    }
}

/// The synthetic tree notation of `corpus/paths/vectors.yaml` as a Shape. In the path fixtures the key
/// fields are the only mandatory fields (ADR 0006), so the fields of a list element are optional except the
/// key; the fields of a composite are optional.
fn tree_shape(y: &YamlOwned, mandatory: bool) -> Shape {
    if y.as_str() == Some("opaque") {
        return path_shape(ShapeType::Opaque, mandatory);
    }
    if let Some(sc) = get(y, "scalar") {
        let t = match text(sc, "scalar") {
            "integer" => ScalarType::Integer,
            "boolean" => ScalarType::Boolean,
            _ => ScalarType::String,
        };
        return path_shape(ShapeType::Scalar(t), mandatory);
    }
    if get(y, "set").is_some() {
        return path_shape(
            ShapeType::Set {
                item: ScalarType::String,
                min_items: None,
            },
            mandatory,
        );
    }
    if let Some(c) = get(y, "composite") {
        return path_shape(ShapeType::Composite(tree_fields(c, &[])), mandatory);
    }
    let list = get(y, "list").expect("list");
    let key_of = |y: &YamlOwned| -> Vec<String> {
        get(y, "key")
            .and_then(YamlOwned::as_vec)
            .expect("key")
            .iter()
            .map(|k| text(k, "key").to_string())
            .collect()
    };
    let elements = if let Some(choice) = get(list, "choice") {
        Elements {
            choice: true,
            cases: choice
                .as_vec()
                .expect("choice")
                .iter()
                .map(|cs| {
                    let key = key_of(cs);
                    Case {
                        name: text(get(cs, "name").expect("name"), "name").to_string(),
                        fields: tree_fields(get(cs, "fields").expect("fields"), &key),
                        key,
                    }
                })
                .collect(),
        }
    } else {
        let key = key_of(list);
        Elements {
            choice: false,
            cases: vec![Case {
                name: String::new(),
                fields: tree_fields(get(list, "fields").expect("fields"), &key),
                key,
            }],
        }
    };
    path_shape(
        ShapeType::DeclaredList {
            min_items: None,
            elements,
        },
        mandatory,
    )
}

fn tree_fields(y: &YamlOwned, key: &[String]) -> Vec<(String, Shape)> {
    let mut fields: Vec<(String, Shape)> = y
        .as_mapping()
        .expect("fields: a mapping")
        .iter()
        .map(|(k, v)| {
            let name = text(k, "field name").to_string();
            let shape = tree_shape(v, key.contains(&name));
            (name, shape)
        })
        .collect();
    fields.sort_by(|a, b| a.0.cmp(&b.0));
    fields
}

fn steps_of(y: &YamlOwned) -> Vec<Step> {
    y.as_vec()
        .expect("at: a list")
        .iter()
        .map(|s| match s.as_integer() {
            Some(n) => Step::Index(usize::try_from(n).expect("index")),
            None => Step::Field(text(s, "step").to_string()),
        })
        .collect()
}

/// What `path_of` says: a path, or the wire form of its one error.
fn path_outcome(id: &str, doc: &Node, root: &Shape, at: &YamlOwned) -> (String, Vec<Wire>) {
    match path_of(doc, root, &a(), &steps_of(at)) {
        Ok(p) => (p, Vec::new()),
        Err(Error::Contract(vs)) => (String::new(), vs.iter().map(wire).collect()),
        Err(other) => panic!("{id}: outside the contract: {other:?}"),
    }
}

fn document_of(case: &YamlOwned) -> Node {
    match get(case, "document_yaml") {
        Some(src) => parse(text(src, "document_yaml")).expect("document_yaml"),
        None => node_of(get(case, "document").expect("document")),
    }
}

fn expected_path_wires(expect: &YamlOwned) -> (String, Vec<Wire>) {
    if let Some(p) = get(expect, "path") {
        return (text(p, "path").to_string(), Vec::new());
    }
    let errors = get(expect, "errors")
        .and_then(YamlOwned::as_vec)
        .expect("errors")
        .iter()
        .map(|e| {
            (
                text(get(e, "code").expect("code"), "code").to_string(),
                text(get(e, "phase").expect("phase"), "phase").to_string(),
                "unavailable".to_string(),
                text(
                    get(e, "logical_path").expect("logical_path"),
                    "logical_path",
                )
                .to_string(),
            )
        })
        .collect();
    (String::new(), errors)
}

#[test]
fn path_cases_match_the_corpus() {
    let mut ran = 0;
    for (name, suite) in suites() {
        if name != "paths" {
            continue;
        }
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let kind = text(get(case, "kind").expect("kind"), "kind");
            if kind != "path_of" && kind != "path_stability" {
                continue;
            }
            ran += 1;
            let tree = text(
                get(
                    get(case, "schema")
                        .and_then(|s| get(s, "synthetic"))
                        .unwrap(),
                    "tree",
                )
                .unwrap(),
                "tree",
            );
            let root = path_shape(
                ShapeType::Composite(tree_fields(
                    get(get(&suite, "trees").expect("trees"), tree).expect("tree"),
                    &[],
                )),
                true,
            );
            let (want_path, want_err) = expected_path_wires(get(case, "expect").expect("expect"));
            if kind == "path_of" {
                let got = path_outcome(id, &document_of(case), &root, get(case, "at").expect("at"));
                assert_eq!(got, (want_path, want_err), "{id}");
                continue;
            }
            for (i, v) in get(case, "variants")
                .and_then(YamlOwned::as_vec)
                .expect("variants")
                .iter()
                .enumerate()
            {
                let got = path_outcome(id, &document_of(v), &root, get(v, "at").expect("at"));
                assert_eq!(
                    got,
                    (want_path.clone(), want_err.clone()),
                    "{id} variant {i}"
                );
            }
        }
    }
    assert_eq!(ran, 25, "18 path_of and 7 path_stability cases");
}

// ---------------------------------------------------------------------------
// expand, collapse, roundtrip, equality (ADR 0001 "Round trip", ADR 0003, ADR 0010): one primitive position.

/// Whether `n` is the typed value a fixture spells (`{type, value}`); `type: absent` is no node at all.
fn typed_matches(n: Option<&Node>, want: &YamlOwned) -> bool {
    if get(want, "type").and_then(YamlOwned::as_str) == Some("absent") {
        return n.is_none();
    }
    n.is_some_and(|n| n.same(&node_of(get(want, "value").expect("value"))))
}

fn fields_match(id: &str, n: &Node, fields: &YamlOwned) {
    for (name, want) in fields.as_mapping().expect("fields: a mapping") {
        let name = text(name, "field name");
        let got = match &n.value {
            Value::Map(es) => es.iter().find(|e| e.key == name).map(|e| &e.value),
            _ => None,
        };
        assert!(
            typed_matches(got, want),
            "{id}: field {name} does not match"
        );
    }
}

#[test]
fn expand_collapse_cases_match_the_corpus() {
    let g = bound_grammar();
    let mut ran = 0;
    for (_, suite) in suites() {
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let kind = text(get(case, "kind").expect("kind"), "kind");
            if !is_p2_run(kind) {
                continue;
            }
            ran += 1;
            let expect = get(case, "expect").expect("expect");
            match kind {
                "equality" => {
                    let a_node = node_of(get(case, "a").expect("a"));
                    let b_node = node_of(get(case, "b").expect("b"));
                    let want = get(expect, "equal").and_then(YamlOwned::as_bool).unwrap();
                    assert_eq!(a_node.same(&b_node), want, "{id}");
                }
                "expand" => {
                    let (out, authored) =
                        expand_position(&node_of(get(case, "input").unwrap()), &a(), g)
                            .unwrap_or_else(|e| panic!("{id}: {e:?}"));
                    assert!(!authored, "{id}");
                    fields_match(id, &out, get(expect, "fields").unwrap());
                }
                "collapse" => {
                    let input = node_of(get(case, "input").unwrap());
                    let authored = get(case, "authored_expanded")
                        .and_then(YamlOwned::as_bool)
                        .unwrap();
                    let basis = get(case, "basis").and_then(YamlOwned::as_str).unwrap_or("");
                    let out = collapse_position(&input, authored, basis, &a(), g);
                    let got = if out.same(&input) {
                        "expanded"
                    } else {
                        "short"
                    };
                    assert_eq!(
                        Some(got),
                        get(expect, "collapse").and_then(YamlOwned::as_str),
                        "{id}"
                    );
                    if let Some(f) = get(expect, "fields") {
                        fields_match(id, &out, f);
                    }
                }
                _ => {
                    let input = if get(case, "absent").is_some() {
                        None
                    } else {
                        Some(node_of(get(case, "input").unwrap()))
                    };
                    let Some(input) = input else {
                        // absent stays absent: there is no node to expand or collapse
                        assert!(
                            typed_matches(None, get(expect, "collapsed").unwrap()),
                            "{id}"
                        );
                        assert!(
                            typed_matches(None, get(expect, "expanded").unwrap()),
                            "{id}"
                        );
                        continue;
                    };
                    let (expanded, authored) =
                        expand_position(&input, &a(), g).unwrap_or_else(|e| panic!("{id}: {e:?}"));
                    let back = collapse_position(&expanded, authored, "", &a(), g);
                    if let Some(f) = get(expect, "fields") {
                        fields_match(id, &back, f);
                        continue;
                    }
                    let value = match &expanded.value {
                        Value::Map(es) => es.iter().find(|e| e.key == "value").map(|e| &e.value),
                        _ => None,
                    };
                    assert!(
                        typed_matches(value, get(expect, "expanded").unwrap()),
                        "{id}: expanded"
                    );
                    assert!(
                        typed_matches(Some(&back), get(expect, "collapsed").unwrap()),
                        "{id}: collapsed"
                    );
                }
            }
        }
    }
    assert_eq!(ran, 26, "expand 1, collapse 15, roundtrip 9, equality 1");
}

// ---------------------------------------------------------------------------
// path_parse, path_format, path_resolve (ADR 0006): the text of logical paths.

/// Segments as one comparable string, the same for parsed segments and for the fixture's.
fn segs_string(segs: &[PathSeg]) -> String {
    segs.iter()
        .map(|s| match s {
            PathSeg::Field(f) => format!("f:{f:?}"),
            PathSeg::Item(item) => format!(
                "i:[{}]",
                item.iter()
                    .map(|c| match &c.value {
                        KeyValue::Str(v) => format!("{:?}=s:{v:?}", c.name),
                        KeyValue::Int(n) => format!("{:?}=i:{n}", c.name),
                        other => format!("{:?}={other:?}", c.name),
                    })
                    .collect::<Vec<_>>()
                    .join(",")
            ),
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn segments_of(y: &YamlOwned) -> Vec<PathSeg> {
    y.as_vec()
        .expect("segments: a list")
        .iter()
        .map(|s| {
            if let Some(f) = get(s, "field") {
                return PathSeg::Field(text(f, "field").to_string());
            }
            PathSeg::Item(
                get(s, "item")
                    .and_then(YamlOwned::as_vec)
                    .expect("item")
                    .iter()
                    .map(|c| {
                        let key = get(c, "key").expect("key");
                        let value = match text(get(key, "type").expect("type"), "type") {
                            "string" => {
                                KeyValue::Str(text(get(key, "value").unwrap(), "value").to_string())
                            }
                            // beyond signed 64 bits: not an integer the loader could hold
                            "integer" => get(key, "value")
                                .and_then(YamlOwned::as_integer)
                                .map_or(KeyValue::OutOfRange, KeyValue::Int),
                            _ => KeyValue::Boolean(true),
                        };
                        KeyComp {
                            name: text(get(c, "name").expect("name"), "name").to_string(),
                            value,
                        }
                    })
                    .collect(),
            )
        })
        .collect()
}

#[test]
fn path_text_cases_match_the_corpus() {
    let mut ran = 0;
    for (name, suite) in suites() {
        if name != "paths" {
            continue;
        }
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            let id = text(get(case, "id").expect("id"), "id");
            let kind = text(get(case, "kind").expect("kind"), "kind");
            if !["path_parse", "path_format", "path_resolve"].contains(&kind) {
                continue;
            }
            ran += 1;
            let expect = get(case, "expect").expect("expect");
            let mut error = None;
            match kind {
                "path_parse" => {
                    let path = text(get(case, "path").unwrap(), "path");
                    match parse_path(path) {
                        Ok(segs) => {
                            assert_eq!(
                                segs_string(&segs),
                                segs_string(&segments_of(
                                    get(expect, "segments").expect("segments")
                                )),
                                "{id}"
                            );
                            // every accepted text is the canonical writing of its segments
                            assert_eq!(
                                format_path(&segs).as_deref(),
                                Ok(path),
                                "{id}: format(parse(t))"
                            );
                        }
                        Err(e) => error = Some(e),
                    }
                }
                "path_format" => match format_path(&segments_of(get(case, "segments").unwrap())) {
                    Ok(t) => assert_eq!(
                        Some(t.as_str()),
                        get(expect, "text").and_then(YamlOwned::as_str),
                        "{id}"
                    ),
                    Err(e) => error = Some(e),
                },
                _ => {
                    let segs =
                        parse_path(text(get(case, "path").unwrap(), "path")).unwrap_or_else(|e| {
                            panic!("{id}: the path to resolve does not parse: {e:?}")
                        });
                    let tree = text(
                        get(
                            get(case, "schema")
                                .and_then(|s| get(s, "synthetic"))
                                .unwrap(),
                            "tree",
                        )
                        .unwrap(),
                        "tree",
                    );
                    let root = path_shape(
                        ShapeType::Composite(tree_fields(
                            get(get(&suite, "trees").expect("trees"), tree).expect("tree"),
                            &[],
                        )),
                        true,
                    );
                    match resolve_path(&root, &segs) {
                        Ok(r) => {
                            assert_eq!(
                                Some(r.target),
                                get(expect, "target").and_then(YamlOwned::as_str),
                                "{id}"
                            );
                            assert_eq!(
                                Some(r.primitive),
                                get(expect, "primitive_position").and_then(YamlOwned::as_bool),
                                "{id}"
                            );
                        }
                        Err(e) => error = Some(e),
                    }
                }
            }
            match (get(expect, "errors").and_then(YamlOwned::as_vec), error) {
                (None, None) => {}
                (Some(errs), Some(e)) => {
                    let want = &errs[0];
                    assert_eq!(
                        Some(e.code),
                        get(want, "code").and_then(YamlOwned::as_str),
                        "{id}"
                    );
                    assert_eq!(
                        Some(e.phase.as_str()),
                        get(want, "phase").and_then(YamlOwned::as_str),
                        "{id}"
                    );
                    assert_eq!(
                        e.logical_path.as_deref().unwrap_or(""),
                        get(want, "logical_path")
                            .and_then(YamlOwned::as_str)
                            .unwrap_or(""),
                        "{id}"
                    );
                    if let Some(off) = get(want, "path_offset").and_then(YamlOwned::as_integer) {
                        assert_eq!(i64::try_from(e.offset).unwrap(), off, "{id}: offset");
                    }
                    assert_eq!(
                        get(get(want, "source_location").unwrap(), "state")
                            .and_then(YamlOwned::as_str),
                        Some("not_applicable"),
                        "{id}"
                    );
                }
                (want, got) => panic!("{id}: want {want:?}, got {got:?}"),
            }
        }
    }
    assert_eq!(
        ran, 100,
        "57 path_parse, 9 path_format and 34 path_resolve cases"
    );
}

// ---------------------------------------------------------------------------
// kind `source_span` (ADR 0012, step E2): where the scalar of a position is, in the original text.

/// The source text of a case: `input_yaml`, or the bytes of `input_hex` (what a text field cannot spell: a lone CR, a BOM).
fn source_of(case: &YamlOwned) -> String {
    if let Some(t) = get(case, "input_yaml") {
        return text(t, "input_yaml").to_string();
    }
    let hex = text(
        get(case, "input_hex").expect("input_yaml or input_hex"),
        "input_hex",
    );
    let bytes: Vec<u8> = (0..hex.len() / 2)
        .map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).expect("hex"))
        .collect();
    String::from_utf8(bytes).expect("the source of a source_span case is UTF-8")
}

fn expected_edit_wires(expect: &YamlOwned) -> Vec<Wire> {
    get(expect, "errors")
        .and_then(YamlOwned::as_vec)
        .expect("errors")
        .iter()
        .map(|e| {
            let sl = get(e, "source_location").expect("source_location");
            let loc = match get(sl, "state") {
                Some(state) => text(state, "state").to_string(),
                None => {
                    let n = |k: &str| get(sl, k).and_then(YamlOwned::as_integer).expect("integer");
                    format!("{}:{}:{}", n("line"), n("column"), n("offset"))
                }
            };
            (
                text(get(e, "code").expect("code"), "code").to_string(),
                text(get(e, "phase").expect("phase"), "phase").to_string(),
                loc,
                get(e, "logical_path")
                    .map_or(String::new(), |p| text(p, "logical_path").to_string()),
            )
        })
        .collect()
}

#[test]
fn source_span_cases_match_the_corpus() {
    let mut ran = 0;
    for (name, suite) in suites() {
        if name != "edit" {
            continue;
        }
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            if text(get(case, "kind").expect("kind"), "kind") != "source_span" {
                continue;
            }
            ran += 1;
            let id = text(get(case, "id").expect("id"), "id");
            let tree = text(
                get(
                    get(case, "schema")
                        .and_then(|s| get(s, "synthetic"))
                        .unwrap(),
                    "tree",
                )
                .unwrap(),
                "tree",
            );
            let root = path_shape(
                ShapeType::Composite(tree_fields(
                    get(get(&suite, "trees").expect("trees"), tree).expect("tree"),
                    &[],
                )),
                true,
            );
            let got = source_span(
                &source_of(case),
                &root,
                text(get(case, "path").expect("path"), "path"),
                &a(),
            );
            let expect = get(case, "expect").expect("expect");
            if let Some(span) = get(expect, "span") {
                let n = |k: &str| {
                    usize::try_from(
                        get(span, k)
                            .and_then(YamlOwned::as_integer)
                            .expect("integer"),
                    )
                    .expect("offset")
                };
                let got = got.unwrap_or_else(|e| panic!("{id}: a span was expected, got {e:?}"));
                assert_eq!(
                    (got.start, got.end, got.style.as_str()),
                    (
                        n("start"),
                        n("end"),
                        text(get(span, "style").expect("style"), "style")
                    ),
                    "{id}"
                );
            } else {
                match got {
                    Err(Error::Contract(vs)) => assert_eq!(
                        vs.iter().map(wire).collect::<Vec<_>>(),
                        expected_edit_wires(expect),
                        "{id}"
                    ),
                    other => panic!("{id}: an error of the contract was expected, got {other:?}"),
                }
            }
        }
    }
    assert_eq!(ran, 28, "the 28 source_span cases of corpus/edit");
}

fn edit_root() -> Shape {
    let (_, suite) = suites()
        .into_iter()
        .find(|(n, _)| n == "edit")
        .expect("the edit suite");
    path_shape(
        ShapeType::Composite(tree_fields(
            get(get(&suite, "trees").expect("trees"), "doc").expect("tree"),
            &[],
        )),
        true,
    )
}

fn span_of(text: &str, path: &str) -> Result<(usize, usize, &'static str), Vec<Wire>> {
    match source_span(text, &edit_root(), path, &a()) {
        Ok(s) => Ok((s.start, s.end, s.style.as_str())),
        Err(Error::Contract(vs)) => Err(vs.iter().map(wire).collect()),
        Err(other) => panic!("{path}: outside the contract: {other:?}"),
    }
}

fn refusal(code: &str, phase: &str, loc: &str, path: &str) -> Vec<Wire> {
    vec![(code.into(), phase.into(), loc.into(), path.into())]
}

/// The places a vector cannot reach (the trees of ADR 0005 have no nullable): a position the document does not spell, a node of the wrong kind,
/// a wrapper in a flow collection, the path errors, and the scanner at its edges.
#[test]
fn source_span_edge_cases() {
    // a value the source does not spell has no text: the error is anchored at the enclosing structure
    assert_eq!(
        span_of("name:\ncount: 3\n", "$.name"),
        Err(refusal("EDIT_NO_SOURCE_VALUE", "edit", "1:1:0", "$.name"))
    );
    assert_eq!(
        span_of("count: 3\n", "$.ip.gateway"),
        Err(refusal(
            "EDIT_NO_SOURCE_VALUE",
            "edit",
            "1:1:0",
            "$.ip.gateway"
        ))
    );
    // a composite position is not a scalar, present or absent (the absent one is anchored at its parent)
    assert_eq!(
        span_of("count: 3\n", "$.ip"),
        Err(refusal("EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$.ip"))
    );
    assert_eq!(
        span_of("name: {a: 1}\n", "$.name"),
        Err(refusal("EDIT_NOT_A_SCALAR", "edit", "1:7:6", "$.name"))
    );
    assert_eq!(
        span_of("name: [a]\n", "$.name"),
        Err(refusal("EDIT_NOT_A_SCALAR", "edit", "1:7:6", "$.name"))
    );
    assert_eq!(
        span_of("name: a\n", "$"),
        Err(refusal("EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$"))
    );
    // an element that is not in the list: anchored at the list
    assert_eq!(
        span_of(
            "routes:\n  - to: a\n    metric: 1\n",
            "$.routes[{to=\"z\"}].metric"
        ),
        Err(refusal(
            "EDIT_NO_SOURCE_VALUE",
            "edit",
            "2:3:10",
            "$.routes[{to=\"z\"}].metric"
        ))
    );
    // an expanded wrapper written in flow style: its value is in a flow collection
    let wrapper = "name: {value: alpha, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}\n";
    assert_eq!(
        span_of(wrapper, "$.name"),
        Err(refusal("EDIT_FLOW_CONTEXT", "edit", "1:15:14", "$.name"))
    );
    // the refusals of the earlier phases are not changed
    assert_eq!(
        span_of("name: a\n", "name"),
        Err(refusal("PATH_SYNTAX", "path_parse", "not_applicable", ""))
    );
    assert_eq!(
        span_of("name: a\n", "$.nope"),
        Err(refusal(
            "UNKNOWN_FIELD",
            "path_resolve",
            "not_applicable",
            "$.nope"
        ))
    );
    assert!(matches!(
        source_span("name: [\n", &edit_root(), "$.name", &a()),
        Err(Error::Syntax(_))
    ));
    // the scanner at its edges
    assert_eq!(
        span_of("name: \"a\\\\\"\n", "$.name"),
        Ok((6, 11, "double_quoted")),
        "an escaped backslash does not end the scalar early"
    );
    assert_eq!(
        span_of("name: \"a\" # c\n", "$.name"),
        Ok((6, 9, "double_quoted"))
    );
    assert_eq!(
        span_of("name: 'a' # c\n", "$.name"),
        Ok((6, 9, "single_quoted"))
    );
    assert_eq!(
        span_of("name: a#b\n", "$.name"),
        Ok((6, 9, "plain")),
        "a # that does not follow a space is part of the scalar"
    );
    assert_eq!(
        span_of("name: alpha  \ncount: 3\n", "$.name"),
        Ok((6, 11, "plain")),
        "trailing spaces are not in the span"
    );
}

/// The offset of a path_parse error is present there and nowhere else; and a list below a composite is found by its identity.
#[test]
fn source_span_path_errors_and_a_list_below_a_composite() {
    let offset = |path: &str| match source_span("name: a\n", &edit_root(), path, &a()) {
        Err(Error::Contract(vs)) => vs[0].path_offset,
        other => panic!("{path}: {other:?}"),
    };
    assert_eq!(
        offset("name"),
        Some(0),
        "a path_parse error carries the offset in the path text"
    );
    assert_eq!(offset("$.nope"), None, "a path_resolve error carries none");
    let tree = YamlOwned::load_from_str(
        "box: {composite: {routes: {list: {key: [to], fields: {to: {scalar: string}, metric: {scalar: integer}}}}}}",
    )
    .expect("tree")
    .remove(0);
    let root = path_shape(ShapeType::Composite(tree_fields(&tree, &[])), true);
    let text = "box:\n  routes:\n    - to: a\n      metric: 1\n    - to: b\n      metric: 2\n";
    let span = source_span(text, &root, "$.box.routes[{to=\"b\"}].metric", &a()).expect("a span");
    assert_eq!(
        (span.start, span.end, span.style.as_str()),
        (69, 70, "plain")
    );
    assert_eq!(&text[span.start..span.end], "2");
}

/// An empty value (`ip:`) has no position of its own: a composite target there is refused at the enclosing structure.
/// The Go peer is TestSourceSpanAnEmptyValueIsNotAScalarTarget.
#[test]
fn source_span_an_empty_value_is_not_a_scalar_target() {
    assert_eq!(
        span_of("ip:\ncount: 3\n", "$.ip"),
        Err(refusal("EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$.ip"))
    );
}

// ---------------------------------------------------------------------------
// kind `edit` (ADR 0012, step E3): replace one scalar of a document in its original text.

/// The new value of an edit case: `{string: s}`, `{integer: n}`, `{boolean: b}` or `{null: true}`.
fn typed_value(v: &YamlOwned) -> Node {
    if let Some(s) = get(v, "string") {
        return Node::new(Value::Str(text(s, "string").to_string()));
    }
    if let Some(i) = get(v, "integer") {
        return Node::new(Value::Int(i.as_integer().expect("integer")));
    }
    if let Some(b) = get(v, "boolean") {
        return Node::new(Value::Bool(b.as_bool().expect("boolean")));
    }
    assert!(get(v, "null").is_some(), "a typed value");
    Node::new(Value::Null)
}

/// The document of a case that was given as a parsed value (a map of scalars), not as text.
fn parsed_input(case: &YamlOwned) -> Node {
    let input = get(case, "input").expect("input");
    assert_eq!(text(get(input, "type").expect("type"), "type"), "map");
    let entries = get(input, "value")
        .and_then(YamlOwned::as_mapping)
        .expect("value");
    Node::map(
        entries
            .iter()
            .map(|(k, v)| {
                let value = match v {
                    YamlOwned::Value(ScalarOwned::Integer(i)) => Value::Int(*i),
                    YamlOwned::Value(ScalarOwned::Boolean(b)) => Value::Bool(*b),
                    other => Value::Str(text(other, "value").to_string()),
                };
                (text(k, "key"), Node::new(value))
            })
            .collect(),
    )
}

fn hex_text(hex: &str) -> String {
    let bytes: Vec<u8> = (0..hex.len() / 2)
        .map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).expect("hex"))
        .collect();
    String::from_utf8(bytes).expect("the output of an edit case is UTF-8")
}

fn span_tuple(s: &cic_primitive_forms::SourceSpan) -> (usize, usize) {
    (s.start, s.end)
}

fn span_pair(v: &YamlOwned) -> (usize, usize) {
    let n = |k: &str| {
        usize::try_from(get(v, k).and_then(YamlOwned::as_integer).expect("integer"))
            .expect("offset")
    };
    (n("start"), n("end"))
}

#[test]
fn edit_cases_match_the_corpus() {
    let mut ran = 0;
    for (name, suite) in suites() {
        if name != "edit" {
            continue;
        }
        for case in get(&suite, "cases")
            .and_then(YamlOwned::as_vec)
            .expect("cases")
        {
            if text(get(case, "kind").expect("kind"), "kind") != "edit" {
                continue;
            }
            ran += 1;
            let id = text(get(case, "id").expect("id"), "id");
            let tree = text(
                get(
                    get(case, "schema")
                        .and_then(|s| get(s, "synthetic"))
                        .unwrap(),
                    "tree",
                )
                .unwrap(),
                "tree",
            );
            let root = path_shape(
                ShapeType::Composite(tree_fields(
                    get(get(&suite, "trees").expect("trees"), tree).expect("tree"),
                    &[],
                )),
                true,
            );
            let value = typed_value(get(case, "value").expect("value"));
            let path = text(get(case, "path").expect("path"), "path");
            let expect = get(case, "expect").expect("expect");
            let got: Result<_, Error> = if get(case, "input").is_some() {
                Err(edit_parsed(&parsed_input(case), &root, path, &a(), &value))
            } else {
                edit_scalar(&source_of(case), &root, path, &a(), &value)
            };
            if get(expect, "errors").is_some() {
                match got {
                    Err(Error::Contract(vs)) => assert_eq!(
                        vs.iter().map(wire).collect::<Vec<_>>(),
                        expected_edit_wires(expect),
                        "{id}"
                    ),
                    other => panic!("{id}: an error of the contract was expected, got {other:?}"),
                }
                continue;
            }
            let res = got.unwrap_or_else(|e| panic!("{id}: an edit was expected, got {e:?}"));
            let want = get(expect, "output_yaml").map_or_else(
                || {
                    hex_text(text(
                        get(expect, "output_hex").expect("output"),
                        "output_hex",
                    ))
                },
                |y| text(y, "output_yaml").to_string(),
            );
            assert_eq!(res.text.as_bytes(), want.as_bytes(), "{id}"); // compared as bytes
            let src = source_of(case);
            let before =
                source_span(&src, &root, path, &a()).unwrap_or_else(|e| panic!("{id}: {e:?}"));
            assert_eq!(
                span_tuple(&before),
                span_pair(get(expect, "span_before").expect("span_before")),
                "{id}: the span before"
            );
            // an edit of the key of a list element changes the address of the scalar
            let path_after = get(case, "path_after").map_or(path, |p| text(p, "path_after"));
            let after = source_span(&res.text, &root, path_after, &a())
                .unwrap_or_else(|e| panic!("{id}: {e:?}"));
            assert_eq!(
                span_tuple(&after),
                span_pair(get(expect, "span_after").expect("span_after")),
                "{id}: the span after"
            );
            assert_eq!(after, res.span, "{id}: the span of the result");
        }
    }
    assert_eq!(ran, 44, "the 44 edit cases of corpus/edit");
}
