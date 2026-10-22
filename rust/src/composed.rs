//! `normalize_with_policy` (P3 closing round, `docs/P3-NORMALIZE-DESIGN.md` section 2): the normalization and the effective rule of every
//! position of its object, made from one compiled schema in one operation. The Rust peer of `go/normalizewithpolicy.go`.

use crate::access::Grammar;
use crate::error::Error;
use crate::expand::Context;
use crate::node::Node;
use crate::normalize::{normalize, NormalizationResult};
use crate::policyresolution::{resolve_policy, PolicyResolution};
use crate::schema::CompiledSchema;

/// The one result of [`normalize_with_policy`]. The rules are data, not a decision.
#[derive(Debug, Clone)]
pub struct PolicyNormalization {
    pub normalization: NormalizationResult,
    pub policy_resolution: PolicyResolution,
    /// The digest of the compiled schema both parts were made from.
    pub schema_digest: String,
}

/// [`normalize`] followed by [`resolve_policy`] on the normalized object, both with the one compiled schema `c`, so a caller cannot pair
/// the two parts with different schemas. `unknown` names, by logical path, the objects whose policy the caller does not know (`$` is
/// the root). If either step fails there is no result. It decides nothing and does not replace validation: `normalize` refuses a
/// document that does not validate, as before.
///
/// # Errors
/// Whatever [`normalize`] or [`resolve_policy`] return.
pub fn normalize_with_policy(
    doc: &Node,
    c: &CompiledSchema,
    a: &[String],
    g: Grammar,
    ctx: &Context,
    unknown: &[String],
) -> Result<PolicyNormalization, Error> {
    let normalization = normalize(doc, &c.root, a, g, ctx)?;
    let policy_resolution = resolve_policy(
        &normalization.object,
        &c.root,
        &c.policy,
        &c.surface,
        a,
        g,
        unknown,
    )?;
    Ok(PolicyNormalization {
        normalization,
        policy_resolution,
        schema_digest: c.digest.clone(),
    })
}

/// Probe of the diff gate (not to be merged): product code that no test covers, so its mutants survive.
#[must_use]
pub fn probe_len(x: &str) -> usize {
    x.len()
}
