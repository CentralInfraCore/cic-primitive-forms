//! The engine's D1.3 schema identity contract (`cic-primitive-engine` PR #64, `conformance/differential/schema_identity`) run through this library's own
//! `bind_schema_identity`, and the binding that the engine's review made mandatory: the name, the version and the digest of a receipt come from one verified
//! source. The Go peer is `go/schemaidentity_contract_test.go`.
//!
//! The vectors are the engine's, as a snapshot in `testdata/p5/schema_identity` so that every job that runs the tests has them. The `rust` job of the CI
//! compares the snapshot with the engine's own directory at the pinned commit (`diff -r`), so the snapshot cannot drift from the engine.

use cic_primitive_forms::access::sha256_hex;
use cic_primitive_forms::{
    bind_schema_identity, schema_digest, schema_identity, schema_name, IdentityFailure,
    SchemaInput, SCHEMA_INVALID_IDENTITY, SCHEMA_SOURCE_BINDING,
};
use serde_json::Value as J;
use std::path::PathBuf;

/// The two golden digests, written here a second time from the engine's issue and recomputed with `sha256sum`: the vector files must hold exactly these bytes.
const GOLDEN_314: &str = "sha256:a36e841db3d5f108a7d5e400f80a636d095bc5c9ddc670142b938d73d0d1a338";
const GOLDEN_313: &str = "sha256:4fff76af7c6313eae102fc1f967b9d607db0f26c51f6f5f8eb11d7c0988b07f7";

fn forms_root() -> PathBuf {
    std::env::var_os("CIC_FORMS_ROOT").map_or_else(
        || PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."),
        PathBuf::from,
    )
}

fn corpus() -> PathBuf {
    let dir = forms_root().join("testdata/p5/schema_identity");
    assert!(dir.is_dir(), "{} is missing", dir.display());
    dir
}

fn opt(v: &J, key: &str) -> Option<String> {
    v[key].as_str().map(str::to_string)
}

#[test]
fn the_engine_vectors() {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(corpus())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.is_dir())
        .collect();
    dirs.sort();
    let (mut accepted, mut rejected) = (0, 0);
    for dir in dirs {
        let name = dir.file_name().unwrap().to_string_lossy().to_string();
        let input: J =
            serde_json::from_slice(&std::fs::read(dir.join("input.json")).unwrap()).unwrap();
        let want: J =
            serde_json::from_slice(&std::fs::read(dir.join("expected.json")).unwrap()).unwrap();
        let source = std::fs::read(dir.join(input["source"].as_str().unwrap())).unwrap();
        let got = bind_schema_identity(
            &source,
            opt(&input, "claimed_digest").as_deref(),
            opt(&input, "namespace").as_deref(),
            opt(&input, "kind").as_deref(),
        );
        if want["accepted"].as_bool().unwrap() {
            accepted += 1;
            let (n, d) = got.unwrap_or_else(|e| panic!("{name}: want accepted, got {e}"));
            assert_eq!(n, want["schema_name"].as_str().unwrap(), "{name}");
            assert_eq!(d, want["schema_digest"].as_str().unwrap(), "{name}");
        } else {
            rejected += 1;
            let e = got.expect_err(&name);
            let w = &want["error"];
            assert_eq!(
                (e.code, e.rule, e.path),
                (
                    w["code"].as_str().unwrap(),
                    w["rule"].as_str().unwrap(),
                    w["path"].as_str().unwrap()
                ),
                "{name}"
            );
        }
    }
    assert!(
        accepted >= 5 && rejected >= 18,
        "corpus too thin: {accepted} accepted, {rejected} rejected"
    );
}

#[test]
fn the_golden_files_hold_the_bytes_the_issue_states() {
    let with = std::fs::read(corpus().join("golden_with_final_lf/schema.yaml")).unwrap();
    let without = std::fs::read(corpus().join("golden_without_final_lf/schema.yaml")).unwrap();
    assert_eq!(
        (with.len(), schema_digest(&with)),
        (314, GOLDEN_314.to_string())
    );
    assert_eq!(
        (without.len(), schema_digest(&without)),
        (313, GOLDEN_313.to_string())
    );
}

/// The test can fail: every one of the 314 bytes of the golden source matters to the digest and to the binding.
#[test]
fn every_byte_of_the_source_is_bound() {
    let source = std::fs::read(corpus().join("golden_with_final_lf/schema.yaml")).unwrap();
    for i in 0..source.len() {
        let mut altered = source.clone();
        altered[i] ^= 1;
        let e = bind_schema_identity(&altered, Some(GOLDEN_314), Some("cic:storage"), Some("K"))
            .expect_err("an altered byte");
        assert_eq!(
            (e.code, e.rule),
            (SCHEMA_SOURCE_BINDING, "R-SCHEMA-DIGEST-MISMATCH"),
            "byte {i}"
        );
    }
}

/// A Forms schema (`testdata/p5/materialize.json`), as a `SchemaInput` over its exact bytes.
fn flat_schema() -> (String, String) {
    let file: J = serde_json::from_slice(
        &std::fs::read(forms_root().join("testdata/p5/materialize.json")).unwrap(),
    )
    .unwrap();
    (
        file["grammar"].as_str().unwrap().to_string(),
        file["schemas"]["flat"].as_str().unwrap().to_string(),
    )
}

fn input<'a>(text: &'a str, sha: &'a str, grammar: &'a str) -> SchemaInput<'a> {
    SchemaInput {
        bytes: text.as_bytes(),
        sha256: sha,
        grammar,
        surface: "config_surface",
    }
}

#[test]
fn the_identity_of_one_verified_source() {
    let (grammar, text) = flat_schema();
    let sha = sha256_hex(text.as_bytes());
    let id = schema_identity(&input(&text, &sha, &grammar)).expect("identity");
    assert_eq!(id.schema_name, "cic:demo:Demo");
    assert_eq!(id.schema_version, "v1.0.0");
    assert_eq!(id.schema_digest, format!("sha256:{sha}"));
    assert_eq!(id.schema_digest, schema_digest(text.as_bytes()));
}

/// The binding the engine's review asked for. The components cannot come from another file: bytes that are not the pinned ones are refused before any
/// identity is read, whichever file the digest belongs to.
#[test]
fn the_components_cannot_come_from_another_file() {
    let (grammar, text) = flat_schema();
    let other = text.replace("kind: Demo", "kind: Other");
    assert_ne!(other, text);
    let sha_of_original = sha256_hex(text.as_bytes());
    let sha_of_other = sha256_hex(other.as_bytes());
    // the bytes of one file under the digest of the other, both ways
    for (bytes, sha) in [(&other, &sha_of_original), (&text, &sha_of_other)] {
        match schema_identity(&input(bytes, sha, &grammar)) {
            Err(IdentityFailure::Schema(e)) => {
                assert!(format!("{e:?}").contains("SCHEMA_DIGEST_MISMATCH"), "{e:?}");
            }
            other => panic!("expected a binding refusal, got {other:?}"),
        }
    }
    // and each file under its own digest names its own kind
    let a = schema_identity(&input(&text, &sha_of_original, &grammar)).unwrap();
    let b = schema_identity(&input(&other, &sha_of_other, &grammar)).unwrap();
    assert_eq!(a.schema_name, "cic:demo:Demo");
    assert_eq!(b.schema_name, "cic:demo:Other");
    assert_ne!(a.schema_digest, b.schema_digest);
}

/// A schema whose own identity breaks the contract is refused with the engine's rule, though its bytes are the pinned ones.
#[test]
fn a_schema_with_an_invalid_identity_is_refused() {
    let (grammar, text) = flat_schema();
    for (from, to, rule) in [
        ("kind: Demo", "kind: \"A:B\"", "R-SCHEMA-NAME-KIND"),
        ("kind: Demo", "kind: \"\"", "R-SCHEMA-NAME-KIND"),
        (
            "namespace: \"cic:demo\"",
            "namespace: \"cic::demo\"",
            "R-SCHEMA-NAME-NAMESPACE",
        ),
        (
            "namespace: \"cic:demo\"",
            "namespace: \"\"",
            "R-SCHEMA-NAME-NAMESPACE",
        ),
        (
            "namespace: \"cic:demo\"",
            "namespace: \":cic\"",
            "R-SCHEMA-NAME-NAMESPACE",
        ),
    ] {
        let bad = text.replace(from, to);
        assert_ne!(bad, text, "{to}");
        let sha = sha256_hex(bad.as_bytes());
        match schema_identity(&input(&bad, &sha, &grammar)) {
            Err(IdentityFailure::Identity(e)) => {
                assert_eq!((e.code, e.rule), (SCHEMA_INVALID_IDENTITY, rule), "{to}");
            }
            other => panic!("{to}: expected an identity refusal, got {other:?}"),
        }
    }
}

#[test]
fn the_name_is_used_as_authored() {
    assert_eq!(
        schema_name(Some(" Cic:Storage"), Some("kind ")).unwrap(),
        " Cic:Storage:kind "
    );
}
