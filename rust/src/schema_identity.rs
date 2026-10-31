//! The schema identity of a receipt (D1.3; P5-3, `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md` section 16). The Go peer is `go/schemaidentity.go`.
//!
//! This is this library's side of the contract that `cic-primitive-engine` fixed in its PR #64 (`docs/RECEIPT-SCHEMA.md`, "Schema identity contract"), under
//! the same names, rules and codes, so that the engine's golden and negative vectors run unchanged through it:
//!
//! - `schema_digest` is `"sha256:"` and the lowercase hex of the SHA-256 of the **raw bytes of the pinned schema file**. Nothing is parsed, normalized or
//!   re-serialized first; the line endings and the final newline are part of the identity.
//! - `schema_name` is `identity.namespace + ":" + identity.kind`, as authored: both non-empty, the namespace made of non-empty `:`-separated components, the
//!   kind without a colon, nothing trimmed or case-folded.
//! - The order of the checks is the digest binding, then the namespace, then the kind.
//!
//! [`bind_schema_identity`] is the contract on loose values, as the engine has it. It cannot know that the namespace and the kind were read from the bytes it
//! is given, and the engine's review made that binding mandatory for the receipt. [`schema_identity`] is where it is made true: it takes the [`SchemaInput`]
//! (the bytes and their pinned digest), compiles it (which verifies the bytes against the pin and reads the identity **from those bytes**), and builds the
//! name, the version and the digest from that one compile. There is no way to hand it a component from another file.
//!
//! This establishes what two receipt fields mean and that they come from one verified source. It does not establish where the source came from.

use crate::canonical::canonical_digest;
use crate::error::Error as SchemaError;
use crate::schema::{compile_schema, SchemaInput};

pub const SCHEMA_INVALID_IDENTITY: &str = "E_INVALID_SCHEMA_IDENTITY";
pub const SCHEMA_SOURCE_BINDING: &str = "E_SCHEMA_SOURCE_BINDING";

/// Why an identity was refused: the code and the rule of the engine's contract, and the path it names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaIdentityError {
    pub code: &'static str,
    pub rule: &'static str,
    pub path: &'static str,
}

impl std::fmt::Display for SchemaIdentityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} ({}) at {}", self.code, self.rule, self.path)
    }
}

impl std::error::Error for SchemaIdentityError {}

/// What the two refusals of [`schema_identity`] can be: the schema did not compile (its bytes are not the pinned ones, or not a schema this library reads),
/// or it compiled and its identity is not a valid one.
#[derive(Debug, Clone, PartialEq)]
pub enum IdentityFailure {
    Schema(SchemaError),
    Identity(SchemaIdentityError),
}

/// The three schema fields of a receipt, from one verified source.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaIdentity {
    pub schema_name: String,
    /// `identity.version`, as authored.
    pub schema_version: String,
    pub schema_digest: String,
}

const NAMESPACE_PATH: &str = "$.spec.identity.namespace";
const KIND_PATH: &str = "$.spec.identity.kind";
const DIGEST_PATH: &str = "$.schema_digest";

fn invalid(rule: &'static str, path: &'static str) -> SchemaIdentityError {
    SchemaIdentityError {
        code: SCHEMA_INVALID_IDENTITY,
        rule,
        path,
    }
}

fn binding(rule: &'static str) -> SchemaIdentityError {
    SchemaIdentityError {
        code: SCHEMA_SOURCE_BINDING,
        rule,
        path: DIGEST_PATH,
    }
}

/// `"sha256:"` and the lowercase hex SHA-256 of `source`, over the bytes as they are.
#[must_use]
pub fn schema_digest(source: &[u8]) -> String {
    canonical_digest(source)
}

/// `namespace + ":" + kind`, exactly as given, if both are well formed.
///
/// # Errors
/// `R-SCHEMA-NAME-NAMESPACE` or `R-SCHEMA-NAME-KIND` of `E_INVALID_SCHEMA_IDENTITY`.
pub fn schema_name(
    namespace: Option<&str>,
    kind: Option<&str>,
) -> Result<String, SchemaIdentityError> {
    let namespace = match namespace {
        Some(n) if !n.is_empty() && !n.split(':').any(str::is_empty) => n,
        _ => return Err(invalid("R-SCHEMA-NAME-NAMESPACE", NAMESPACE_PATH)),
    };
    let kind = match kind {
        Some(k) if !k.is_empty() && !k.contains(':') => k,
        _ => return Err(invalid("R-SCHEMA-NAME-KIND", KIND_PATH)),
    };
    Ok(format!("{namespace}:{kind}"))
}

/// The engine's contract on loose values: `claimed_digest` must be the digest of `source`, then the name is formed. Returns the name and the digest.
///
/// # Errors
/// `R-SCHEMA-DIGEST-MISSING` or `-MISMATCH` of `E_SCHEMA_SOURCE_BINDING`, else the errors of [`schema_name`].
pub fn bind_schema_identity(
    source: &[u8],
    claimed_digest: Option<&str>,
    namespace: Option<&str>,
    kind: Option<&str>,
) -> Result<(String, String), SchemaIdentityError> {
    let claimed = match claimed_digest {
        Some(claimed) => claimed,
        None => return Err(binding("R-SCHEMA-DIGEST-MISSING")),
    };
    let actual = schema_digest(source);
    if claimed != actual {
        return Err(binding("R-SCHEMA-DIGEST-MISMATCH"));
    }
    Ok((schema_name(namespace, kind)?, actual))
}

/// The schema identity of `input`, with the namespace, the kind and the version read from the very bytes whose digest is stated.
///
/// # Errors
/// [`IdentityFailure::Schema`] when the bytes are not the pinned ones or not a schema this library reads; [`IdentityFailure::Identity`] when its identity
/// breaks the contract.
pub fn schema_identity(input: &SchemaInput<'_>) -> Result<SchemaIdentity, IdentityFailure> {
    let compiled = compile_schema(input).map_err(IdentityFailure::Schema)?;
    // compile_schema has verified `input.bytes` against `input.sha256`; the claim below is that same pin, so the digest bound is the one of the bytes read
    let claimed = format!("sha256:{}", input.sha256);
    let (schema_name, schema_digest) = bind_schema_identity(
        input.bytes,
        Some(&claimed),
        Some(&compiled.namespace),
        Some(&compiled.kind),
    )
    .map_err(IdentityFailure::Identity)?;
    Ok(SchemaIdentity {
        schema_name,
        schema_version: compiled.version,
        schema_digest,
    })
}
