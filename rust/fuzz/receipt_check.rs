//! The property of the receipt fuzz target (P5-3, `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 19), in one place for the two callers: the `receipt` fuzz target
//! (`fuzz_targets/receipt.rs`) and the test that replays the seeds (`tests/receipt_fuzz_seeds.rs`), which also hands the receipts to the pinned engine's verifier. The Go
//! peer is `FuzzReceipt` and `receiptFuzzCheck` in `go/receipt_fuzz_test.go`; the inputs (schema, fixture data, seeds) are derived by `tools/p5/receipt_fuzz_seeds.py`.
//!
//! An input is the FLAGS byte, the KIND byte and the document (YAML text); the flags and the kind are described in that script. The properties:
//!  * the parser is not the receipt's: text that is not UTF-8 or does not parse is left;
//!  * nothing panics, and the same input gives the same answer twice (the document, the digest, the authored input, the binding, or the same refusal);
//!  * the error boundary is the order of the checks: a broken schema binding is an `Identity` error whatever the rest is; then the empty version, the release fields, the
//!    engine fields (`Refused`, with the field named); only then the document: a refusal of the materialization is a `Materialize` error with a reason the faults of the
//!    document (and, with the capability fault, of the capability map) can cause, and a repeated key a caller built is a `Canonical` error (never a panic);
//!  * a receipt is made only when no fault was made, and then it is exactly what the contract says: one line `{"materialization":{...}}` whose digest is that of its bytes,
//!    with the schema digest of the bytes, the digest of the canonical authored input, the output digest of the materialization, the applied defaults in the order of the
//!    paths with the value digests of the declared defaults, a binding of every path to its field, the fields in byte order with `authored` exactly where the document
//!    writes the field; and the document written as YAML and read again gives the same receipt.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    canonical_bytes, canonical_digest, emit, parse, receipt_flat_fixture, EngineIdentity, Error,
    Node, ReceiptError, ReleaseFixture, SchemaInput, Value, MATERIALIZE_CAPABILITY_MISSING,
    MATERIALIZE_CAPABILITY_UNDECLARED, MATERIALIZE_CAPABILITY_UNKNOWN,
    MATERIALIZE_INVALID_DOCUMENT, MATERIALIZE_OMITTED_OPTIONAL, MATERIALIZE_UNSUPPORTED,
    RECEIPT_ENGINE_FIELD, RECEIPT_PATH_NOT_ONE_SEGMENT, RECEIPT_RELEASE_FIELD,
    RECEIPT_SCHEMA_VERSION_EMPTY,
};
use std::collections::BTreeMap;

/// The directory `testdata/p5/receipt-fuzz` is read from at run time, not at compile time: the coverage and the mutation tools build the crate somewhere else, where
/// a path relative to this file does not lead to the repository. `CIC_FORMS_ROOT` as in the other tests; without it, the nearest directory above the manifest that has it
/// (the manifest is `rust/` for the replay test and `rust/fuzz/` for the fuzz target).
fn data(file: &str) -> &'static str {
    use std::sync::OnceLock;
    static ROOT: OnceLock<std::path::PathBuf> = OnceLock::new();
    static TEXTS: OnceLock<std::sync::Mutex<BTreeMap<String, &'static str>>> = OnceLock::new();
    let root = ROOT.get_or_init(|| {
        if let Some(r) = std::env::var_os("CIC_FORMS_ROOT") {
            return std::path::PathBuf::from(r);
        }
        let mut dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            if dir.join("testdata/p5/receipt-fuzz").is_dir() {
                return dir;
            }
            assert!(dir.pop(), "no testdata/p5/receipt-fuzz above the manifest");
        }
    });
    let mut texts = TEXTS
        .get_or_init(|| std::sync::Mutex::new(BTreeMap::new()))
        .lock()
        .expect("the lock");
    texts.entry(file.to_string()).or_insert_with(|| {
        let text = std::fs::read_to_string(root.join("testdata/p5/receipt-fuzz").join(file))
            .unwrap_or_else(|e| panic!("testdata/p5/receipt-fuzz/{file}: {e}"));
        Box::leak(text.into_boxed_str())
    })
}

pub fn schema() -> &'static str {
    data("schema.yaml")
}

fn fixture_text() -> &'static str {
    data("fixture.txt")
}
pub const IDENTITY: &str = "cic-primitive-forms-rust";

const BIND: u8 = 1;
const NO_VERSION: u8 = 2;
const BAD_HASH: u8 = 4;
const NO_ENGINE_VERSION: u8 = 8;
const CAPS: u8 = 16;
const DUPLICATE: u8 = 32;
const DOTTED: u8 = 64;

/// What a receipt that was made is, for the callers that go on with it (the replay hands it to the engine).
pub struct Made {
    pub document: String,
    pub authored_input: String,
    pub binding: BTreeMap<String, String>,
    pub expected_keys: Vec<String>,
    pub projection: String,
    pub defaults: usize,
}

/// How the call ended, for the callers that count it.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Ended {
    Left,
    Identity,
    Version,
    Release,
    Engine,
    Document,
    Capability,
    Repeated,
    Segment,
    Receipt,
}

fn fixture(key: &str) -> &'static str {
    fixture_text()
        .lines()
        .find_map(|l| l.strip_prefix(key).and_then(|r| r.strip_prefix('\t')))
        .unwrap_or_else(|| panic!("fixture.txt has no {key}"))
}

/// The declared top-level field names, in byte order (the keys of the capability map).
pub fn declared() -> Vec<String> {
    let mut names: Vec<String> = fixture_text()
        .lines()
        .filter_map(|l| l.strip_prefix("cap:").and_then(|r| r.split('\t').next()))
        .map(str::to_string)
        .collect();
    names.sort();
    names
}

fn capabilities(flags: u8, kind: u8) -> BTreeMap<String, String> {
    let mut caps: BTreeMap<String, String> = fixture_text()
        .lines()
        .filter_map(|l| l.strip_prefix("cap:"))
        .filter_map(|l| l.split_once('\t'))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
    if flags & CAPS != 0 {
        match (kind / 3) % 3 {
            0 => {
                caps.remove("mtu");
            }
            1 => {
                caps.insert("label".into(), "Implemented".into());
            }
            _ => {
                caps.insert("zzz".into(), "implemented".into());
            }
        }
    }
    if flags & DOTTED != 0 {
        if let Some(v) = caps.remove("label") {
            caps.insert("la.bel".into(), v);
        }
    }
    caps
}

fn release(flags: u8, kind: u8) -> ReleaseFixture {
    let mut r = ReleaseFixture {
        primitive_release: fixture("primitive_release").into(),
        primitive_source_commit: fixture("primitive_source_commit").into(),
        primitive_build_hash: fixture("primitive_build_hash").into(),
        grammar_sha256: fixture("grammar_sha256").into(),
        grammar_schema_sha256: fixture("grammar_schema_sha256").into(),
    };
    if flags & BAD_HASH != 0 {
        let good = r.primitive_build_hash.clone();
        r.primitive_build_hash = match (kind / 9) % 3 {
            0 => "sha256:ABC".into(),
            // a digest with ONE wrong character, at the end and at the start: a check that stops early or starts late is seen
            1 => format!("{}g", &good[..good.len() - 1]),
            _ => format!("sha256:G{}", &good["sha256:".len() + 1..]),
        };
    }
    r
}

type Outcome = Result<cic_primitive_forms::FlatReceipt, ReceiptError>;

fn make(flags: u8, kind: u8, doc: &Node) -> Outcome {
    let mut schema = schema().to_string();
    if flags & NO_VERSION != 0 {
        schema = schema.replace("    version: v1.0.0\n", "");
        assert!(!schema.contains("v1.0.0"), "the version line was not found");
    }
    if flags & DOTTED != 0 {
        let renamed = schema.replace("      - name: label\n", "      - name: \"la.bel\"\n");
        assert_ne!(renamed, schema, "the field label was not found");
        schema = renamed;
    }
    let sha = sha256_hex(schema.as_bytes());
    let (bytes, claim) = if flags & BIND != 0 {
        match kind % 3 {
            0 => {
                let mut c = sha.clone();
                let last = c.pop().expect("a digest");
                c.push(if last == '0' { '1' } else { '0' });
                (schema.clone(), c)
            }
            1 => (format!("{schema}# after the claim\n"), sha),
            _ => (schema.clone(), String::new()),
        }
    } else {
        (schema.clone(), sha)
    };
    let grammar = fixture("grammar");
    let a: Vec<String> = fixture("access_keys")
        .split(',')
        .map(str::to_string)
        .collect();
    let engine = EngineIdentity {
        identity: IDENTITY.to_string(),
        version: if flags & NO_ENGINE_VERSION != 0 {
            String::new()
        } else {
            fixture("engine_version").to_string()
        },
    };
    receipt_flat_fixture(
        &SchemaInput {
            bytes: bytes.as_bytes(),
            sha256: &claim,
            grammar,
            surface: "config_surface",
        },
        Some(doc),
        &a,
        &capabilities(flags, kind),
        &release(flags, kind),
        &engine,
    )
}

fn same(a: &Outcome, b: &Outcome) -> bool {
    match (a, b) {
        (Ok(x), Ok(y)) => {
            x.document == y.document
                && x.digest == y.digest
                && x.binding == y.binding
                && x.authored_input == y.authored_input
        }
        (Err(x), Err(y)) => format!("{x:?}") == format!("{y:?}"),
        _ => false,
    }
}

/// Why an input is left to the parser, as a stable token for the trace of the replay (the comparison with the Go round asks for the Rust reason of the one class of parsers'
/// difference it lets through): `short`, `not_utf8`, `syntax: ...`, `unsupported: ...`, `limit: ...`, `contract: CODE`, or `parsed` for an input that is not left.
pub fn left_reason(data: &[u8]) -> String {
    let Some(rest) = data.get(2..) else {
        return "short".into();
    };
    let Ok(text) = std::str::from_utf8(rest) else {
        return "not_utf8".into();
    };
    match parse(text) {
        Ok(_) => "parsed".into(),
        Err(Error::Syntax(m)) => format!("syntax: {m}"),
        Err(Error::Unsupported(m)) => format!("unsupported: {m}"),
        Err(Error::Limit(m)) => format!("limit: {m}"),
        Err(Error::Contract(v)) => format!("contract: {}", v.first().map_or("", |x| x.code)),
        Err(other) => format!("other: {other:?}"),
    }
}

/// Runs the property on one input; panics (with the input in the message) when it does not hold.
pub fn run(data: &[u8]) -> (Ended, Option<Made>) {
    let (Some((&flags, rest)), true) = (data.split_first(), data.len() >= 2) else {
        return (Ended::Left, None);
    };
    let (kind, rest) = (rest[0], &rest[1..]);
    let Ok(text) = std::str::from_utf8(rest) else {
        return (Ended::Left, None);
    };
    let Ok(mut doc) = parse(text) else {
        return (Ended::Left, None);
    };
    let mut repeated = false;
    if flags & DUPLICATE != 0 {
        if let Value::Map(entries) = &mut doc.value {
            if let Some(first) = entries.first().cloned() {
                entries.push(first);
                repeated = true;
            }
        }
    }
    let before = doc.clone();
    let first = make(flags, kind, &doc);
    assert!(doc.same(&before), "the receipt changed its input {data:?}");
    assert!(
        same(&first, &make(flags, kind, &doc)),
        "the same input gave another answer {data:?}"
    );
    let caps_broken = flags & CAPS != 0;
    let doc_reasons = [
        MATERIALIZE_INVALID_DOCUMENT,
        MATERIALIZE_UNSUPPORTED,
        MATERIALIZE_OMITTED_OPTIONAL,
    ];
    let cap_reasons = [
        MATERIALIZE_CAPABILITY_MISSING,
        MATERIALIZE_CAPABILITY_UNKNOWN,
        MATERIALIZE_CAPABILITY_UNDECLARED,
    ];
    let expected_layer = if flags & BIND != 0 {
        Ended::Identity
    } else if flags & NO_VERSION != 0 {
        Ended::Version
    } else if flags & BAD_HASH != 0 {
        Ended::Release
    } else if flags & NO_ENGINE_VERSION != 0 {
        Ended::Engine
    } else {
        Ended::Document
    };
    let receipt = match first {
        Err(ReceiptError::Identity(_)) => {
            assert_eq!(expected_layer, Ended::Identity, "{data:?}");
            return (Ended::Identity, None);
        }
        Err(ReceiptError::Refused { reason, field }) => {
            let got = match (expected_layer, reason, field.as_str()) {
                (Ended::Version, RECEIPT_SCHEMA_VERSION_EMPTY, "schema_version") => Ended::Version,
                (Ended::Release, RECEIPT_RELEASE_FIELD, "primitive_build_hash") => Ended::Release,
                (Ended::Engine, RECEIPT_ENGINE_FIELD, "engine_version") => Ended::Engine,
                (Ended::Document, RECEIPT_PATH_NOT_ONE_SEGMENT, "la.bel")
                    if flags & DOTTED != 0 =>
                {
                    Ended::Segment
                }
                _ => panic!("a refusal the faults made cannot cause: {reason} {field} {data:?}"),
            };
            return (got, None);
        }
        Err(ReceiptError::Materialize(m)) => {
            assert_eq!(expected_layer, Ended::Document, "{data:?}");
            assert!(
                doc_reasons.contains(&m.reason) || (caps_broken && cap_reasons.contains(&m.reason)),
                "a materialization refusal the faults cannot cause: {} {data:?}",
                m.reason
            );
            let caps = cap_reasons.contains(&m.reason);
            return (
                if caps {
                    Ended::Capability
                } else {
                    Ended::Document
                },
                None,
            );
        }
        Err(ReceiptError::Canonical(e)) => {
            assert_eq!(expected_layer, Ended::Document, "{data:?}");
            assert!(
                repeated,
                "a canonical error without a repeated key {data:?}"
            );
            assert_eq!(e.reason, "duplicate_key", "{data:?}");
            return (Ended::Repeated, None);
        }
        Ok(r) => r,
    };
    assert_eq!(
        expected_layer,
        Ended::Document,
        "a receipt although a check should have refused {data:?}"
    );
    assert!(
        flags & (CAPS | DOTTED) == 0 && (flags & DUPLICATE == 0 || !repeated),
        "a receipt although the capability map, the schema or the document was broken {data:?}"
    );

    // the receipt is exactly what the contract says
    let doc_text = &receipt.document;
    assert!(
        doc_text.starts_with("{\"materialization\":{") && doc_text.ends_with("}}"),
        "{data:?}"
    );
    assert!(!doc_text.contains(['\n', '\r', '\t']), "{data:?}");
    assert_eq!(
        receipt.digest,
        canonical_digest(doc_text.as_bytes()),
        "the digest is not that of the document {data:?}"
    );
    let authored = String::from_utf8(canonical_bytes(&doc).expect("canonical")).expect("utf-8");
    assert_eq!(receipt.authored_input, authored, "{data:?}");
    let has = |s: String| {
        assert!(
            doc_text.contains(&s),
            "{s} is missing from {doc_text} {data:?}"
        )
    };
    has(format!(
        "\"input_digest\":\"{}\"",
        canonical_digest(authored.as_bytes())
    ));
    has(format!(
        "\"output_digest\":\"{}\"",
        receipt.materialized.output_digest
    ));
    has(format!(
        "\"schema_digest\":\"sha256:{}\"",
        sha256_hex(schema().as_bytes())
    ));
    has(format!("\"engine_identity\":\"{IDENTITY}\""));
    has("\"receipt_schema_version\":1".to_string());
    // the values of the identity: the name is `namespace:kind` and the version is the one the schema writes, the engine version is the fixture's
    let line = |key: &str| {
        schema()
            .lines()
            .find_map(|l| l.strip_prefix(&format!("    {key}: ")))
            .map(|v| v.trim_matches('"').to_string())
            .unwrap_or_else(|| panic!("the schema has no {key}"))
    };
    has(format!(
        "\"schema_name\":\"{}:{}\"",
        line("namespace"),
        line("kind")
    ));
    has(format!("\"schema_version\":\"{}\"", line("version")));
    has(format!(
        "\"engine_version\":\"{}\"",
        fixture("engine_version")
    ));
    for key in [
        "primitive_release",
        "primitive_source_commit",
        "primitive_build_hash",
        "grammar_sha256",
        "grammar_schema_sha256",
    ] {
        has(format!("\"{key}\":\"{}\"", fixture(key)));
    }
    has("\"derived_values\":[]".to_string());
    for name in [
        "grammar_sha256",
        "grammar_schema_sha256",
        "primitive_release",
        "primitive_source_commit",
        "primitive_build_hash",
        "schema_name",
        "schema_version",
        "engine_version",
        "applied_defaults",
    ] {
        assert_eq!(
            doc_text.matches(&format!("\"{name}\":")).count(),
            1,
            "{name} {data:?}"
        );
    }
    let names = declared();
    let keys: Vec<&str> = receipt
        .materialized
        .fields
        .iter()
        .map(|f| f.key.as_str())
        .collect();
    assert_eq!(
        keys, names,
        "the fields are the declared names in byte order {data:?}"
    );
    assert_eq!(receipt.materialized.expected_keys, names, "{data:?}");
    let written: BTreeMap<&str, &Node> = match &doc.value {
        Value::Map(entries) => entries.iter().map(|e| (e.key.as_str(), &e.value)).collect(),
        _ => BTreeMap::new(),
    };
    let mut entries = Vec::new();
    let mut binding = BTreeMap::new();
    for f in &receipt.materialized.fields {
        match written.get(f.key.as_str()) {
            Some(v) => {
                assert_eq!(f.provenance, "authored", "{} {data:?}", f.key);
                assert!(
                    f.value.same(v),
                    "an authored value was changed {} {data:?}",
                    f.key
                );
            }
            None => {
                assert_eq!(f.provenance, "schema_default", "{} {data:?}", f.key);
                let digest = canonical_digest(&canonical_bytes(&f.value).expect("canonical"));
                entries.push(format!(
                    "{{\"path\":\"$.{}\",\"rule\":\"schema-default\",\"value_digest\":\"{digest}\"}}",
                    f.key
                ));
                binding.insert(format!("$.{}", f.key), f.key.clone());
            }
        }
    }
    has(format!("\"applied_defaults\":[{}]", entries.join(",")));
    assert_eq!(receipt.binding, binding, "the binding {data:?}");
    // written as YAML and read again, the same receipt
    let again = parse(&emit(&doc)).expect("an emitted document reads again");
    let second = make(flags, kind, &again).expect("accepted twice");
    assert_eq!(
        second.document, receipt.document,
        "emit and parse changed the receipt {data:?}"
    );
    let made = Made {
        defaults: entries.len(),
        document: receipt.document,
        authored_input: receipt.authored_input,
        binding: receipt.binding,
        expected_keys: receipt.materialized.expected_keys,
        projection: receipt.materialized.projection_json,
    };
    (Ended::Receipt, Some(made))
}
