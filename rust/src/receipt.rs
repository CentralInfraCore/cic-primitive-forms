//! The receipt of a flat materialization (P5-3; `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 17). The Go peer is `go/receipt.go`; the shared cases are
//! `testdata/p5/receipt.json`.
//!
//! The document is the engine's (`cic-primitive-engine` `docs/RECEIPT-SCHEMA.md`): `{"materialization": {15 fields}}`, as canonical bytes (the same A1-A6 form
//! as everything else here), with the digest of those bytes. The engine's `verify_receipt` is the gate that decides whether a receipt of this module is
//! consistent with its object and its input (`tools/p5/engine-harness-*`); this module does not claim it by itself.
//!
//! **One compile.** [`receipt_flat_fixture`] takes the schema as a [`SchemaInput`] and compiles it once. The name, the version and the digest come from that
//! compile ([`crate::schema_identity`]), and so does the shape that the document is materialized against, so the key set of the object is the key set of the
//! schema that the identity names. There is no parameter through which another schema's component or key set could enter.
//!
//! **Fixture only.** The three release fields and the two grammar digests are not facts of this library, and the pinned grammar is a commit of `devel` that is
//! not shown to belong to a signed release (section 15.1, decision L). The function is therefore named for what it is: it takes a [`ReleaseFixture`], checks
//! the form of its fields and nothing about their origin, and a receipt it makes proves nothing about where the release came from. There is no production
//! function yet; it needs a verified release record, which does not exist. The receipt layout has no field that could mark a receipt as fixture-made, so the
//! separation is in the function, not in the document.
//!
//! What it refuses (a [`ReceiptError`], never a guess): the errors of the schema identity and of the materialization as they are; a schema whose `identity.version`
//! is empty (the engine's verifier refuses an empty `schema_version`); a release or engine field that is empty, or a digest that is not `sha256:` and 64
//! lowercase hex; and a field name that is not exactly one plain path segment (decision O: a dot, an escape character, a bracket, a brace, `=` or `,` in a
//! name; the verifier cannot tell `a.b` as two segments from one field named `a.b`, and the library adds no escape of its own).
//!
//! `applied_defaults` lists the fields of provenance `schema_default`, sorted by path in byte order, rule `schema-default`, with the digest of the canonical
//! bytes of the value. `derived_values` is `[]`: this library derives nothing. `input_digest` is the digest of the canonical bytes of the parsed authored
//! document, before the defaults. The returned [`FlatReceipt::binding`] maps `$.<field>` to `<field>` for the listed fields, the explicit path binding that the
//! verifier requires.

use std::collections::BTreeMap;

use crate::canonical::{canonical_bytes, canonical_digest, CanonicalError};
use crate::materialize::{
    entry, materialize_flat, text, MaterializeError, Materialized, MATERIALIZE_NO_DOCUMENT,
};
use crate::node::{Node, Value};
use crate::recognize::{logical_path, Seg};
use crate::schema::{compile_schema, SchemaInput};
use crate::schema_identity::{bind_schema_identity, IdentityFailure};

pub const RECEIPT_SCHEMA_VERSION_EMPTY: &str = "schema_version_empty";
pub const RECEIPT_RELEASE_FIELD: &str = "release_field";
pub const RECEIPT_ENGINE_FIELD: &str = "engine_field";
pub const RECEIPT_PATH_NOT_ONE_SEGMENT: &str = "path_not_one_segment";

/// The release and grammar identity of a **fixture** receipt: stated by the caller, checked for form, not for origin.
#[derive(Debug, Clone)]
pub struct ReleaseFixture {
    pub primitive_release: String,
    pub primitive_source_commit: String,
    pub primitive_build_hash: String,
    pub grammar_sha256: String,
    pub grammar_schema_sha256: String,
}

/// The implementation that materialized: its identity and its own version.
#[derive(Debug, Clone)]
pub struct EngineIdentity {
    pub identity: String,
    pub version: String,
}

/// Why no receipt was made.
#[derive(Debug, Clone, PartialEq)]
pub enum ReceiptError {
    /// The schema did not compile, or its identity breaks the D1.3 contract.
    Identity(IdentityFailure),
    /// The document was not materialized.
    Materialize(MaterializeError),
    /// A value has no canonical bytes.
    Canonical(CanonicalError),
    /// A refusal of this module: one of the `RECEIPT_*` reasons, and the field it is about.
    Refused { reason: &'static str, field: String },
}

fn refused(reason: &'static str, field: &str) -> ReceiptError {
    ReceiptError::Refused {
        reason,
        field: field.to_string(),
    }
}

/// A receipt, its canonical bytes and digest, the explicit path binding the verifier needs, and the object it describes.
#[derive(Debug, Clone)]
pub struct FlatReceipt {
    /// The canonical bytes of `{"materialization": ...}`, as text.
    pub document: String,
    /// `sha256:` and the hex digest of those bytes.
    pub digest: String,
    /// `$.<field>` to `<field>`, for every entry of `applied_defaults`.
    pub binding: BTreeMap<String, String>,
    /// The canonical bytes of the parsed authored document, as text (what `input_digest` is the digest of).
    pub authored_input: String,
    /// The object the receipt describes.
    pub materialized: Materialized,
}

fn is_digest(s: &str) -> bool {
    s.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64 && hex.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    })
}

fn check_text(s: &str, reason: &'static str, name: &str) -> Result<(), ReceiptError> {
    if s.is_empty() {
        return Err(refused(reason, name));
    }
    Ok(())
}

fn check_digest(s: &str, reason: &'static str, name: &str) -> Result<(), ReceiptError> {
    if is_digest(s) {
        return Ok(());
    }
    Err(refused(reason, name))
}

fn one_segment(name: &str) -> bool {
    logical_path(&vec![Seg::Field(name.to_string())]).as_deref() == Some(&format!("$.{name}"))
}

fn int(i: i64) -> Node {
    Node::new(Value::Int(i))
}

fn map(entries: Vec<(&str, Node)>) -> Node {
    Node::new(Value::Map(
        entries.into_iter().map(|(k, v)| entry(k, v)).collect(),
    ))
}

/// Makes the receipt of the flat document `doc` against `input`, as a **fixture** (see the module comment). `a` and `capabilities` are as for
/// [`materialize_flat`].
///
/// The order of the checks is part of the contract: the schema identity (compile, digest binding, namespace, kind), the schema version, the release fields (in
/// the order of the struct), the engine fields, the presence of the document (before the capability map, so a missing document is named first), then the materialization, then the field
/// names, then the canonical bytes.
///
/// # Errors
/// A [`ReceiptError`] with the reason of the module comment.
pub fn receipt_flat_fixture(
    input: &SchemaInput<'_>,
    doc: Option<&Node>,
    a: &[String],
    capabilities: &BTreeMap<String, String>,
    release: &ReleaseFixture,
    engine: &EngineIdentity,
) -> Result<FlatReceipt, ReceiptError> {
    let compiled =
        compile_schema(input).map_err(|e| ReceiptError::Identity(IdentityFailure::Schema(e)))?;
    // compile_schema has verified `input.bytes` against `input.sha256`; the claim is that same pin
    let claimed = format!("sha256:{}", input.sha256);
    let (schema_name, schema_digest) = bind_schema_identity(
        input.bytes,
        Some(&claimed),
        Some(&compiled.namespace),
        Some(&compiled.kind),
    )
    .map_err(|e| ReceiptError::Identity(IdentityFailure::Identity(e)))?;
    check_text(
        &compiled.version,
        RECEIPT_SCHEMA_VERSION_EMPTY,
        "schema_version",
    )?;

    check_text(
        &release.primitive_release,
        RECEIPT_RELEASE_FIELD,
        "primitive_release",
    )?;
    check_text(
        &release.primitive_source_commit,
        RECEIPT_RELEASE_FIELD,
        "primitive_source_commit",
    )?;
    check_digest(
        &release.primitive_build_hash,
        RECEIPT_RELEASE_FIELD,
        "primitive_build_hash",
    )?;
    check_digest(
        &release.grammar_sha256,
        RECEIPT_RELEASE_FIELD,
        "grammar_sha256",
    )?;
    check_digest(
        &release.grammar_schema_sha256,
        RECEIPT_RELEASE_FIELD,
        "grammar_schema_sha256",
    )?;
    check_text(&engine.identity, RECEIPT_ENGINE_FIELD, "engine_identity")?;
    check_text(&engine.version, RECEIPT_ENGINE_FIELD, "engine_version")?;

    let doc = match doc {
        Some(doc) => doc,
        None => {
            return Err(ReceiptError::Materialize(MaterializeError {
                reason: MATERIALIZE_NO_DOCUMENT,
                field: String::new(),
            }))
        }
    };
    let materialized = materialize_flat(Some(doc), &compiled.root, a, capabilities)
        .map_err(ReceiptError::Materialize)?;
    for f in &materialized.fields {
        if !one_segment(&f.key) {
            return Err(refused(RECEIPT_PATH_NOT_ONE_SEGMENT, &f.key));
        }
    }
    let authored = canonical_bytes(doc).map_err(ReceiptError::Canonical)?;

    let mut defaults: Vec<(String, String)> = Vec::new();
    let mut binding = BTreeMap::new();
    for f in &materialized.fields {
        if f.provenance != "schema_default" {
            continue;
        }
        let path = format!("$.{}", f.key);
        let bytes = canonical_bytes(&f.value).map_err(ReceiptError::Canonical)?;
        defaults.push((path.clone(), canonical_digest(&bytes)));
        binding.insert(path, f.key.clone());
    }
    // no sort: the fields are in byte order of the key, every path is `$.` and the key (one plain segment, so no escape changes a byte), and the order of the
    // paths is therefore the order of the keys; the engine's verifier refuses any other order (R-RECEIPT-ORDER, seen in the harness)
    let applied = Node::new(Value::Seq(
        defaults
            .iter()
            .map(|(path, digest)| {
                map(vec![
                    ("path", text(path)),
                    ("rule", text("schema-default")),
                    ("value_digest", text(digest)),
                ])
            })
            .collect(),
    ));

    let body = map(vec![
        ("receipt_schema_version", int(1)),
        ("grammar_sha256", text(&release.grammar_sha256)),
        (
            "grammar_schema_sha256",
            text(&release.grammar_schema_sha256),
        ),
        ("primitive_release", text(&release.primitive_release)),
        (
            "primitive_source_commit",
            text(&release.primitive_source_commit),
        ),
        ("primitive_build_hash", text(&release.primitive_build_hash)),
        ("schema_name", text(&schema_name)),
        ("schema_version", text(&compiled.version)),
        ("schema_digest", text(&schema_digest)),
        ("engine_identity", text(&engine.identity)),
        ("engine_version", text(&engine.version)),
        ("input_digest", text(&canonical_digest(&authored))),
        ("output_digest", text(&materialized.output_digest)),
        ("applied_defaults", applied),
        ("derived_values", Node::new(Value::Seq(Vec::new()))),
    ]);
    let bytes =
        canonical_bytes(&map(vec![("materialization", body)])).map_err(ReceiptError::Canonical)?;
    Ok(FlatReceipt {
        digest: canonical_digest(&bytes),
        document: String::from_utf8_lossy(&bytes).into_owned(),
        binding,
        authored_input: String::from_utf8_lossy(&authored).into_owned(),
        materialized,
    })
}
