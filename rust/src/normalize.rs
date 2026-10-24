//! Normalize and denormalize (P3, third unit; `docs/P3-UNIT3-COMPOSITION.md`). The Rust peer of `go/normalize.go`; the shared
//! cases are `testdata/p1/normalize.json`.
//!
//! `normalize` is `expand`, then the declared default of every absent optional field inserted into the expanded tree. The
//! author's own writing is recorded by `expand` ([`Provenance`], how each position was written) and the origin of each value
//! (authored or default) is recorded where the default is inserted, in a side map: neither is read back out of the values. An
//! inserted default is an Access atom like any position (the symbolic policy of the bound grammar, `default_injection` null,
//! ADR 0003 rule 6) and has no provenance entry. `denormalize` is the inverse: it removes the values whose origin is default
//! and collapses with the provenance, so that `denormalize(normalize(x))` is `x` as a value, for every document that `expand`
//! accepts, short, expanded or mixed, with explicit policy settings, `null`, and values that equal their default.
//!
//! `normalize` validates before it walks (#88): a document that `validate` refuses (with every expanded wrapper read as its
//! value) - an undeclared key, a value of the wrong type, two list elements of one identity, a missing mandatory field - is
//! refused with its violations, and an authored expanded wrapper whose access, modify or inherit the bound grammar does not
//! read is refused as the resolution refuses it (#90). It takes the trusted [`Context`] of ADR 0010. It never inserts a
//! default for a mandatory field, into an absent composite or list, or over a present value.

use std::collections::{BTreeMap, BTreeSet};

use crate::access::Grammar;
use crate::defaults::{ValueOrigin, VALUE_DEFAULT};
use crate::error::Error;
use crate::expand::{Context, Provenance, Walker};
use crate::node::Node;
use crate::recognize::logical_path;
use crate::validate::Shape;

/// What [`normalize`] produces: the object, how the author wrote it, and who made each value.
#[derive(Debug, Clone)]
pub struct NormalizationResult {
    /// The expanded document with the defaults inserted. It is not a pure data tree to be processed without
    /// `value_origins`: the two travel together.
    pub object: Node,
    /// The P2 record, only for the positions the author wrote. An inserted position has none.
    pub provenance: Provenance,
    /// Every declared position of `object` in path order, authored or default (see `apply_defaults`).
    pub value_origins: Vec<ValueOrigin>,
}

fn origins_of(mut found: Vec<(crate::recognize::Path, &'static str)>) -> Vec<ValueOrigin> {
    found.sort_by(|x, y| x.0.cmp(&y.0));
    found
        .into_iter()
        .map(|(p, origin)| ValueOrigin {
            path: logical_path(&p).unwrap_or_default(),
            origin,
        })
        .collect()
}

/// Expands `doc` and inserts the declared defaults of its absent optional fields. The input is not modified. A document that
/// does not validate (every expanded wrapper read as its value) is refused with its violations, as they are, and nothing is made:
/// the walk checks the forms, not the values or the list identities (#88).
///
/// # Errors
/// A contradictory context; the refusal of the validation; then those of [`crate::expand`]: an undeclared key
/// (`UNKNOWN_FIELD`), a structure the schema does not allow.
pub fn normalize(
    doc: &Node,
    shape: &Shape,
    a: &[String],
    g: Grammar,
    ctx: &Context,
) -> Result<NormalizationResult, Error> {
    ctx.check(shape)?;
    crate::editscalar::refuse_invalid(doc, shape, a)?;
    let mut w = Walker::plain(a, g, Provenance::new(), Vec::new());
    w.defaults = true;
    w.members = true;
    let object = w.record(doc, shape, &mut Vec::new(), true)?;
    Ok(NormalizationResult {
        object,
        provenance: w.prov,
        value_origins: origins_of(w.origins),
    })
}

/// Normalizes a result again, keeping what it knows: how the author wrote each position and which values the normalization
/// made. The object of a result is already expanded, so a plain [`normalize`] of it would report every position as authored
/// expanded and every inserted default as authored; this one returns the same object, the same provenance and the same
/// value origins (idempotence is a property of the pair, not of the object alone). A position the result does not know is
/// reported as authored: it is written in the object.
///
/// # Errors
/// Those of [`normalize`].
pub fn renormalize(
    prior: &NormalizationResult,
    shape: &Shape,
    a: &[String],
    g: Grammar,
    ctx: &Context,
) -> Result<NormalizationResult, Error> {
    let again = normalize(&prior.object, shape, a, g, ctx)?;
    let known: BTreeMap<&str, &'static str> = prior
        .value_origins
        .iter()
        .map(|o| (o.path.as_str(), o.origin))
        .collect();
    let mut provenance = Provenance::new();
    let mut value_origins = Vec::with_capacity(again.value_origins.len());
    for o in &again.value_origins {
        let kept = known.get(o.path.as_str()).copied().unwrap_or(o.origin);
        value_origins.push(ValueOrigin {
            path: o.path.clone(),
            origin: kept,
        });
        if kept == VALUE_DEFAULT {
            continue; // the author did not write it: no provenance
        }
        let entry = prior
            .provenance
            .get(&o.path)
            .or_else(|| again.provenance.get(&o.path));
        if let Some(entry) = entry {
            provenance.insert(o.path.clone(), *entry);
        }
    }
    Ok(NormalizationResult {
        object: again.object,
        provenance,
        value_origins,
    })
}

/// Gives back the authored document: the values the normalization made are removed, then the object is collapsed with the
/// provenance and the trusted [`Context`] (ADR 0001, ADR 0010). The result is a value-equal copy of what was normalized.
///
/// # Errors
/// Those of [`crate::collapse`].
pub fn denormalize(
    res: &NormalizationResult,
    shape: &Shape,
    a: &[String],
    g: Grammar,
    ctx: &Context,
) -> Result<Node, Error> {
    ctx.check(shape)?;
    let mut w = Walker::plain(
        a,
        g,
        res.provenance.clone(),
        ctx.attested_positions.clone().unwrap_or_default(),
    );
    w.strip = res
        .value_origins
        .iter()
        .filter(|o| o.origin == VALUE_DEFAULT)
        .map(|o| o.path.clone())
        .collect::<BTreeSet<_>>();
    w.record(&res.object, shape, &mut Vec::new(), false)
}
