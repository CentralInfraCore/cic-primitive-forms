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

pub mod emit;
pub mod error;
pub mod node;
pub mod parse;
pub mod recognize;
pub mod validate;

pub use emit::emit;
pub use error::{Error, Location, Phase, Violation};
pub use node::{Entry, Node, Pos, Value};
pub use parse::{parse, parse_with, Limits};
pub use recognize::{recognize, Class, Outcome, Position};
pub use validate::{validate, Presence, ScalarType, Shape, ShapeType, Validation};
