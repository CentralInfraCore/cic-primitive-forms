package forms

import (
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"
)

// CanonicalBytes and CanonicalDigest (P5-1; ADR 0014, engine MATERIALIZATION-SPEC.md section A, A1-A7): the canonical bytes and the digest of a processed value.
//
// The form is the engine's, not a second one: canonical JSON without whitespace, object keys sorted by raw UTF-8 byte order, arrays in the order given,
// integers as decimal digits, the string escaping of Go's json.Marshal (the five characters <, >, &, U+2028, U+2029 escaped, \b \t \n \f \r short, any other
// control character below U+0020 as lowercase \u00xx), no Unicode normalization (the named gap of A4). The digest is "sha256:" and the lowercase hex of the
// SHA-256 of those bytes (A7).
//
// The range is what this library holds: null, boolean, signed 64-bit integer, string, sequence, map. A float or an integer beyond 64 bits (KindUnsupported) is
// refused, never approximated, and so is a map that repeats a key (the engine's Map cannot hold one) and a string that is not UTF-8 (a Rust string cannot be).
// The vectors of the engine outside this range are out of range here by name (docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md, decision G). The order of a set is not
// decided (decision H); a sequence is written as given.
//
// The error is a *CanonicalError; it is a refusal of this function, not a code of the registry of ADR 0007. The Rust peer is rust/src/canonical.rs.

// The reasons a value has no canonical bytes.
const (
	CanonicalUnsupportedValue = "unsupported_value"
	CanonicalDuplicateKey     = "duplicate_key"
	CanonicalNotUTF8          = "not_utf8"
)

// CanonicalError says why a value has no canonical bytes.
type CanonicalError struct{ Reason string }

func (e *CanonicalError) Error() string { return "no canonical bytes: " + e.Reason }

// CanonicalBytes returns the canonical bytes of n (A1-A6).
func CanonicalBytes(n *Node) ([]byte, error) {
	var b strings.Builder
	if err := writeValueBytes(&b, n); err != nil {
		return nil, err
	}
	return []byte(b.String()), nil
}

// CanonicalDigest returns "sha256:" and the lowercase hex of the SHA-256 of b (A7).
func CanonicalDigest(b []byte) string { return "sha256:" + SHA256Hex(b) }

func writeValueBytes(b *strings.Builder, n *Node) error {
	switch n.Kind {
	case KindNull:
		b.WriteString("null")
	case KindBool:
		b.WriteString(strconv.FormatBool(n.Bool))
	case KindInt:
		b.WriteString(strconv.FormatInt(n.Int, 10))
	case KindStr:
		return writeValueString(b, n.Str)
	case KindSeq:
		b.WriteByte('[')
		for i, item := range n.Items {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeValueBytes(b, item); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case KindMap:
		sorted := slices.Clone(n.Entries)
		slices.SortStableFunc(sorted, func(x, y Entry) int { return strings.Compare(x.Key, y.Key) })
		for i := 1; i < len(sorted); i++ {
			if sorted[i-1].Key == sorted[i].Key {
				return &CanonicalError{CanonicalDuplicateKey}
			}
		}
		b.WriteByte('{')
		for i, e := range sorted {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeValueString(b, e.Key); err != nil {
				return err
			}
			b.WriteByte(':')
			if err := writeValueBytes(b, e.Value); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	default:
		return &CanonicalError{CanonicalUnsupportedValue}
	}
	return nil
}

func writeValueString(b *strings.Builder, s string) error {
	if !utf8.ValidString(s) {
		return &CanonicalError{CanonicalNotUTF8}
	}
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\b':
			b.WriteString(`\b`)
		case '\t':
			b.WriteString(`\t`)
		case '\n':
			b.WriteString(`\n`)
		case '\f':
			b.WriteString(`\f`)
		case '\r':
			b.WriteString(`\r`)
		case '<', '>', '&', '\u2028', '\u2029':
			writeValueEscape(b, r)
		default:
			if r < 0x20 {
				writeValueEscape(b, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return nil
}

func writeValueEscape(b *strings.Builder, r rune) {
	const hexDigits = "0123456789abcdef"
	b.WriteString(`\u`)
	for shift := 12; shift >= 0; shift -= 4 {
		b.WriteByte(hexDigits[(r>>shift)&0xf])
	}
}
