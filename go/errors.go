package forms

import "fmt"

// Phase is a phase of the machine response (ADR 0007); a response holds one phase only.
type Phase string

// The phases this slice produces.
const (
	// PhaseParse is the phase of reading the YAML text into a node tree.
	PhaseParse Phase = "parse"
	// PhaseRecognize is the phase of telling a short from an expanded Access writing (ADR 0001).
	PhaseRecognize Phase = "recognize"
	// PhaseValidate is the phase of checking a document against its schema.
	PhaseValidate Phase = "validate"
	// PhasePathOf is the phase of PathOf: the identity of a list element could not be built.
	PhasePathOf Phase = "path_of"
	// The phases of the path operations (ADR 0006): no source location, only the text of the path.
	PhasePathParse Phase = "path_parse"
	// PhasePathFormat is the phase of writing segments as a canonical path text (FormatPath).
	PhasePathFormat Phase = "path_format"
	// PhasePathResolve is the phase of resolving a parsed path against the schema (ResolvePath).
	PhasePathResolve Phase = "path_resolve"
	// PhaseEdit is the phase of a source-preserving edit and of the span it works on (ADR 0012).
	PhaseEdit Phase = "edit"
	// PhaseDiff is the phase of Diff itself (ADR 0008): the contexts of the views differ, or a view holds a value the diff refuses.
	PhaseDiff Phase = "diff"
)

// Location is the source_location of ADR 0007: coordinates, or unavailable for a document
// supplied as an already parsed value. A position is never invented.
//
// NotApplicable is the third state: no YAML node can be at fault (a context error, a path operation), so the location is
// neither coordinates nor merely unavailable. The zero Location is unavailable.
type Location struct {
	Available     bool
	NotApplicable bool
	Pos           Pos
}

// Violation is one entry of the machine response (code, phase, source location, logical
// path). The message is not part of it (ADR 0007).
type Violation struct {
	Code     string
	Phase    Phase
	Location Location
	// LogicalPath is the canonical ADR 0006 path of the offending location; "" where ADR 0007
	// leaves it out: the parse phase, a single position with no structure around it, and a
	// location the synthetic schema cannot name (a list element has no declared identity).
	LogicalPath string
	// PathOffset is the offset in code points of the offending character in the path text, for phase path_parse only
	// (ADR 0007): present there, zero included, and nil for every other phase.
	PathOffset *int
	// Operand is "a" or "b" when the error concerns one view of a diff (ADR 0008), and "" for every other error.
	Operand string
}

// ErrorKind says which boundary an Error is.
type ErrorKind int

// The kinds of Error. Only ErrContract is the ADR 0007 machine response; the others are
// the boundary of the supported subset and carry no registry code.
const (
	// ErrContract is the ADR 0007 machine response: Violations holds the registry codes, Code is the first one's.
	ErrContract ErrorKind = iota
	// ErrUnsupported is an input outside the supported subset (UNSUPPORTED_SYNTAX); refused, never guessed.
	ErrUnsupported
	// ErrSyntax is text that is not well-formed YAML (YAML_SYNTAX).
	ErrSyntax
	// ErrLimit is an input beyond a resource limit of the reader (LIMIT_EXCEEDED).
	ErrLimit
	// ErrBinding is a grammar version or a pinned schema that could not be bound. Its code is the
	// upstream contract's own (GRAMMAR_VERSION_MISSING, UNKNOWN_GRAMMAR_VERSION,
	// GRAMMAR_VERSION_CONFLICT) or GRAMMAR_DIGEST_MISMATCH (ADR 0002); none is renamed. Nothing falls
	// back to a default version.
	ErrBinding
	// ErrSchema is a pinned schema that CompileSchema refused: a structure outside ADR 0002 or a
	// Shape feature this slice does not process. Its code is SchemaCode; the Detail names the node.
	ErrSchema
)

// Error is the failure of an operation of this package: a contract response (ErrContract, with its violations) or one of the
// boundary kinds that carry no registry code.
type Error struct {
	Kind       ErrorKind
	Violations []Violation
	Detail     string
	// BindingCode is the code of an ErrBinding error.
	BindingCode string
	// SchemaCode is the code of an ErrSchema error: the code of its first problem.
	SchemaCode string
	// Problems are every refusal of an ErrSchema error, in schema order: the first problem of each
	// node, so a schema is reported in one pass and not one defect at a time.
	Problems []SchemaProblem
}

// SchemaProblem is one refusal of a schema: a code and the canonical path of the schema node.
type SchemaProblem struct {
	Code, Path, Detail string
}

// Code is the stable code of the error. The codes of every kind but ErrContract are outside the
// registry of corpus/codes.yaml; a contract response reports its first violation's code.
func (e *Error) Code() string {
	switch e.Kind {
	case ErrContract:
		return e.Violations[0].Code
	case ErrUnsupported:
		return "UNSUPPORTED_SYNTAX"
	case ErrBinding:
		return e.BindingCode
	case ErrSchema:
		return e.SchemaCode
	case ErrSyntax:
		return "YAML_SYNTAX"
	default:
		return "LIMIT_EXCEEDED"
	}
}

// Error is the code and the detail.
func (e *Error) Error() string {
	return fmt.Sprintf("%s: %s", e.Code(), e.Detail)
}

// unsupported is an ErrUnsupported *Error with a formatted detail.
func unsupported(format string, a ...any) *Error {
	return &Error{Kind: ErrUnsupported, Detail: fmt.Sprintf(format, a...)}
}

// limit is an ErrLimit *Error with a formatted detail.
func limit(format string, a ...any) *Error {
	return &Error{Kind: ErrLimit, Detail: fmt.Sprintf(format, a...)}
}
