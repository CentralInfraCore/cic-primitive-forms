package forms

// Reading the Access and PolicySurface values of access-policy/1 (cic-primitives D-018).
//
// The normative source is the pinned proposals/atom-grammar/P0-4-ACCESS-GRAMMAR.md and the pinned
// schemas/atomic/access.yaml (see corpus/pins.yaml). This file reads: it checks a value and returns it
// in a typed form. It resolves nothing (P3), decides no permission, applies no default and implements
// no conformance basis.
//
// # Grammar version binding
//
// The version of the inherit spelling is not read from a document and has no default. It is a
// property of the processing context and of the pinned schema (spec.access_grammar.version), the two
// must agree, and one Grammar is bound for a whole document (BindGrammar). The version of the pinned
// schema is derived from the schema's own bytes after their SHA-256 has been checked against the pin
// (PinnedGrammarVersion). A missing version, a value that is not one of the integers in
// SupportedGrammars and a disagreement are three different failures. There is no fallback and no
// conversion between versions: ReadMode accepts the spelling of the bound version only.
//
// # Codes and their source
//
// INVALID_INHERIT, INVALID_ACCESS_VALUE and INVALID_CERT_PATTERN are in the corpus registry.
// INHERIT_ENUM_IN_GRAMMAR_1, LEGACY_INHERIT_IN_GRAMMAR_2, GRAMMAR_VERSION_MISSING,
// UNKNOWN_GRAMMAR_VERSION and GRAMMAR_VERSION_CONFLICT are the upstream contract's own codes
// (P0-4-ACCESS-GRAMMAR.md, "Spelling and versions" and "Grammar version binding"); they are not in
// corpus/codes.yaml and no vector uses them yet. They are emitted unchanged, not renamed.
// GRAMMAR_DIGEST_MISMATCH (ADR 0002) reports a pinned schema whose bytes differ from the pin.

import (
	"crypto/sha256"
	"encoding/hex"
	"slices"
	"strings"
	"unicode"
)

// supportedGrammars are the grammar versions the pinned contract defines. It is not exported as a
// variable: a caller must not be able to add a version the contract does not define.
var supportedGrammars = []int64{1, 2}

// SupportedGrammars returns a copy of the grammar versions the pinned contract defines.
func SupportedGrammars() []int64 { return slices.Clone(supportedGrammars) }

func isSupported(version int64) bool { return slices.Contains(supportedGrammars, version) }

// Grammar is a grammar version, bound once for a whole document.
type Grammar struct{ version int64 }

// Version is the bound version.
func (g Grammar) Version() int64 { return g.version }

func binding(code string) error { return &Error{Kind: ErrBinding, BindingCode: code, Detail: code} }

func isMissing(v *Node) bool { return v == nil || v.Kind == KindNull }

// checked: a version is an integer that is one of SupportedGrammars.
func checked(v *Node) (int64, error) {
	if v.Kind != KindInt || !isSupported(v.Int) {
		return 0, binding("UNKNOWN_GRAMMAR_VERSION")
	}
	return v.Int, nil
}

// BindGrammar binds the version of the processing context against the version of the pinned schema.
//
// Both are required (no default to 1). A missing one (nil or null) is GRAMMAR_VERSION_MISSING, a value
// that is not one of the supported integers ("1", true, 3) is UNKNOWN_GRAMMAR_VERSION, a difference is
// GRAMMAR_VERSION_CONFLICT. Missing is checked before unknown, unknown before conflict. The error, when
// not nil, is an *Error of kind ErrBinding.
func BindGrammar(context, pinned *Node) (Grammar, error) {
	if isMissing(context) || isMissing(pinned) {
		return Grammar{}, binding("GRAMMAR_VERSION_MISSING")
	}
	c, err := checked(context)
	if err != nil {
		return Grammar{}, err
	}
	p, err := checked(pinned)
	if err != nil {
		return Grammar{}, err
	}
	if c != p {
		return Grammar{}, binding("GRAMMAR_VERSION_CONFLICT")
	}
	return Grammar{c}, nil
}

// SHA256Hex is the lower-case hexadecimal SHA-256 of b.
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// PinnedGrammarVersion is the spec.access_grammar.version of the pinned access.yaml, after its bytes
// have been verified against expectedSHA256; nil when the verified schema declares none. A digest that
// does not match is GRAMMAR_DIGEST_MISMATCH (ErrBinding); bytes outside the supported YAML subset give
// the errors of Parse.
func PinnedGrammarVersion(schema []byte, expectedSHA256 string) (*Node, error) {
	if SHA256Hex(schema) != expectedSHA256 {
		return nil, binding("GRAMMAR_DIGEST_MISMATCH")
	}
	doc, err := Parse(string(schema))
	if err != nil {
		return nil, err
	}
	at := doc
	for _, key := range []string{"spec", "access_grammar", "version"} {
		if at = lookup(at, key); at == nil {
			return nil, nil
		}
	}
	return at, nil
}

// lookup is the value of the first entry named key of a mapping, nil when n is not a mapping or has none.
func lookup(n *Node, key string) *Node {
	if n.Kind != KindMap {
		return nil
	}
	for _, e := range n.Entries {
		if e.Key == key {
			return e.Value
		}
	}
	return nil
}

// Mode is InheritanceMode, one concept at two scopes (Access.inherit, PolicySurface.inherit_policy).
type Mode int

// The three states.
const (
	ModeInherit Mode = iota
	ModeNoInherit
	ModeReset
)

// ModeInvalid is what ReadMode returns together with a violation: never one of the three states.
const ModeInvalid Mode = -1

var modeNames = []string{"inherit", "no-inherit", "reset"}

func invalid(code string, n *Node) *Violation {
	return &Violation{Code: code, Phase: PhaseValidate, Location: loc(n.Pos)}
}

// ReadMode reads a spelling of InheritanceMode under the bound grammar. Exactly one spelling set is
// valid per version, the two are never mixed and never coerced.
//
//   - grammar 1: boolean true is inherit, boolean false is no-inherit, integer 0 is reset; told apart
//     by the type of the scalar, so false and 0 are never equal. An enum name is
//     INHERIT_ENUM_IN_GRAMMAR_1; anything else (1, "true", "0", null, a list) is INVALID_INHERIT.
//   - grammar 2: the strings inherit, no-inherit, reset. A legacy spelling (a boolean or integer 0) is
//     LEGACY_INHERIT_IN_GRAMMAR_2; anything else is INVALID_INHERIT.
//
// A violation is anchored at the value.
func ReadMode(n *Node, g Grammar) (Mode, *Violation) {
	// A Grammar that was not bound to a supported version (the zero value, or any other) reads nothing:
	// there is no implicit grammar 2.
	if !isSupported(g.version) {
		return ModeInvalid, invalid("UNKNOWN_GRAMMAR_VERSION", n)
	}
	isEnum := n.Kind == KindStr && slices.Contains(modeNames, n.Str)
	isLegacy := n.Kind == KindBool || (n.Kind == KindInt && n.Int == 0)
	if g.version == 1 {
		switch {
		case n.Kind == KindBool && n.Bool:
			return ModeInherit, nil
		case n.Kind == KindBool:
			return ModeNoInherit, nil
		case n.Kind == KindInt && n.Int == 0:
			return ModeReset, nil
		case isEnum:
			return ModeInvalid, invalid("INHERIT_ENUM_IN_GRAMMAR_1", n)
		}
		return ModeInvalid, invalid("INVALID_INHERIT", n)
	}
	if isEnum {
		return Mode(slices.Index(modeNames, n.Str)), nil
	}
	if isLegacy {
		return ModeInvalid, invalid("LEGACY_INHERIT_IN_GRAMMAR_2", n)
	}
	return ModeInvalid, invalid("INVALID_INHERIT", n)
}

// WriteMode is the inverse of ReadMode: a bijection per grammar version, so a round trip keeps the
// spelling. Only the three states can be written, under a bound supported grammar: an invalid or unknown
// Mode, or a Grammar that is not bound to a supported version, gives no Node and an error (the upstream
// code UNKNOWN_GRAMMAR_VERSION for the grammar; ErrUnsupported for a Mode that is not a state).
func WriteMode(m Mode, g Grammar) (*Node, error) {
	if !isSupported(g.version) {
		return nil, binding("UNKNOWN_GRAMMAR_VERSION")
	}
	if m < ModeInherit || m > ModeReset {
		return nil, unsupported("%d is not one of the three InheritanceMode states", m)
	}
	if g.version == 1 {
		switch m {
		case ModeInherit:
			return &Node{Kind: KindBool, Bool: true}, nil
		case ModeNoInherit:
			return &Node{Kind: KindBool}, nil
		}
		return &Node{Kind: KindInt}, nil
	}
	return &Node{Kind: KindStr, Str: modeNames[m]}, nil
}

// InheritSymbol is the access/modify symbol that means "no field-level rule".
const InheritSymbol = "inherit"

// Rule is the value of access or modify.
type Rule struct {
	// Symbol is true for the symbol inherit (no field-level rule); then Patterns is nil.
	Symbol bool
	// Patterns is the list of CertPatterns; the empty list is an explicit "nobody", never the symbol.
	Patterns []string
}

// patternSpace reports whether c ends the first character of a pattern value: the upstream pattern
// uses \s, which in Python is the Unicode White_Space property plus U+001C to U+001F.
func patternSpace(c rune) bool { return unicode.IsSpace(c) || (c >= 0x1c && c <= 0x1f) }

func isLetter(c rune) bool { return c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' }

func isAttr(a string) bool {
	for i, c := range a {
		if i == 0 && !isLetter(c) {
			return false
		}
		if !isLetter(c) && !(c >= '0' && c <= '9') && c != '.' && c != '-' {
			return false
		}
	}
	return a != ""
}

// IsCertPattern: attr=value pairs joined by "," with nothing between them. attr is
// [A-Za-z][A-Za-z0-9.-]*; a value is non-empty, does not start with "=", "," or a space, and holds no
// "=" or ",". A bare inherit is not a pattern, and neither is "OU=a, O=b".
func IsCertPattern(p string) bool {
	for _, pair := range strings.Split(p, ",") {
		attr, value, ok := strings.Cut(pair, "=")
		if !ok || !isAttr(attr) || strings.Contains(value, "=") {
			return false
		}
		first := []rune(value)
		if len(first) == 0 || patternSpace(first[0]) {
			return false
		}
	}
	return true
}

// ReadRule reads an access / modify value: a list of CertPatterns or the symbol inherit.
//
// [] is an explicit empty set (nobody), [inherit] is a list holding an invalid CertPattern
// (INVALID_CERT_PATTERN), and null, a bare pattern string, a boolean or a mapping is
// INVALID_ACCESS_VALUE. A violation is anchored at the value.
func ReadRule(n *Node) (Rule, *Violation) {
	switch {
	case n.Kind == KindSeq:
		patterns := []string{}
		for _, item := range n.Items {
			if item.Kind != KindStr || !IsCertPattern(item.Str) {
				return Rule{}, invalid("INVALID_CERT_PATTERN", n)
			}
			patterns = append(patterns, item.Str)
		}
		return Rule{Patterns: patterns}, nil
	case n.Kind == KindStr && n.Str == InheritSymbol:
		return Rule{Symbol: true}, nil
	}
	return Rule{}, invalid("INVALID_ACCESS_VALUE", n)
}
