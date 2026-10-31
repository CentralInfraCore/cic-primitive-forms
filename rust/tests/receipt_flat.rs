//! The flat receipt (P5-3, `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 17): the shared cases of `testdata/p5/receipt.json` (expected values computed by
//! `tools/p5/receipt_cases.py`, in Python), and the refusals. The engine's verifier is run over the same receipts by `tools/p5/engine-harness-rust`. The Go peer
//! is `go/receipt_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    parse, receipt_flat_fixture, EngineIdentity, Entry, IdentityFailure, Node, ReceiptError,
    ReleaseFixture, SchemaInput, Value, MATERIALIZE_CAPABILITY_MISSING, MATERIALIZE_NO_DOCUMENT,
    MATERIALIZE_UNSUPPORTED, RECEIPT_ENGINE_FIELD, RECEIPT_PATH_NOT_ONE_SEGMENT,
    RECEIPT_RELEASE_FIELD, RECEIPT_SCHEMA_VERSION_EMPTY, SCHEMA_INVALID_IDENTITY,
    SCHEMA_SOURCE_BINDING,
};
use serde_json::Value as J;
use std::collections::BTreeMap;
use std::path::PathBuf;

const IDENTITY: &str = "cic-primitive-forms-rust";

fn root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn load(name: &str) -> J {
    let text = std::fs::read_to_string(root().join("testdata/p5").join(name)).unwrap();
    serde_json::from_str(&text).unwrap()
}

fn strings(v: &J) -> Vec<String> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().unwrap().to_string())
        .collect()
}

fn release(file: &J) -> ReleaseFixture {
    let r = &file["release"];
    let s = |k: &str| r[k].as_str().unwrap().to_string();
    ReleaseFixture {
        primitive_release: s("primitive_release"),
        primitive_source_commit: s("primitive_source_commit"),
        primitive_build_hash: s("primitive_build_hash"),
        grammar_sha256: s("grammar_sha256"),
        grammar_schema_sha256: s("grammar_schema_sha256"),
    }
}

fn engine(file: &J) -> EngineIdentity {
    EngineIdentity {
        identity: IDENTITY.to_string(),
        version: file["engine_version"].as_str().unwrap().to_string(),
    }
}

fn input<'a>(text: &'a str, sha: &'a str, grammar: &'a str) -> SchemaInput<'a> {
    SchemaInput {
        bytes: text.as_bytes(),
        sha256: sha,
        grammar,
        surface: "config_surface",
    }
}

/// The first accepted case of the materialize file, to be altered one thing at a time.
struct Base {
    schema: String,
    grammar: String,
    a: Vec<String>,
    yaml: String,
    caps: BTreeMap<String, String>,
    release: ReleaseFixture,
    engine: EngineIdentity,
}

fn base() -> Base {
    let m = load("materialize.json");
    let r = load("receipt.json");
    let c = &m["cases"][0];
    Base {
        schema: m["schemas"][c["schema"].as_str().unwrap()]
            .as_str()
            .unwrap()
            .to_string(),
        grammar: m["grammar"].as_str().unwrap().to_string(),
        a: strings(&m["access_keys"]),
        yaml: c["yaml"].as_str().unwrap().to_string(),
        caps: c["capabilities"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect(),
        release: release(&r),
        engine: engine(&r),
    }
}

impl Base {
    fn run(&self) -> Result<cic_primitive_forms::FlatReceipt, ReceiptError> {
        self.run_with(&self.schema, Some(&self.yaml))
    }

    fn run_with(
        &self,
        schema: &str,
        yaml: Option<&str>,
    ) -> Result<cic_primitive_forms::FlatReceipt, ReceiptError> {
        let sha = sha256_hex(schema.as_bytes());
        let doc = yaml.map(|y| parse(y).unwrap());
        receipt_flat_fixture(
            &input(schema, &sha, &self.grammar),
            doc.as_ref(),
            &self.a,
            &self.caps,
            &self.release,
            &self.engine,
        )
    }
}

fn refusal(e: ReceiptError) -> (&'static str, String) {
    match e {
        ReceiptError::Refused { reason, field } => (reason, field),
        other => panic!("not a refusal of the receipt module: {other:?}"),
    }
}

#[test]
fn the_shared_receipt_cases() {
    let m = load("materialize.json");
    let r = load("receipt.json");
    let token = r["engine_identity_token"].as_str().unwrap();
    let a = strings(&m["access_keys"]);
    let grammar = m["grammar"].as_str().unwrap();
    let mut n = 0;
    for c in r["cases"].as_array().unwrap() {
        let id = c["id"].as_str().unwrap();
        let mc = m["cases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|x| x["id"] == c["id"])
            .unwrap();
        let schema = m["schemas"][mc["schema"].as_str().unwrap()]
            .as_str()
            .unwrap();
        let caps: BTreeMap<String, String> = mc["capabilities"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        let sha = sha256_hex(schema.as_bytes());
        let doc = parse(mc["yaml"].as_str().unwrap()).unwrap();
        let got = receipt_flat_fixture(
            &input(schema, &sha, grammar),
            Some(&doc),
            &a,
            &caps,
            &release(&r),
            &engine(&r),
        )
        .unwrap_or_else(|e| panic!("{id}: {e:?}"));
        let want = c["document"].as_str().unwrap().replace(token, IDENTITY);
        assert_eq!(got.document, want, "{id}: the canonical bytes");
        assert_eq!(
            got.digest,
            format!("sha256:{}", sha256_hex(want.as_bytes())),
            "{id}: the digest"
        );
        assert_eq!(
            got.authored_input,
            c["authored_input"].as_str().unwrap(),
            "{id}"
        );
        let binding: BTreeMap<String, String> = c["binding"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
            .collect();
        assert_eq!(got.binding, binding, "{id}: the path binding");
        assert_eq!(
            got.materialized.projection_json,
            mc["expect"]["projection"].as_str().unwrap(),
            "{id}: the object the receipt describes"
        );
        n += 1;
    }
    assert_eq!(n, 7);
}

#[test]
fn the_receipt_names_the_schema_it_was_made_from() {
    let b = base();
    let first = b.run().unwrap();
    // the same document under another schema file (one more space after the version) is another digest and another receipt
    let other = b.schema.replace("version: v1.0.0", "version: v1.0.0 ");
    let second = b.run_with(&other, Some(&b.yaml)).unwrap();
    assert_ne!(first.document, second.document);
    assert!(second.document.contains(&format!(
        "\"schema_digest\":\"sha256:{}\"",
        sha256_hex(other.as_bytes())
    )));
}

#[test]
fn a_schema_that_is_not_the_pinned_bytes_makes_no_receipt() {
    let b = base();
    let sha = sha256_hex(b.schema.as_bytes());
    let altered = b.schema.replace("kind: Demo", "kind: Demx");
    let doc = parse(&b.yaml).unwrap();
    let e = receipt_flat_fixture(
        &input(&altered, &sha, &b.grammar),
        Some(&doc),
        &b.a,
        &b.caps,
        &b.release,
        &b.engine,
    )
    .unwrap_err();
    assert!(matches!(
        e,
        ReceiptError::Identity(IdentityFailure::Schema(_))
    ));
}

#[test]
fn a_schema_whose_identity_breaks_the_contract_makes_no_receipt() {
    let b = base();
    let bad = b
        .schema
        .replace("namespace: \"cic:demo\"", "namespace: \"cic::demo\"");
    match b.run_with(&bad, Some(&b.yaml)).unwrap_err() {
        ReceiptError::Identity(IdentityFailure::Identity(e)) => {
            assert_eq!(e.code, SCHEMA_INVALID_IDENTITY);
            assert_eq!(e.rule, "R-SCHEMA-NAME-NAMESPACE");
        }
        other => panic!("{other:?}"),
    }
    // a digest that is not the digest of the bytes is the binding error of the schema compile, before any identity rule
    let sha = sha256_hex(b.schema.as_bytes());
    let doc = parse(&b.yaml).unwrap();
    let wrong = sha256_hex(b"other");
    let e = receipt_flat_fixture(
        &input(&b.schema, &wrong, &b.grammar),
        Some(&doc),
        &b.a,
        &b.caps,
        &b.release,
        &b.engine,
    )
    .unwrap_err();
    assert!(matches!(
        e,
        ReceiptError::Identity(IdentityFailure::Schema(_))
    ));
    assert_ne!(sha, wrong);
    assert_eq!(SCHEMA_SOURCE_BINDING, "E_SCHEMA_SOURCE_BINDING");
}

#[test]
fn an_empty_schema_version_makes_no_receipt() {
    let b = base();
    let empty = b.schema.replace("version: v1.0.0", "version: \"\"");
    let (reason, field) = refusal(b.run_with(&empty, Some(&b.yaml)).unwrap_err());
    assert_eq!(
        (reason, field.as_str()),
        (RECEIPT_SCHEMA_VERSION_EMPTY, "schema_version")
    );
}

#[test]
fn a_missing_schema_version_makes_no_receipt() {
    let b = base();
    let missing = b.schema.replace("    version: v1.0.0\n", "");
    let (reason, field) = refusal(b.run_with(&missing, Some(&b.yaml)).unwrap_err());
    assert_eq!(
        (reason, field.as_str()),
        (RECEIPT_SCHEMA_VERSION_EMPTY, "schema_version")
    );
}

#[test]
fn every_release_and_engine_field_is_required_and_the_digests_have_their_form() {
    let digest_ok = format!("sha256:{}", "a".repeat(64));
    type Edit = fn(&mut Base, String);
    let cases: Vec<(&str, &str, Edit)> = vec![
        ("primitive_release", RECEIPT_RELEASE_FIELD, |b, v| {
            b.release.primitive_release = v
        }),
        ("primitive_source_commit", RECEIPT_RELEASE_FIELD, |b, v| {
            b.release.primitive_source_commit = v
        }),
        ("primitive_build_hash", RECEIPT_RELEASE_FIELD, |b, v| {
            b.release.primitive_build_hash = v
        }),
        ("grammar_sha256", RECEIPT_RELEASE_FIELD, |b, v| {
            b.release.grammar_sha256 = v
        }),
        ("grammar_schema_sha256", RECEIPT_RELEASE_FIELD, |b, v| {
            b.release.grammar_schema_sha256 = v
        }),
        ("engine_identity", RECEIPT_ENGINE_FIELD, |b, v| {
            b.engine.identity = v
        }),
        ("engine_version", RECEIPT_ENGINE_FIELD, |b, v| {
            b.engine.version = v
        }),
    ];
    for (name, code, edit) in cases {
        let mut b = base();
        edit(&mut b, String::new());
        let (reason, field) = refusal(b.run().unwrap_err());
        assert_eq!((reason, field.as_str()), (code, name), "{name}: empty");
        let digest_field = name.ends_with("hash") || name.starts_with("grammar");
        for bad in [
            digest_ok.to_uppercase().replace("SHA256", "sha256"),
            digest_ok.replacen("sha256:", "", 1),
            format!("sha256:{}", "a".repeat(63)),
        ] {
            let mut b = base();
            edit(&mut b, bad);
            if digest_field {
                let (reason, field) = refusal(b.run().unwrap_err());
                assert_eq!((reason, field.as_str()), (code, name), "{name}: form");
            } else {
                assert!(b.run().is_ok(), "{name}: a free-form text is not a digest");
            }
        }
        let mut b = base();
        edit(&mut b, digest_ok.clone());
        assert!(b.run().is_ok(), "{name}: a well-formed value");
    }
}

#[test]
fn a_field_name_that_is_not_one_plain_segment_makes_no_receipt() {
    for name in ["a.b", "a[0]", "a{b", "a=b", "a,b", "a\\b", "a]"] {
        let mut b = base();
        let key = format!("      - name: \"{}\"\n", name.replace('\\', "\\\\"));
        let schema = b.schema.replace(
            "      - name: type\n",
            &format!("{key}        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n      - name: type\n"),
        );
        b.caps.insert(name.to_string(), "implemented".to_string());
        let yaml = format!("\"{}\": v\ntype: t\n", name.replace('\\', "\\\\"));
        let (reason, field) = refusal(b.run_with(&schema, Some(&yaml)).unwrap_err());
        assert_eq!(
            (reason, field.as_str()),
            (RECEIPT_PATH_NOT_ONE_SEGMENT, name)
        );
    }
}

#[test]
fn the_materialization_refusals_come_through_unchanged() {
    let mut b = base();
    let e = b.run_with(&b.schema, None).unwrap_err();
    assert!(matches!(e, ReceiptError::Materialize(ref m) if m.reason == MATERIALIZE_NO_DOCUMENT));
    b.caps.remove("mtu");
    match b.run().unwrap_err() {
        ReceiptError::Materialize(m) => {
            assert_eq!(
                (m.reason, m.field.as_str()),
                (MATERIALIZE_CAPABILITY_MISSING, "mtu")
            );
        }
        other => panic!("{other:?}"),
    }
}

#[test]
fn the_order_of_the_checks_names_the_first_reason() {
    // the schema identity is checked before the release fields, the release fields before the engine fields, those before the document and the capabilities
    let mut b = base();
    b.release.primitive_release = String::new();
    b.engine.identity = String::new();
    b.caps.clear();
    let bad_ns = b
        .schema
        .replace("namespace: \"cic:demo\"", "namespace: \"\"");
    assert!(matches!(
        b.run_with(&bad_ns, None).unwrap_err(),
        ReceiptError::Identity(_)
    ));
    assert_eq!(
        refusal(b.run_with(&b.schema, None).unwrap_err()).1,
        "primitive_release"
    );
    b.release.primitive_release = "x".to_string();
    assert_eq!(
        refusal(b.run_with(&b.schema, None).unwrap_err()).1,
        "engine_identity"
    );
    b.engine.identity = "x".to_string();
    assert!(matches!(
        b.run_with(&b.schema, None).unwrap_err(),
        ReceiptError::Materialize(m) if m.reason == MATERIALIZE_NO_DOCUMENT
    ));
}

#[test]
fn a_digest_has_only_lowercase_hex_characters_at_every_position() {
    // `all` taken for `any` accepts a digest with one good character: the wrong one is put at the first, a middle and the last position
    let mut b = base();
    for c in ['g', 'A', '/', 'G', ':'] {
        for at in [0usize, 31, 63] {
            let mut hex = "a".repeat(64);
            hex.replace_range(at..=at, &c.to_string());
            b.release.primitive_build_hash = format!("sha256:{hex}");
            let (reason, field) = refusal(b.run().unwrap_err());
            assert_eq!(
                (reason, field.as_str()),
                (RECEIPT_RELEASE_FIELD, "primitive_build_hash"),
                "{c} at {at}"
            );
        }
    }
}

#[test]
fn the_canonical_errors_of_the_receipt_are_refusals_and_the_unreachable_ones_are_shown_so() {
    // The canonical bytes can fail on a float, an integer beyond 64 bits or a repeated key.
    //  * The authored input: a node built by a caller can repeat a key and the materialization does not see it, so the receipt must refuse it itself (a `Canonical`
    //    error, never a panic). A number the model does not carry is refused earlier, by the materialization (`unsupported`).
    //  * A default value and the receipt body cannot fail: the defaults come from a compiled schema (text, whose compiler refuses a float or a large integer, below),
    //    the flat fields are scalars, and the body is made of texts and the integer 1.
    let b = base();
    let entry = |k: &str, v: Value| Entry {
        key: k.to_string(),
        key_pos: None,
        value: Node::new(v),
    };
    let run = |doc: Node| {
        let sha = sha256_hex(b.schema.as_bytes());
        receipt_flat_fixture(
            &input(&b.schema, &sha, &b.grammar),
            Some(&doc),
            &b.a,
            &b.caps,
            &b.release,
            &b.engine,
        )
    };
    let number = vec![
        entry("type", Value::Str("t".into())),
        entry("mtu", Value::Unsupported("1.5".into())),
    ];
    match run(Node::new(Value::Map(number))) {
        Err(ReceiptError::Materialize(m)) => assert_eq!(m.reason, MATERIALIZE_UNSUPPORTED),
        other => panic!("{other:?}"),
    }
    let repeated = vec![
        entry("type", Value::Str("t".into())),
        entry("mtu", Value::Int(1)),
        entry("mtu", Value::Int(2)),
    ];
    match run(Node::new(Value::Map(repeated))) {
        Err(ReceiptError::Canonical(e)) => assert_eq!(e.reason, "duplicate_key"),
        other => panic!("{other:?}"),
    }
    // text never gets that far: the parser refuses the spellings, and so does the schema compiler for a default
    for y in [
        "type: t\nmtu: 99999999999999999999\n",
        "type: t\nmtu: 1.5\n",
        "type: t\nmtu: 1\nmtu: 2\n",
    ] {
        assert!(parse(y).is_err(), "{y:?}");
    }
    for d in ["99999999999999999999", "1.5"] {
        let schema = b.schema.replace("default: 1500", &format!("default: {d}"));
        assert!(
            matches!(
                b.run_with(&schema, Some(&b.yaml)).unwrap_err(),
                ReceiptError::Identity(_)
            ),
            "a default of {d}"
        );
    }
}
