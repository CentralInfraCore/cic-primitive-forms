//! The error contract of ADR 0007 and the errors outside it.

use crate::node::Pos;

/// The phases of the machine response (ADR 0007); a response holds one phase only.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Parse,
    Recognize,
    Validate,
    /// The identity of a list element could not be built (`path_of`).
    PathOf,
    /// The phases of the path operations (ADR 0006): no source location, only the text of the path.
    PathParse,
    PathFormat,
    PathResolve,
    /// The phase of a source-preserving edit and of the span it works on (ADR 0012).
    Edit,
    /// The phase of `diff` itself (ADR 0008): the contexts of the views differ, or a view holds a value the diff refuses.
    Diff,
    /// The phase of `patch` itself (ADR 0013).
    Patch,
}

impl Phase {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Phase::Parse => "parse",
            Phase::Recognize => "recognize",
            Phase::Validate => "validate",
            Phase::PathOf => "path_of",
            Phase::PathParse => "path_parse",
            Phase::PathFormat => "path_format",
            Phase::PathResolve => "path_resolve",
            Phase::Edit => "edit",
            Phase::Diff => "diff",
            Phase::Patch => "patch",
        }
    }
}

/// `source_location` of ADR 0007: coordinates, or `unavailable` for a document that was
/// supplied as an already parsed value. A position is never invented.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Location {
    At(Pos),
    Unavailable,
    /// No YAML node can be at fault (a context error, a path operation): neither coordinates nor merely unavailable.
    NotApplicable,
}

/// One entry of the machine response (code, phase, source location, logical path).
/// `message` is not part of it (ADR 0007).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    pub code: &'static str,
    pub phase: Phase,
    pub location: Location,
    /// The canonical ADR 0006 path of the offending location. Absent where ADR 0007 leaves it
    /// out: the parse phase, a single position with no structure around it, and a location
    /// the synthetic schema cannot name (a list element has no declared identity).
    pub logical_path: Option<String>,
    /// The offset in code points of the offending character in the path text, for phase `path_parse` only (ADR 0007): it is
    /// present there, `0` included, and never invented for another phase.
    pub path_offset: Option<usize>,
    /// `a` or `b` when the error concerns one view of a diff (ADR 0008), and `None` for every other error.
    pub operand: Option<&'static str>,
}

/// A failure. Only [`Error::Contract`] is the ADR 0007 machine response; the others are
/// the boundary of the supported subset and carry no registry code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// A response of the error contract: an ordered, non-empty list of one phase.
    Contract(Vec<Violation>),
    /// Input the supported subset does not read (anchors, tags, floats, several
    /// documents, ...). Never reinterpreted silently.
    Unsupported(String),
    /// The YAML library rejected the text.
    Syntax(String),
    /// A resource limit was exceeded.
    Limit(String),
    /// The grammar version or the pinned schema could not be bound. The code is the upstream
    /// contract's own (`GRAMMAR_VERSION_MISSING`, `UNKNOWN_GRAMMAR_VERSION`,
    /// `GRAMMAR_VERSION_CONFLICT`) or `GRAMMAR_DIGEST_MISMATCH` (ADR 0002); none of them is
    /// renamed. Nothing falls back to a default version.
    Binding(&'static str),
    /// A pinned schema that `compile_schema` refused: a structure outside ADR 0002 or a Shape feature
    /// this slice does not process. Non-empty; the code is the first problem's.
    Schema(Vec<SchemaProblem>),
}

/// One refusal of a schema: a code and the canonical path of the schema node. The detail is
/// for people and not part of the contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaProblem {
    pub code: &'static str,
    pub path: String,
    pub detail: String,
}

impl Error {
    /// The stable code of this error. The first three are outside the registry of
    /// `corpus/codes.yaml`; a contract response reports its first violation's code.
    #[must_use]
    pub fn code(&self) -> &'static str {
        match self {
            Error::Contract(v) => v[0].code,
            Error::Unsupported(_) => "UNSUPPORTED_SYNTAX",
            Error::Syntax(_) => "YAML_SYNTAX",
            Error::Limit(_) => "LIMIT_EXCEEDED",
            Error::Binding(code) => code,
            Error::Schema(problems) => problems[0].code,
        }
    }
}
