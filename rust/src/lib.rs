//! Schema-driven processing of CIC primitive documents: the P1 vertical slice.
//!
//! Supported subset (see `docs/P1-SUPPORTED-SUBSET.md`): [`parse`] reads a single
//! YAML document into a [`Node`] tree with source positions, [`recognize`] applies
//! the ADR 0001 rule and the position validation of ADR 0003/0007 at the
//! positions of a synthetic [`Position`] schema, [`validate`] checks presence, nullability, type and
//! `min_items` against a [`Shape`], and [`emit`] writes a
//! deterministic block-style YAML text. Nothing here modifies its input.
//!
//! The expanded-form key set `A` is never hardcoded: the caller passes it in.

pub mod access;
pub mod composed;
pub mod defaults;
pub mod diff;
pub mod editscalar;
pub mod emit;
pub mod error;
pub mod expand;
pub mod listitem;
pub mod node;
pub mod normalize;
pub mod parse;
pub mod pathtext;
pub mod policy;
pub mod policyresolution;
pub mod recognize;
pub mod resolve;
pub(crate) mod role;
pub mod schema;
pub mod sourcespan;
pub mod validate;

pub use access::{
    bind_grammar, is_cert_pattern, pinned_grammar_version, read_mode, read_rule, sha256_hex,
    write_mode, Grammar, Mode, Rule, INHERIT_SYMBOL, SUPPORTED_GRAMMARS,
};
pub use composed::{normalize_with_policy, PolicyNormalization};
pub use defaults::{apply_defaults, Defaulted, ValueOrigin, VALUE_AUTHORED, VALUE_DEFAULT};
pub use diff::{
    diff, DeclChange, DiffContext, DiffEvent, DiffResult, DiffView, EventKind, RepresentationChange,
};
pub use editscalar::{edit_parsed, edit_scalar, EditResult};
pub use emit::emit;
pub use error::{Error, Location, Phase, SchemaProblem, Violation};
pub use expand::{
    collapse, collapse_position, expand, expand_position, Context, DocOrigin, Origin, Provenance,
    BASIS_BINDING, ORIGIN_ATTESTATION_MISMATCH,
};
pub use listitem::{path_of, Case, Elements, KeyComp, KeyValue, Step};
pub use node::{Entry, Node, Pos, Value};
pub use normalize::{denormalize, normalize, renormalize, NormalizationResult};
pub use parse::{parse, parse_with, Limits};
pub use pathtext::{format_path, parse_path, resolve_path, PathError, PathSeg, Resolved};
pub use policy::{read_policy_surface, PolicyRule, PolicySurface, SurfaceRule};
pub use policyresolution::{resolve_policy, PolicyEntry, PolicyResolution};
pub use recognize::{recognize, Class, Outcome, Position};
pub use resolve::{
    resolve_rule, Carrier, EffectiveRule, ObjectPolicy, PolicyGraph, PolicyObject, RuleKind,
    LAYER_FIELD, LAYER_FIELD_INHERITED, LAYER_NONE, LAYER_OBJECT_DEFAULT, LAYER_POLICY_INHERITED,
    LAYER_SURFACE_RULE, LAYER_SYSTEM, REASON_CONTEXT_INCOMPLETE, REASON_POLICY_CONTEXT_UNKNOWN,
    REASON_RESET_NO_LOCAL_RULE, REASON_UNKNOWN_OPERATION,
};
pub use schema::{compile_schema, CompiledSchema, SchemaInput};
pub use sourcespan::{source_span, SourceSpan, SpanStyle};
pub use validate::{validate, DefaultValue, Presence, ScalarType, Shape, ShapeType, Validation};
