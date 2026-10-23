package forms

import (
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// EditResult is a successful edit.
type EditResult struct {
	// Text is the edited text; for a no-op it is the input.
	Text string
	// Span is where the scalar is in Text.
	Span Span
	// Changed is false when the position already held the value: nothing is rewritten, not even to the canonical spelling.
	Changed bool
}

// EditScalar replaces the scalar at path in text by value and leaves every other byte alone (ADR 0012). value is a scalar Node (a string, an
// integer, a boolean or null; its positions are not read). A refusal of parse, validate or the path operations is returned as it is; the
// refusals of the edit phase come in the fixed order of ADR 0012, section 4, and no text is returned with an error.
//
// What a success guarantees (ADR 0012, section 3) is checked on every call: the bytes outside the span are the original's, the result
// parses to the old tree with that one scalar replaced and validates, and a position that already holds the value keeps its token.
func EditScalar(text string, root Shape, path string, a []string, value *Node) (EditResult, error) {
	doc, err := Parse(text)
	if err != nil {
		return EditResult{}, err
	}
	if err := refuseInvalid(doc, root, a); err != nil {
		return EditResult{}, err
	}
	sp, f, err := spanOf(text, doc, root, path, a)
	if err != nil {
		return EditResult{}, err
	}
	n := f.value
	if value == nil || !representable(value) {
		return EditResult{}, editFailure("EDIT_VALUE_NOT_REPRESENTABLE", n.Pos, path)
	}
	if !carries(f.shape, value) {
		return EditResult{}, editFailure("EDIT_TYPE_MISMATCH", n.Pos, path)
	}
	if n.Same(value) {
		return EditResult{Text: text, Span: sp}, nil
	}
	token, ok := tokenFor(sp.Style, value)
	if !ok {
		return EditResult{}, editFailure("EDIT_STYLE_CONFLICT", n.Pos, path)
	}
	out := text[:sp.Start] + token + text[sp.End:]
	if code := verifyEdit(text, sp, out, root, a, replaced(doc, sp.Start, value)); code != "" {
		return EditResult{}, editFailure(code, n.Pos, path)
	}
	return EditResult{Text: out, Span: Span{Start: sp.Start, End: sp.Start + len(token), Style: sp.Style}, Changed: true}, nil
}

// verifyEdit is the check of the guarantees on the text that was built, on every call: "" when they hold, otherwise the code of the refusal. The
// bytes outside the span are the original's, the result parses and validates (EDIT_INVALID_RESULT), and it is the expected tree (EDIT_SPAN_MISMATCH,
// an internal assertion: no input can make it fail on purpose, its proof is a direct test and the seeded defects of step E4).
func verifyEdit(text string, sp Span, out string, root Shape, a []string, want *Node) string {
	if !strings.HasPrefix(out, text[:sp.Start]) || !strings.HasSuffix(out, text[sp.End:]) {
		return "EDIT_SPAN_MISMATCH"
	}
	parsed, err := Parse(out)
	if err != nil || refuseInvalid(parsed, root, a) != nil {
		return "EDIT_INVALID_RESULT"
	}
	if !parsed.Same(want) {
		return "EDIT_SPAN_MISMATCH"
	}
	return ""
}

// replaced is a copy of the tree with the scalar that starts at byte offset replaced by value (a scalar is the only node that starts there).
func replaced(n *Node, offset int, value *Node) *Node {
	out := &Node{Kind: n.Kind, Bool: n.Bool, Int: n.Int, Str: n.Str, Pos: n.Pos}
	switch n.Kind {
	case KindMap:
		out.Entries = make([]Entry, len(n.Entries))
		for i, e := range n.Entries {
			out.Entries[i] = Entry{Key: e.Key, KeyPos: e.KeyPos, Value: replaced(e.Value, offset, value)}
		}
	case KindSeq:
		out.Items = make([]*Node, len(n.Items))
		for i, it := range n.Items {
			out.Items[i] = replaced(it, offset, value)
		}
	default:
		if n.Pos != nil && n.Pos.Offset == offset {
			out.Kind, out.Bool, out.Int, out.Str = value.Kind, value.Bool, value.Int, value.Str
		}
	}
	return out
}

// EditParsed is what an edit of a document that was supplied as a parsed value is: there is no text to point into (EDIT_NO_SOURCE_TEXT), after
// the document and the path have been accepted.
func EditParsed(doc *Node, root Shape, path string, a []string, value *Node) error {
	if err := refuseInvalid(doc, root, a); err != nil {
		return err
	}
	segs, perr := ParsePath(path)
	if perr != nil {
		return pathFailure(perr)
	}
	if _, perr := ResolvePath(root, segs); perr != nil {
		return pathFailure(perr)
	}
	return editFailure("EDIT_NO_SOURCE_TEXT", nil, path)
}

// refuseInvalid is the validate phase as an error: the violations, as they are. An expanded Access wrapper is read as its value (shortView).
func refuseInvalid(doc *Node, root Shape, a []string) error {
	v, err := Validate(shortView(doc, a), root, a)
	if err != nil {
		return err
	}
	if len(v.Violations) > 0 {
		return &Error{Kind: ErrContract, Violations: v.Violations}
	}
	return nil
}

// representable says whether the value can be written at all: a string without a line break, a control character or invalid UTF-8 (reading is wider
// than writing: an existing token with an escaped control character is read and can be replaced, a new value never holds one).
func representable(v *Node) bool {
	return v.Kind != KindStr || (utf8.ValidString(v.Str) && !strings.ContainsFunc(v.Str, unwritable))
}

func unwritable(r rune) bool {
	return unicode.IsControl(r) || r == 0x2028 || r == 0x2029
}

// carries says whether the position can hold the value: its kind is the scalar type of the shape, or null where the shape is nullable.
func carries(shape *Shape, v *Node) bool {
	if shape == nil {
		return false
	}
	switch v.Kind {
	case KindNull:
		return shape.Nullable
	case KindStr:
		return shape.Scalar == ScalarString
	case KindInt:
		return shape.Scalar == ScalarInteger
	case KindBool:
		return shape.Scalar == ScalarBoolean
	}
	return false
}

// tokenFor is the canonical spelling of the value in the style of the scalar; ok is false when the style cannot carry it (the edit never changes a
// style).
func tokenFor(style SpanStyle, v *Node) (token string, ok bool) {
	switch style {
	case SpanSingleQuoted:
		return "'" + strings.ReplaceAll(v.Str, "'", "''") + "'", v.Kind == KindStr
	case SpanDoubleQuoted:
		return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(v.Str) + `"`, v.Kind == KindStr
	}
	switch v.Kind {
	case KindInt:
		return strconv.FormatInt(v.Int, 10), true
	case KindBool:
		return strconv.FormatBool(v.Bool), true
	case KindNull:
		return "null", true
	}
	return v.Str, editPlainSafe(v.Str)
}

// editPlainSafe says whether the string, written as a plain scalar, reads back as the same string: it does not start with an indicator (the one
// thing the parser alone would let through, as in "-a"), and the parser reads it back as that very string. The round trip is what rules out the
// rest of the closed rule of the design note: an empty string, a leading or trailing space, ": " or " #" inside, a final colon, and everything the
// parser reads as another kind (true, 123, null, ~, 1.5).
func editPlainSafe(s string) bool {
	if s == "" || strings.ContainsAny(s[:1], "-?:,[]{}#&*!|>'\"%@`") {
		return false
	}
	back, err := Parse(s)
	return err == nil && back.Same(&Node{Kind: KindStr, Str: s})
}

// shortView is the document as the validator reads it: an expanded Access wrapper is replaced by its value. The other members of a wrapper
// are the recognition's, and an edit of the value cannot change them. Positions are kept.
func shortView(n *Node, a []string) *Node {
	switch n.Kind {
	case KindMap:
		if class, viol := classify(n, a); viol == nil && class == ClassExpanded { // (the key set of an expanded wrapper holds `value`)
			return shortView(lookup(n, "value"), a)
		}
		out := &Node{Kind: KindMap, Pos: n.Pos, Entries: make([]Entry, len(n.Entries))}
		for i, e := range n.Entries {
			out.Entries[i] = Entry{Key: e.Key, KeyPos: e.KeyPos, Value: shortView(e.Value, a)}
		}
		return out
	case KindSeq:
		out := &Node{Kind: KindSeq, Pos: n.Pos, Items: make([]*Node, len(n.Items))}
		for i, it := range n.Items {
			out.Items[i] = shortView(it, a)
		}
		return out
	}
	return n
}
