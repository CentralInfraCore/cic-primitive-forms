package forms

import (
	"cmp"
	"fmt"
	"slices"
	"strconv"
	"strings"
)

// PositionKind is the shape of a primitive position of the synthetic schema model.
type PositionKind int

// The position kinds.
const (
	// PosScalar is a scalar value.
	PosScalar PositionKind = iota
	// PosOpaque is any value; the content of an expanded node's value is not re-recognized.
	PosOpaque
	// PosStructured is a mapping with declared Fields (not recognized further).
	PosStructured
	// PosListOf is a list of records; each record field is a position of its own.
	PosListOf
)

// RecordField is one field of a list record.
type RecordField struct {
	Name     string
	Position Position
}

// Position is a primitive position.
type Position struct {
	Kind   PositionKind
	Fields []string      // PosStructured
	Record []RecordField // PosListOf
}

// Class is the recognition class of a node.
type Class string

// The classes. ClassError is a failed recognition (EXPANDED_WITH_EXTRA_KEY).
const (
	ClassShort    Class = "short"
	ClassExpanded Class = "expanded"
	ClassError    Class = "error"
)

// Outcome is the result of recognizing and validating one document at one position.
type Outcome struct {
	// Class is the class of the node at the top position.
	Class Class
	// Elements is, for a list position, the class of each field of each element.
	Elements []map[string]Class
	// OpaqueValue is set for an expanded node at an opaque position: its value stays opaque.
	OpaqueValue bool
	// Violations is the machine response; empty means the document is valid at the position.
	Violations []Violation
}

type seg struct {
	field string
	index int
	isIdx bool
	// item is a ListItem segment (ADR 0006): the key components of one list element, in the declared key
	// order. It follows the field segment that names the list.
	item   []KeyComp
	isItem bool
}

type found struct {
	path []seg
	v    Violation
}

// key orders a segment: every field sorts before every list index, fields by the UTF-8 bytes of
// the name, indexes numerically.
func (s seg) key() string {
	if s.isItem {
		// ADR 0007: ListItem segments compare by their components in the declared key order, strings by
		// UTF-8 bytes and integers numerically. Each name and string value is written order-preservingly
		// (NUL escaped, then a terminator that sorts below every continuation), so that concatenating the
		// components does not change how two segments compare.
		var b strings.Builder
		b.WriteString("\x02")
		for _, c := range s.item {
			b.WriteString(orderBytes(c.Name))
			if c.IsInt {
				b.WriteString(fmt.Sprintf("1%020d", uint64(c.Int)^(1<<63)))
			} else {
				b.WriteString("0" + orderBytes(c.Str))
			}
		}
		return b.String()
	}
	if s.isIdx {
		return fmt.Sprintf("\x01%020d", s.index)
	}
	return "\x00" + s.field
}

// orderBytes writes a string so that comparing two written strings bytewise gives the order of the strings.
func orderBytes(s string) string {
	return strings.ReplaceAll(s, "\x00", "\x00\xff") + "\x00\x01"
}

func (f found) keys() []string {
	out := make([]string, len(f.path))
	for i, s := range f.path {
		out[i] = s.key()
	}
	return out
}

func loc(p *Pos) Location {
	if p == nil {
		return Location{}
	}
	return Location{Available: true, Pos: *p}
}

// logicalPath is the canonical ADR 0006 text of a path made of field names; "" where ADR 0007
// leaves the path out. That is the root alone (a single position with no structure around it),
// a list element (the synthetic schema declares no identity, so the item cannot be named) and
// a field name that cannot be written (empty, or holding a control character).
func logicalPath(path []seg) string {
	if len(path) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteByte('$')
	for _, s := range path {
		if s.isItem {
			if !writeSelector(&b, s.item) {
				return ""
			}
			continue
		}
		if s.isIdx || s.field == "" {
			return ""
		}
		b.WriteByte('.')
		if !writeName(&b, s.field) {
			return ""
		}
	}
	return b.String()
}

// writeName writes a field or component name with the ADR 0006 escape set; false for a name that
// cannot be written (a control character).
func writeName(b *strings.Builder, name string) bool {
	for _, c := range name {
		if c <= 0x1f || c == 0x7f {
			return false
		}
		if strings.ContainsRune("\\.[]{}=,", c) {
			b.WriteByte('\\')
		}
		b.WriteRune(c)
	}
	return true
}

// writeSelector writes `[{name="value",n=1}]`: strings as canonical double-quoted JSON, integers bare.
func writeSelector(b *strings.Builder, item []KeyComp) bool {
	if len(item) == 0 {
		return false
	}
	b.WriteString("[{")
	for i, c := range item {
		if i > 0 {
			b.WriteByte(',')
		}
		if c.Name == "" || !writeName(b, c.Name) {
			return false
		}
		b.WriteByte('=')
		if c.IsInt {
			b.WriteString(strconv.FormatInt(c.Int, 10))
		} else {
			writeJSONString(b, c.Str)
		}
	}
	b.WriteString("}]")
	return true
}

// writeJSONString is the one canonical writing of ADR 0006: \" and \\, the short escapes \b \f \n \r \t,
// \u00xx (lower-case hex) for the other control characters, everything else literal UTF-8.
func writeJSONString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, c := range s {
		switch {
		case c == '"':
			b.WriteString(`\"`)
		case c == '\\':
			b.WriteString(`\\`)
		case c == '\b':
			b.WriteString(`\b`)
		case c == '\f':
			b.WriteString(`\f`)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c == '\t':
			b.WriteString(`\t`)
		case c < 0x20:
			b.WriteString(fmt.Sprintf(`\u%04x`, c))
		default:
			b.WriteRune(c)
		}
	}
	b.WriteByte('"')
}

// sorted orders by logical path, then code (ADR 0007) and fills in each LogicalPath.
//
// (logical_path, code) is unique in a response. Every violation that has a path is distinct by
// construction (a field occurs once); the ones without a path (list elements, unwritable
// names) cannot be told apart in the response, so only the first of each code, in path order,
// is kept.
func sorted(fs []found) []Violation {
	slices.SortStableFunc(fs, func(a, b found) int {
		return cmp.Or(slices.Compare(a.keys(), b.keys()), strings.Compare(a.v.Code, b.v.Code))
	})
	out := []Violation{}
	pathless := map[string]bool{}
	for _, f := range fs {
		v := f.v
		v.LogicalPath = logicalPath(f.path)
		if v.LogicalPath == "" {
			if pathless[v.Code] {
				continue
			}
			pathless[v.Code] = true
		}
		out = append(out, v)
	}
	return out
}

func hasKey(n *Node, key string) bool {
	for _, e := range n.Entries {
		if e.Key == key {
			return true
		}
	}
	return false
}

// classify applies ADR 0001 to one node: a violation for a mapping that holds all of a
// plus another key.
func classify(n *Node, a []string) (Class, *Violation) {
	if n.Kind != KindMap {
		return ClassShort, nil
	}
	for _, k := range a {
		if !hasKey(n, k) {
			return ClassShort, nil
		}
	}
	if len(n.Entries) == len(a) {
		return ClassExpanded, nil
	}
	return ClassError, &Violation{Code: "EXPANDED_WITH_EXTRA_KEY", Phase: PhaseRecognize, Location: loc(n.Pos)}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// validateShort validates a Short node at a position.
func validateShort(n *Node, p Position, path []seg, out *[]found) error {
	switch p.Kind {
	case PosScalar:
		if n.Kind == KindMap {
			*out = append(*out, found{path, Violation{Code: "MAP_AT_SCALAR_POSITION", Phase: PhaseValidate, Location: loc(n.Pos)}})
		}
	case PosStructured:
		if n.Kind != KindMap {
			return unsupported("a non-mapping at a structured position")
		}
		for _, e := range n.Entries {
			if contains(p.Fields, e.Key) {
				continue
			}
			ep := append(append([]seg{}, path...), seg{field: e.Key})
			*out = append(*out, found{ep, Violation{Code: "UNKNOWN_FIELD", Phase: PhaseValidate, Location: loc(e.KeyPos)}})
		}
	case PosListOf:
		return unsupported("a list position inside a record")
	}
	return nil
}

// Recognize recognizes n at position p and validates what recognition leaves Short. a is
// the expanded key set A. The error, when not nil, is an *Error of kind ErrUnsupported
// for a shape outside the subset: a scalar where a mapping or a list is declared.
func Recognize(n *Node, p Position, a []string) (*Outcome, error) {
	if p.Kind == PosListOf {
		return recognizeList(n, p.Record, a)
	}
	class, v := classify(n, a)
	switch class {
	case ClassError:
		return &Outcome{Class: ClassError, Violations: []Violation{*v}}, nil
	case ClassExpanded:
		return &Outcome{Class: ClassExpanded, OpaqueValue: p.Kind == PosOpaque, Violations: []Violation{}}, nil
	}
	var fs []found
	if err := validateShort(n, p, nil, &fs); err != nil {
		return nil, err
	}
	return &Outcome{Class: ClassShort, Violations: sorted(fs)}, nil
}

func recordPosition(record []RecordField, name string) (Position, bool) {
	for _, f := range record {
		if f.Name == name {
			return f.Position, true
		}
	}
	return Position{}, false
}

func recognizeList(n *Node, record []RecordField, a []string) (*Outcome, error) {
	if n.Kind != KindSeq {
		return nil, unsupported("a non-list at a list position")
	}
	elements := make([]map[string]Class, 0, len(n.Items))
	var recognition, validation []found
	for i, item := range n.Items {
		if item.Kind != KindMap {
			return nil, unsupported("a non-record list element")
		}
		classes := map[string]Class{}
		for _, e := range item.Entries {
			path := []seg{{index: i, isIdx: true}, {field: e.Key}}
			fp, ok := recordPosition(record, e.Key)
			if !ok {
				validation = append(validation, found{path, Violation{Code: "UNKNOWN_FIELD", Phase: PhaseValidate, Location: loc(e.KeyPos)}})
				continue
			}
			class, v := classify(e.Value, a)
			if v != nil {
				recognition = append(recognition, found{path, *v})
			} else if class == ClassShort {
				if err := validateShort(e.Value, fp, path, &validation); err != nil {
					return nil, err
				}
			}
			classes[e.Key] = class
		}
		elements = append(elements, classes)
	}
	if len(recognition) > 0 {
		return &Outcome{Class: ClassError, Elements: elements, Violations: sorted(recognition)}, nil
	}
	return &Outcome{Class: ClassShort, Elements: elements, Violations: sorted(validation)}, nil
}
