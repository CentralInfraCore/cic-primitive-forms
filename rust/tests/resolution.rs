//! The shared PolicyResolution cases (`testdata/p1/resolution.json`): a document is normalized, then every position of the object is
//! resolved. The expectations were derived from the rules of the reference resolution (`access_policy.py`, in the pin), not from this
//! code's output. The Go peer is `go/policyresolution_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_grammar, compile_schema, denormalize, normalize, parse, resolve_policy, CompiledSchema,
    Context, EffectiveRule, Grammar, Mode, PolicySurface, Presence, RuleKind, ScalarType,
    SchemaInput, Shape, ShapeType, Value,
};
use serde_json::Value as J;
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn bound(v: i64) -> Grammar {
    let n = Value::Int(v);
    bind_grammar(Some(&n), Some(&n)).unwrap()
}

fn compile(schema: &str, pin: &str) -> CompiledSchema {
    compile_schema(&SchemaInput {
        bytes: schema.as_bytes(),
        sha256: &sha256_hex(schema.as_bytes()),
        grammar: pin,
        surface: "config_surface",
    })
    .unwrap()
}

fn row(path: &str, op: &str, r: &EffectiveRule) -> J {
    let kind = match r.kind {
        RuleKind::List => "list",
        RuleKind::Open => "open",
        RuleKind::Owner => "owner",
        RuleKind::None => "none",
        RuleKind::Unknown => "unknown",
    };
    let pats = if r.kind == RuleKind::List {
        J::from(r.patterns.clone())
    } else {
        J::Null
    };
    J::from(vec![
        J::from(path),
        J::from(op),
        J::from(kind),
        J::from(r.layer),
        J::from(r.source_path.clone()),
        pats,
        J::from(r.reason),
    ])
}

struct Loaded {
    json: J,
    a: Vec<String>,
}

fn load() -> Loaded {
    let text = std::fs::read_to_string(repo_root().join("testdata/p1/resolution.json")).unwrap();
    let json: J = serde_json::from_str(&text).unwrap();
    let a = json["access_keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect();
    Loaded { json, a }
}

fn unknown_of(c: &J) -> Vec<String> {
    c["unknown"]
        .as_array()
        .map(|u| u.iter().map(|s| s.as_str().unwrap().to_string()).collect())
        .unwrap_or_default()
}

#[test]
fn shared_policy_resolution_cases() {
    let f = load();
    let pin = f.json["grammar_pin"].as_str().unwrap();
    for c in f.json["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let cs = compile(c["schema"].as_str().unwrap(), pin);
        let doc = parse(c["yaml"].as_str().unwrap()).unwrap();
        let g = bound(c["grammar"].as_i64().unwrap());
        let res = normalize(&doc, &cs.root, &f.a, g, &Context::default()).unwrap();
        let pr = resolve_policy(
            &res.object,
            &cs.root,
            &cs.policy,
            "config_surface",
            &f.a,
            g,
            &unknown_of(c),
        )
        .unwrap();
        let positions: Vec<&str> = pr.keys().map(String::as_str).collect();
        let want: Vec<&str> = c["positions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p.as_str().unwrap())
            .collect();
        assert_eq!(positions, want, "{id}: positions");
        for r in c["expect"].as_array().unwrap() {
            let (path, op) = (r[0].as_str().unwrap(), r[1].as_str().unwrap());
            let e = &pr[path];
            let got = if op == "modify" {
                row(path, op, &e.modify)
            } else {
                row(path, op, &e.access)
            };
            assert_eq!(&got, r, "{id}: {path} {op}");
        }
        // the resolution leaves the normalization alone: the authored document still comes back
        let back = denormalize(&res, &cs.root, &f.a, g, &Context::default()).unwrap();
        assert!(back.same(&doc), "{id}: denormalize after the resolution");
    }
}

#[test]
fn resolution_does_not_depend_on_the_order_of_the_list_elements() {
    let f = load();
    let pin = f.json["grammar_pin"].as_str().unwrap();
    for c in f.json["cases"].as_array().unwrap() {
        if !c["id"].as_str().unwrap().starts_with("list-elements") {
            continue;
        }
        let cs = compile(c["schema"].as_str().unwrap(), pin);
        let g = bound(c["grammar"].as_i64().unwrap());
        let y = c["yaml"].as_str().unwrap();
        let run = |text: &str| {
            let res = normalize(
                &parse(text).unwrap(),
                &cs.root,
                &f.a,
                g,
                &Context::default(),
            )
            .unwrap();
            resolve_policy(
                &res.object,
                &cs.root,
                &cs.policy,
                "config_surface",
                &f.a,
                g,
                &[],
            )
            .unwrap()
        };
        let first = "    - to: b\n      metric:\n";
        let (i, j, k) = (
            y.find(first),
            y.find("    - to: a\n"),
            y.find("  access: [O=r]"),
        );
        let (i, j, k) = (i.unwrap(), j.unwrap(), k.unwrap());
        assert!(
            j > i && k > j,
            "the case text changed: the reorder found nothing"
        );
        let swapped = format!("{}{}{}{}", &y[..i], &y[j..k], &y[i..j], &y[k..]);
        assert_eq!(
            run(y),
            run(&swapped),
            "a different order of the elements gave another resolution"
        );
    }
}

#[test]
fn resolution_refuses_what_it_cannot_describe() {
    let f = load();
    let c = &f.json["cases"][0];
    let cs = compile(
        c["schema"].as_str().unwrap(),
        f.json["grammar_pin"].as_str().unwrap(),
    );
    let g = bound(1);
    let ctx = Context::default();
    let go = |obj: &cic_primitive_forms::Node, unknown: &[String]| {
        resolve_policy(
            obj,
            &cs.root,
            &cs.policy,
            "config_surface",
            &f.a,
            g,
            unknown,
        )
    };
    // a short position is not an expanded one
    assert!(go(&parse("type: t\n").unwrap(), &[]).is_err());
    let res = normalize(&parse("type: t\n").unwrap(), &cs.root, &f.a, g, &ctx).unwrap();
    assert!(go(&res.object, &["$.nowhere".to_string()]).is_err());
    let bad = parse("bogus:\n  value: 1\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n").unwrap();
    assert!(go(&bad, &[]).is_err());
}

/// An expanded position around `v`, with the access, modify and inherit members given as text.
fn wrapped(v: &str, access: &str, modify: &str, inherit: &str) -> String {
    format!("{{value: {v}, access: {access}, modify: {modify}, inherit: {inherit}, default_injection: ~, conformance: implemented}}")
}

#[test]
fn resolution_refuses_every_structure_it_cannot_walk() {
    let f = load();
    let c = &f.json["cases"][0];
    let cs = compile(
        c["schema"].as_str().unwrap(),
        f.json["grammar_pin"].as_str().unwrap(),
    );
    let g = bound(1);
    let ok = |v: &str| wrapped(v, "inherit", "inherit", "true");
    let cases = [
        (
            "an access value that is not a rule",
            format!("type: {}\n", wrapped("t", "bogus", "inherit", "true")),
            "access of the position",
        ),
        (
            "a modify value that is not a rule",
            format!("type: {}\n", wrapped("t", "inherit", "bogus", "true")),
            "modify of the position",
        ),
        (
            "an inherit value that is not a mode",
            format!("type: {}\n", wrapped("t", "inherit", "inherit", "maybe")),
            "inherit of the position",
        ),
        (
            "a list position that holds a scalar",
            format!("routes: {}\n", ok("5")),
            "a value that is not a list",
        ),
        (
            "a list element that is not a record",
            format!("routes: {}\n", ok("[1]")),
            "not a record",
        ),
        (
            "a list element without an identity",
            format!("routes: {}\n", ok(&format!("[{{metric: {}}}]", ok("1")))),
            "without an identity",
        ),
        (
            "a short position inside a list element",
            format!("routes: {}\n", ok("[{to: a}]")),
            ".to is not an expanded",
        ),
        (
            "an undeclared field in a list element",
            format!(
                "routes: {}\n",
                ok(&format!("[{{to: {}, zzz: {}}}]", ok("a"), ok("1")))
            ),
            "is not a field the schema declares",
        ),
        (
            "an undeclared field in a composite",
            format!("ip: {}\n", ok(&format!("{{zzz: {}}}", ok("1")))),
            "is not a field the schema declares",
        ),
        (
            "a short position in a composite",
            format!("ip: {}\n", ok("{gateway: g}")),
            "$.ip.gateway is not an expanded",
        ),
        (
            "a value that is not a mapping",
            format!("ip: {}\n", ok("5")),
            "where a mapping is declared",
        ),
    ];
    for (name, y, text) in cases {
        let doc = parse(&y).unwrap();
        let err = resolve_policy(&doc, &cs.root, &cs.policy, "config_surface", &f.a, g, &[])
            .expect_err(&format!("resolved what it cannot describe: {name}"));
        assert!(
            format!("{err:?}").contains(text),
            "{name}: refused for another reason: {err:?}"
        );
    }
}

/// A field name that cannot be written as an ADR 0006 path (a control character) has no key in the resolution: it is refused. An identity is always writable (it is escaped).
/// The Go peer is `TestPolicyResolutionRefusesAPositionItCannotName`.
#[test]
fn resolution_refuses_a_position_it_cannot_name() {
    let f = load();
    let g = bound(1);
    let name = format!("a{}b", char::from(7u8)); // built, not written: a control character in a source file is easy to lose
    let leaf = Shape {
        shape_type: ShapeType::Scalar(ScalarType::String),
        presence: Presence::Optional,
        nullable: false,
        has_default: false,
        default: None,
    };
    let root = Shape {
        shape_type: ShapeType::Composite(vec![(name, leaf)]),
        presence: Presence::Mandatory,
        nullable: false,
        has_default: false,
        default: None,
    };
    let doc = parse(&format!(
        "\"a\\ab\": {}\n",
        wrapped("y", "inherit", "inherit", "true")
    ))
    .unwrap();
    let surface = PolicySurface {
        declared: false,
        default_access: None,
        default_modify: None,
        surface_rules: Vec::new(),
        inherit_policy: Mode::Inherit,
        inherit_policy_declared: false,
    };
    assert!(resolve_policy(&doc, &root, &surface, "config_surface", &f.a, g, &[]).is_err());
}
