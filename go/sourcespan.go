package forms

import (
	"slices"
	"strings"
)

// SourceSpan (P3b, step E2; ADR 0012 and docs/P3B-SOURCE-PRESERVING-EDIT.md): where, in the original text, the scalar of one position is.
// Read only: it parses the text, finds the position by its logical path, and returns the half-open byte span of the scalar and its style.
// It changes nothing; Node and every P1-P3 result are as they were. The Rust peer is rust/src/sourcespan.rs.
//
// The extent of a scalar is found by a small scanner over the supported subset and verified by reading it back: the span, parsed alone as a
// document, must give the value the parser gave for that node. A scanner defect therefore cannot silently point at the wrong bytes; at worst
// it refuses.
//
// Not in this step: Validate of the document (P1 Validate does not read an expanded document, and the edit of step E3 validates its result),
// and the edit itself.

// SpanStyle is the style of a scalar's text: the ones this unit can replace.
type SpanStyle string

// The styles of SpanStyle.
const (
	SpanPlain        SpanStyle = "plain"
	SpanSingleQuoted SpanStyle = "single_quoted"
	SpanDoubleQuoted SpanStyle = "double_quoted"
)

// Span is the scalar's own text: bytes [Start, End) of the original UTF-8 input, a leading BOM included (the offset of ADR 0007).
type Span struct {
	Start, End int
	Style      SpanStyle
}

func editFailure(code string, at *Pos, path string) *Error {
	loc := Location{}
	if at != nil {
		loc = Location{Available: true, Pos: *at}
	}
	return &Error{Kind: ErrContract, Violations: []Violation{{Code: code, Phase: PhaseEdit, Location: loc, LogicalPath: path}}}
}

func pathFailure(e *PathError) *Error {
	v := Violation{Code: e.Code, Phase: e.Phase, Location: Location{NotApplicable: true}, LogicalPath: e.LogicalPath}
	if e.Phase == PhasePathParse {
		off := e.Offset
		v.PathOffset = &off
	}
	return &Error{Kind: ErrContract, Violations: []Violation{v}}
}

// throughWrapper is the node whose value is meant: through an expanded Access wrapper to its value, otherwise the node itself. wrapped says
// that it was a wrapper.
func throughWrapper(n *Node, a []string) (v *Node, wrapped bool) {
	if class, viol := classify(n, a); viol == nil && class == ClassExpanded {
		return lookup(n, "value"), true
	}
	return n, false
}

// spanFound is what the walk along the path found: the value node of the position (nil if the document spells none), the node an "absent" error is
// anchored at (the enclosing structure), and the mapping that directly holds the scalar (to tell a flow context).
type spanFound struct {
	value, parent, container *Node
}

func locate(doc *Node, root Shape, segs []PathSeg, a []string) spanFound {
	var fields []NamedShape
	if root.Kind == ShapeComposite {
		fields = root.Fields
	}
	var elements *Elements
	f := spanFound{value: doc, parent: doc, container: doc}
	for _, s := range segs {
		here := f.value
		if here == nil {
			break
		}
		f.parent = here
		if !s.IsItem {
			var shape *Shape
			for i := range fields {
				if fields[i].Name == s.Field {
					shape = &fields[i].Shape
					break
				}
			}
			f.container = here
			position := lookup(here, s.Field)
			var wrapped bool
			f.value = nil
			if position != nil {
				f.value, wrapped = throughWrapper(position, a)
			}
			if wrapped {
				f.container = position
			}
			if shape != nil {
				switch shape.Kind {
				case ShapeComposite:
					fields = shape.Fields
				case ShapeDeclaredList:
					elements = shape.Elements
				}
			}
			continue
		}
		f.container = here
		f.value = nil
		if here.Kind != KindSeq || elements == nil {
			continue
		}
		for _, rec := range here.Items {
			c, id, code, _ := identify(rec, elements, a)
			if code == "" && slices.Equal(id, s.Item) {
				f.value, fields = rec, c.Fields
				break
			}
		}
	}
	return f
}

// scanSpan scans the text of the scalar that starts at start; code is the code of the refusal, "" when there is none.
func scanSpan(text string, start int) (end int, style SpanStyle, code string) {
	at := func(i int) byte {
		if i < len(text) {
			return text[i]
		}
		return 0
	}
	switch at(start) {
	case '|', '>':
		return 0, "", "EDIT_BLOCK_SCALAR"
	case '\'':
		for i := start + 1; ; {
			switch c := at(i); {
			case c == '\'' && at(i+1) == '\'':
				i += 2
			case c == '\'':
				return i + 1, SpanSingleQuoted, ""
			case c == '\n' || c == '\r' || i >= len(text):
				return 0, "", "EDIT_MULTILINE_SCALAR"
			default:
				i++
			}
		}
	case '"':
		for i := start + 1; ; {
			switch c := at(i); {
			// a backslash escapes the next byte; one at the very end leaves nothing, and the next turn finds the end of the text
			case c == '\\' && (at(i+1) == '\n' || at(i+1) == '\r'):
				return 0, "", "EDIT_MULTILINE_SCALAR"
			case c == '\\':
				i += 2
			case c == '"':
				return i + 1, SpanDoubleQuoted, ""
			case c == '\n' || c == '\r' || i >= len(text):
				return 0, "", "EDIT_MULTILINE_SCALAR"
			default:
				i++
			}
		}
	}
	// a plain scalar ends at the end of its line, before a comment (a # that follows a space; the parser refuses a tab anywhere) and before the
	// spaces in front of it
	line := text[start:]
	if i := strings.IndexAny(line, "\r\n"); i >= 0 {
		line = line[:i]
	}
	if i := strings.Index(line, " #"); i >= 0 {
		line = line[:i]
	}
	return start + len(strings.TrimRight(line, " ")), SpanPlain, ""
}

// readBack is the check that the span is the scalar: the span, parsed alone as a document, must be the value the parser gave for the node.
// code is the refusal: a plain scalar whose value is longer than its first line is multi-line, anything else that disagrees is a defect of the
// scanner.
func readBack(token string, n *Node, style SpanStyle) (code string) {
	if parsed, err := Parse(token); err == nil && parsed.Same(n) {
		return ""
	}
	if style == SpanPlain && n.Kind == KindStr && len(n.Str) > len(token) && strings.HasPrefix(n.Str, token) {
		return "EDIT_MULTILINE_SCALAR"
	}
	return "EDIT_SPAN_MISMATCH"
}

// SourceSpan is the span of the scalar that holds the value of the position path in text, read against root (a composite).
//
// The refusal of an earlier phase is returned as it is (parse, path_parse, path_resolve); otherwise an *Error of phase edit with one of
// EDIT_NOT_A_SCALAR, EDIT_NO_SOURCE_VALUE, EDIT_BLOCK_SCALAR, EDIT_FLOW_CONTEXT, EDIT_MULTILINE_SCALAR, or EDIT_SPAN_MISMATCH (an internal
// assertion that failed: a defect, reported and not repaired).
func SourceSpan(text string, root Shape, path string, a []string) (Span, error) {
	doc, err := Parse(text)
	if err != nil {
		return Span{}, err
	}
	segs, perr := ParsePath(path)
	if perr != nil {
		return Span{}, pathFailure(perr)
	}
	resolved, perr := ResolvePath(root, segs)
	if perr != nil {
		return Span{}, pathFailure(perr)
	}
	canonical := path // the path text is strict (ADR 0006): what parsed is canonical, and it is the logical path of the position
	f := locate(doc, root, segs, a)
	if resolved.Target != "scalar" {
		at := f.parent.Pos
		if f.value != nil && f.value.Pos != nil {
			at = f.value.Pos
		}
		return Span{}, editFailure("EDIT_NOT_A_SCALAR", at, canonical)
	}
	n := f.value
	if n == nil || n.Pos == nil {
		return Span{}, editFailure("EDIT_NO_SOURCE_VALUE", f.parent.Pos, canonical)
	}
	if n.Kind == KindMap || n.Kind == KindSeq {
		return Span{}, editFailure("EDIT_NOT_A_SCALAR", n.Pos, canonical)
	}
	if p := f.container.Pos; p != nil && p.Offset < len(text) && (text[p.Offset] == '{' || text[p.Offset] == '[') {
		return Span{}, editFailure("EDIT_FLOW_CONTEXT", n.Pos, canonical)
	}
	start := n.Pos.Offset
	end, style, code := scanSpan(text, start)
	if code != "" {
		return Span{}, editFailure(code, n.Pos, canonical)
	}
	if code := readBack(text[start:end], n, style); code != "" {
		return Span{}, editFailure(code, n.Pos, canonical)
	}
	return Span{Start: start, End: end, Style: style}, nil
}
