//! P5-3 end-to-end proof WITHOUT REFERENCE RESOLUTION (`docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 21).
//!
//! One execution chain per case: the schema source bytes -> the verified compile and the identity; the authored document -> parse -> the standalone materialization ->
//! `receipt_flat_fixture`. The receipt builder materializes again inside, so TWO materializations happen; the chain compares them field by field and states both.
//! Every value that leaves the chain (the dump for `tools/parity/check_receipt_e2e.py` and the engine harnesses) comes from the same run.
//!
//! The capability map is an explicit caller input (the P5-2 contract): where it comes from is not claimed to be authenticated. The expected values of the cases are
//! computed in Python (`tools/p5/end_to_end_cases.py`). With `CIC_E2E_DUMP` set, the test writes the chain of every case to that file. The Go peer is
//! `go/receipt_end_to_end_test.go`.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    canonical_bytes, canonical_digest, compile_schema, materialize_flat, parse,
    receipt_flat_fixture, schema_identity, EngineIdentity, FlatReceipt, IdentityFailure,
    MaterializeError, Materialized, ReceiptError, ReleaseFixture, SchemaIdentity, SchemaInput,
    MATERIALIZE_CAPABILITY_MISSING, MATERIALIZE_CAPABILITY_UNDECLARED,
    MATERIALIZE_CAPABILITY_UNKNOWN, MATERIALIZE_NOT_FLAT, SCHEMA_SOURCE_BINDING,
};
use serde_json::{json, Value as J};
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

fn caps_of(v: &J) -> BTreeMap<String, String> {
    v.as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string()))
        .collect()
}

struct Fixture {
    file: J,
    a: Vec<String>,
    grammar: String,
    release: ReleaseFixture,
    engine: EngineIdentity,
}

fn fixture() -> Fixture {
    let file = load("end-to-end.json");
    let s = |k: &str| file["release"][k].as_str().unwrap().to_string();
    Fixture {
        a: file["access_keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect(),
        grammar: file["grammar"].as_str().unwrap().to_string(),
        release: ReleaseFixture {
            primitive_release: s("primitive_release"),
            primitive_source_commit: s("primitive_source_commit"),
            primitive_build_hash: s("primitive_build_hash"),
            grammar_sha256: s("grammar_sha256"),
            grammar_schema_sha256: s("grammar_schema_sha256"),
        },
        engine: EngineIdentity {
            identity: IDENTITY.to_string(),
            version: file["engine_version"].as_str().unwrap().to_string(),
        },
        file,
    }
}

/// What every step of one execution produced.
#[derive(Debug)]
struct Chain {
    identity: SchemaIdentity,
    authored: String,
    standalone: Materialized,
    receipt: FlatReceipt,
}

/// Where a chain stopped, and why.
#[derive(Debug)]
#[allow(dead_code)] // the payloads are for the Debug text of a failed assertion
enum Stop {
    Identity(IdentityFailure),
    Parse,
    Materialize(MaterializeError),
    Receipt(ReceiptError),
}

fn same_object(a: &Materialized, b: &Materialized) -> bool {
    a.expected_keys == b.expected_keys
        && a.projection_json == b.projection_json
        && a.output_digest == b.output_digest
        && a.fields.len() == b.fields.len()
        && a.fields.iter().zip(&b.fields).all(|(x, y)| {
            x.key == y.key
                && x.capability == y.capability
                && x.provenance == y.provenance
                && canonical_bytes(&x.value).unwrap() == canonical_bytes(&y.value).unwrap()
        })
}

/// The chain of ONE execution. The schema is compiled and its identity bound once for the step; the document is parsed once and that very node is what the
/// standalone materialization and the receipt builder are given. Nothing is taken from a file but the inputs.
fn run(
    fx: &Fixture,
    schema: &str,
    sha: &str,
    yaml: &str,
    caps: &BTreeMap<String, String>,
) -> Result<Chain, Stop> {
    let input = SchemaInput {
        bytes: schema.as_bytes(),
        sha256: sha,
        grammar: &fx.grammar,
        surface: "config_surface",
    };
    let identity = schema_identity(&input).map_err(Stop::Identity)?;
    let compiled =
        compile_schema(&input).map_err(|e| Stop::Identity(IdentityFailure::Schema(e)))?;
    let doc = parse(yaml).map_err(|_| Stop::Parse)?;
    let authored = String::from_utf8(canonical_bytes(&doc).unwrap()).unwrap();
    let standalone =
        materialize_flat(Some(&doc), &compiled.root, &fx.a, caps).map_err(Stop::Materialize)?;
    let receipt = receipt_flat_fixture(&input, Some(&doc), &fx.a, caps, &fx.release, &fx.engine)
        .map_err(Stop::Receipt)?;
    Ok(Chain {
        identity,
        authored,
        standalone,
        receipt,
    })
}

/// The links of the chain: each step's output is the next step's input, and the receipt carries exactly what the earlier steps produced.
fn link(id: &str, c: &Chain) -> J {
    // the two materializations are one and the same object
    assert!(
        same_object(&c.standalone, &c.receipt.materialized),
        "{id}: the standalone materialization and the one inside the receipt builder differ"
    );
    assert_eq!(
        c.receipt.authored_input, c.authored,
        "{id}: the authored input"
    );
    let body = &serde_json::from_str::<J>(&c.receipt.document).unwrap()["materialization"];
    assert_eq!(body["schema_name"], c.identity.schema_name, "{id}");
    assert_eq!(body["schema_version"], c.identity.schema_version, "{id}");
    assert_eq!(body["schema_digest"], c.identity.schema_digest, "{id}");
    assert_eq!(
        body["input_digest"],
        canonical_digest(c.authored.as_bytes()),
        "{id}: the input digest is that of the authored input of the chain"
    );
    assert_eq!(
        body["output_digest"], c.standalone.output_digest,
        "{id}: the output digest is that of the standalone materialization"
    );
    let defaulted: Vec<String> = c
        .standalone
        .fields
        .iter()
        .filter(|f| f.provenance == "schema_default")
        .map(|f| format!("$.{}", f.key))
        .collect();
    let paths: Vec<String> = body["applied_defaults"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| d["path"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(
        paths, defaulted,
        "{id}: the evidence is the defaulted fields"
    );
    assert_eq!(
        c.receipt.binding.keys().cloned().collect::<Vec<_>>(),
        defaulted,
        "{id}: the binding covers the evidence"
    );
    for (path, key) in &c.receipt.binding {
        assert_eq!(
            path,
            &format!("$.{key}"),
            "{id}: the binding is $.field -> field"
        );
    }
    body.clone()
}

/// The record of one run, as the dump and the checker see it. `chain` binds the schema, the input, the output, the receipt and the path binding (the engine's separate input) of THIS run, and its capability map.
fn record(c: &Chain, caps: &BTreeMap<String, String>) -> J {
    let r = &c.receipt;
    let chain = sha256_hex(
        format!(
            "{}\n{}\n{}\n{}\n{}\n{}",
            c.identity.schema_digest,
            canonical_digest(c.authored.as_bytes()),
            c.standalone.output_digest,
            r.digest,
            serde_json::to_string(caps).unwrap(),
            sha256_hex(serde_json::to_string(&r.binding).unwrap().as_bytes())
        )
        .as_bytes(),
    );
    json!({
        "capabilities": caps,
        "schema_name": c.identity.schema_name,
        "schema_version": c.identity.schema_version,
        "schema_digest": c.identity.schema_digest,
        "authored_input": c.authored,
        "standalone": {
            "projection": c.standalone.projection_json,
            "expected_keys": c.standalone.expected_keys,
            "output_digest": c.standalone.output_digest,
        },
        "receipt": {
            "document": r.document,
            "digest": r.digest,
            "binding": r.binding,
            "authored_input": r.authored_input,
            "projection": r.materialized.projection_json,
            "output_digest": r.materialized.output_digest,
        },
        "chain": chain,
    })
}

/// What the Python reference says about one run, set against what the chain made.
fn against_python(id: &str, which: &str, c: &Chain, want: &J, token: &str) {
    let w = |k: &str| want[k].as_str().unwrap();
    assert_eq!(
        c.standalone.projection_json,
        w("projection"),
        "{id} {which}"
    );
    assert_eq!(
        c.standalone.output_digest,
        w("output_digest"),
        "{id} {which}"
    );
    assert_eq!(c.authored, w("authored_input"), "{id} {which}");
    assert_eq!(
        c.receipt.document,
        w("document").replace(token, IDENTITY),
        "{id} {which}: the receipt"
    );
    let binding: BTreeMap<String, String> = caps_of(&want["binding"]);
    assert_eq!(c.receipt.binding, binding, "{id} {which}: the binding");
    assert_eq!(
        c.standalone.expected_keys,
        want["expected_keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|k| k.as_str().unwrap().to_string())
            .collect::<Vec<_>>(),
        "{id} {which}"
    );
}

#[test]
fn the_chain_against_the_python_reference() {
    let fx = fixture();
    let token = fx.file["engine_identity_token"].as_str().unwrap();
    let mut dump = String::new();
    let mut n = 0;
    for case in fx.file["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let (schema, sha, yaml) = (
            case["schema"].as_str().unwrap(),
            case["sha256"].as_str().unwrap(),
            case["yaml"].as_str().unwrap(),
        );
        let mut records = serde_json::Map::new();
        for which in ["base", "alt"] {
            let want = &case[which];
            let caps = caps_of(&want["capabilities"]);
            let c = run(&fx, schema, sha, yaml, &caps).unwrap_or_else(|e| panic!("{id}: {e:?}"));
            link(id, &c);
            against_python(id, which, &c, want, token);
            assert_eq!(
                c.identity.schema_digest,
                format!("sha256:{sha}"),
                "{id}: the schema digest"
            );
            records.insert(which.to_string(), record(&c, &caps));
        }
        dump += &json!({"id": id, "base": records["base"], "alt": records["alt"]}).to_string();
        dump.push('\n');
        n += 1;
    }
    assert_eq!(n, 4);
    if let Some(path) = std::env::var_os("CIC_E2E_DUMP") {
        std::fs::write(path, dump).unwrap();
    }
}

#[test]
fn a_capability_change_moves_the_object_the_digest_and_the_receipt_and_nothing_else() {
    let fx = fixture();
    for case in fx.file["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let (schema, sha, yaml) = (
            case["schema"].as_str().unwrap(),
            case["sha256"].as_str().unwrap(),
            case["yaml"].as_str().unwrap(),
        );
        let base_caps = caps_of(&case["base"]["capabilities"]);
        let alt_caps = caps_of(&case["alt"]["capabilities"]);
        assert_ne!(
            base_caps, alt_caps,
            "{id}: the alt run changes a capability"
        );
        let base = run(&fx, schema, sha, yaml, &base_caps).unwrap();
        let alt = run(&fx, schema, sha, yaml, &alt_caps).unwrap();
        // the object, its digest and the receipt move
        assert_ne!(
            base.standalone.projection_json, alt.standalone.projection_json,
            "{id}"
        );
        assert_ne!(
            base.standalone.output_digest, alt.standalone.output_digest,
            "{id}"
        );
        assert_ne!(base.receipt.document, alt.receipt.document, "{id}");
        assert_ne!(base.receipt.digest, alt.receipt.digest, "{id}");
        // the authored input did not change, so the input digest and the schema identity do not
        let b = link(id, &base);
        let a = link(id, &alt);
        assert_eq!(
            b["input_digest"], a["input_digest"],
            "{id}: the input digest"
        );
        assert_eq!(base.identity, alt.identity, "{id}: the schema identity");
        assert_eq!(
            base.receipt.binding, alt.receipt.binding,
            "{id}: the binding"
        );
        // the receipt differs in the output digest alone
        let (mut b, mut a) = (b, a);
        b.as_object_mut().unwrap().remove("output_digest");
        a.as_object_mut().unwrap().remove("output_digest");
        assert_eq!(b, a, "{id}: only the output digest of the receipt moved");
        // the old receipt does not describe the new object
        assert_ne!(
            serde_json::from_str::<J>(&base.receipt.document).unwrap()["materialization"]
                ["output_digest"],
            serde_json::Value::from(alt.standalone.output_digest.clone()),
            "{id}: the receipt of the base run names another output"
        );
    }
}

#[test]
fn a_capability_fault_is_refused_in_the_standalone_step_and_in_the_receipt_alike() {
    let fx = fixture();
    let case = &fx.file["cases"].as_array().unwrap()[2]; // many-defaults
    let (schema, sha, yaml) = (
        case["schema"].as_str().unwrap(),
        case["sha256"].as_str().unwrap(),
        case["yaml"].as_str().unwrap(),
    );
    let good = caps_of(&case["base"]["capabilities"]);
    let mut missing = good.clone();
    missing.remove("mtu");
    let mut unknown = good.clone();
    unknown.insert("mtu".to_string(), "Implemented".to_string());
    let mut unverified = good.clone();
    unverified.insert("label".to_string(), "unverified".to_string());
    let mut undeclared = good.clone();
    undeclared.insert("aaa".to_string(), "implemented".to_string());
    for (what, caps, reason, field) in [
        ("missing", missing, MATERIALIZE_CAPABILITY_MISSING, "mtu"),
        ("unknown", unknown, MATERIALIZE_CAPABILITY_UNKNOWN, "mtu"),
        (
            "unverified",
            unverified,
            MATERIALIZE_CAPABILITY_UNKNOWN,
            "label",
        ),
        (
            "undeclared",
            undeclared,
            MATERIALIZE_CAPABILITY_UNDECLARED,
            "aaa",
        ),
    ] {
        // the chain stops at the standalone materialization: no receipt step is reached
        match run(&fx, schema, sha, yaml, &caps) {
            Err(Stop::Materialize(e)) => {
                assert_eq!((e.reason, e.field.as_str()), (reason, field), "{what}")
            }
            other => panic!("{what}: not a controlled refusal of the materialization: {other:?}"),
        }
        // and the receipt builder, asked on its own, refuses it alike and makes no receipt
        let input = SchemaInput {
            bytes: schema.as_bytes(),
            sha256: sha,
            grammar: &fx.grammar,
            surface: "config_surface",
        };
        let doc = parse(yaml).unwrap();
        match receipt_flat_fixture(&input, Some(&doc), &fx.a, &caps, &fx.release, &fx.engine) {
            Err(ReceiptError::Materialize(e)) => {
                assert_eq!((e.reason, e.field.as_str()), (reason, field), "{what}")
            }
            other => panic!("{what}: the receipt builder did not refuse alike: {other:?}"),
        }
    }
}

#[test]
fn the_chain_is_bound_to_its_sources() {
    let fx = fixture();
    let case = &fx.file["cases"].as_array().unwrap()[2];
    let (schema, sha, yaml) = (
        case["schema"].as_str().unwrap(),
        case["sha256"].as_str().unwrap(),
        case["yaml"].as_str().unwrap(),
    );
    let caps = caps_of(&case["base"]["capabilities"]);
    let base = run(&fx, schema, sha, yaml, &caps).unwrap();

    // a schema source that is not the pinned bytes stops the chain at its first step, before the document is read
    let altered = schema.replace("kind: Demo", "kind: Demx");
    match run(&fx, &altered, sha, yaml, &caps) {
        Err(Stop::Identity(_)) => {}
        other => panic!("an altered schema source was not stopped by the identity step: {other:?}"),
    }
    // the same source with its own pin is another schema: another digest in the receipt
    let other_sha = sha256_hex(altered.as_bytes());
    let other = run(&fx, &altered, &other_sha, yaml, &caps).unwrap();
    assert_ne!(other.identity.schema_digest, base.identity.schema_digest);
    assert_ne!(other.receipt.document, base.receipt.document);

    // an authored document that is changed gives another input digest, another output and another receipt
    let changed = run(&fx, schema, sha, "type: u\n", &caps).unwrap();
    assert_ne!(changed.authored, base.authored);
    assert_ne!(
        changed.standalone.output_digest,
        base.standalone.output_digest
    );
    let (b, c) = (link("base", &base), link("changed", &changed));
    assert_ne!(b["input_digest"], c["input_digest"]);

    // a document the schema does not allow stops at the materialization
    match run(&fx, schema, sha, "type: t\nbogus: 1\n", &caps) {
        Err(Stop::Materialize(_)) => {}
        other => panic!("{other:?}"),
    }
    // a document that does not parse stops at the parse
    assert!(matches!(
        run(&fx, schema, sha, "a: [\n", &caps),
        Err(Stop::Parse)
    ));

    // a schema that is not flat is refused explicitly, never walked into
    let m = load("materialize.json");
    let nested = m["schemas"]["nested"].as_str().unwrap();
    let nested_caps = caps_of(&json!({"type": "implemented", "ip": "implemented"}));
    match run(
        &fx,
        nested,
        &sha256_hex(nested.as_bytes()),
        "type: t\nip:\n  gateway: g\n",
        &nested_caps,
    ) {
        Err(Stop::Materialize(e)) => assert_eq!(e.reason, MATERIALIZE_NOT_FLAT),
        other => panic!("a nested schema was not refused: {other:?}"),
    }
    assert_eq!(SCHEMA_SOURCE_BINDING, "E_SCHEMA_SOURCE_BINDING");
}
