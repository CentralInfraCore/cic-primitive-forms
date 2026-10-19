package forms

import "fmt"

// Phase is a phase of the machine response (ADR 0007); a response holds one phase only.
type Phase string

// The phases this slice produces.
const (
	PhaseParse     Phase = "parse"
	PhaseRecognize Phase = "recognize"
	PhaseValidate  Phase = "validate"
)

// Location is the source_location of ADR 0007: coordinates, or unavailable for a document
// supplied as an already parsed value. A position is never invented.
type Location struct {
	Available bool
	Pos       Pos
}

// Violation is one entry of the machine response. The message is not part of it.
type Violation struct {
	Code     string
	Phase    Phase
	Location Location
}

// ErrorKind says which boundary an Error is.
type ErrorKind int

// The kinds of Error. Only ErrContract is the ADR 0007 machine response; the others are
// the boundary of the supported subset and carry no registry code.
const (
	ErrContract ErrorKind = iota
	ErrUnsupported
	ErrSyntax
	ErrLimit
)

// Error is a failure of Parse or Recognize.
type Error struct {
	Kind       ErrorKind
	Violations []Violation
	Detail     string
}

// Code is the stable code of the error. The codes of the last three kinds are outside the
// registry of corpus/codes.yaml; a contract response reports its first violation's code.
func (e *Error) Code() string {
	switch e.Kind {
	case ErrContract:
		return e.Violations[0].Code
	case ErrUnsupported:
		return "UNSUPPORTED_SYNTAX"
	case ErrSyntax:
		return "YAML_SYNTAX"
	default:
		return "LIMIT_EXCEEDED"
	}
}

func (e *Error) Error() string {
	return fmt.Sprintf("%s: %s", e.Code(), e.Detail)
}

func unsupported(format string, a ...any) *Error {
	return &Error{Kind: ErrUnsupported, Detail: fmt.Sprintf(format, a...)}
}

func limit(format string, a ...any) *Error {
	return &Error{Kind: ErrLimit, Detail: fmt.Sprintf(format, a...)}
}
