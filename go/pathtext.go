package forms

// The text of logical paths (ADR 0006): strict parse, canonical format and resolution against a schema.
//
// ParsePath accepts only the canonical writing, so FormatPath(ParsePath(t)) == t for every accepted t: a text with a valid
// reading that is not canonical (a leading zero, +7, -0, a needless \u escape, \/) is refused, not rewritten, because two
// spellings of one path would defeat byte-identical diffs. Offsets are in code points. The three operations report a
// *PathError: a code of the registry, its phase (path_parse, path_format, path_resolve), the offset (path_parse only) and,
// for path_resolve, the logical path up to and including the segment at fault. They have no source location.

import (
	"fmt"
	"slices"
	"strconv"
	"strings"
)

// PathSeg is a segment of a path: a field, or a ListItem (the key components of one list element).
type PathSeg struct {
	Field  string
	Item   []KeyComp
	IsItem bool
}

// PathError is the failure of a path operation. LogicalPath is set by ResolvePath only.
type PathError struct {
	Code        string
	Phase       Phase
	Offset      int
	LogicalPath string
}

func (e *PathError) Error() string { return fmt.Sprintf("%s (%s)", e.Code, e.Phase) }

// Violation is the machine-response form of the error: no source location applies.
func (e *PathError) Violation() Violation {
	v := Violation{Code: e.Code, Phase: e.Phase, Location: Location{NotApplicable: true}, LogicalPath: e.LogicalPath}
	if e.Phase == PhasePathParse {
		offset := e.Offset
		v.PathOffset = &offset
	}
	return v
}

const escapable = "\\.[]{}=,"

type pathParser struct{ r []rune }

func (p *pathParser) fail(code string, at int) *PathError {
	return &PathError{Code: code, Phase: PhasePathParse, Offset: at}
}

func isDigit(c rune) bool { return c >= '0' && c <= '9' }

// ParsePath reads the canonical text of a path into segments.
func ParsePath(text string) ([]PathSeg, *PathError) {
	p := &pathParser{r: []rune(text)}
	if len(p.r) == 0 || p.r[0] != '$' {
		return nil, p.fail("PATH_SYNTAX", 0)
	}
	var segs []PathSeg
	afterName := false // a selector may only directly follow a field name
	for i := 1; i < len(p.r); {
		switch p.r[i] {
		case '.':
			name, next, err := p.name(i+1, false)
			if err != nil {
				return nil, err
			}
			segs, i, afterName = append(segs, PathSeg{Field: name}), next, true
		case '[':
			if !afterName {
				return nil, p.fail("PATH_SYNTAX", i)
			}
			item, next, err := p.selector(i)
			if err != nil {
				return nil, err
			}
			segs, i, afterName = append(segs, PathSeg{Item: item, IsItem: true}), next, false
		default:
			return nil, p.fail("PATH_SYNTAX", i)
		}
	}
	return segs, nil
}

// name reads a field name, or the name of a selector component; the first character that ends it is not consumed.
func (p *pathParser) name(start int, inSelector bool) (string, int, *PathError) {
	var b strings.Builder
	i := start
loop:
	for i < len(p.r) {
		switch c := p.r[i]; {
		case c == '\\':
			if i+1 >= len(p.r) || !strings.ContainsRune(escapable, p.r[i+1]) {
				return "", 0, p.fail("PATH_BAD_ESCAPE", i)
			}
			b.WriteRune(p.r[i+1])
			i += 2
		case c < 0x20 || c == 0x7f:
			return "", 0, p.fail("PATH_SYNTAX", i)
		case !inSelector && (c == '.' || c == '['), inSelector && c == '=':
			break loop
		case strings.ContainsRune(escapable, c):
			return "", 0, p.fail("PATH_SYNTAX", i)
		default:
			b.WriteRune(c)
			i++
		}
	}
	if b.Len() == 0 {
		return "", 0, p.fail("PATH_EMPTY_NAME", start)
	}
	return b.String(), i, nil
}

func (p *pathParser) selector(start int) ([]KeyComp, int, *PathError) {
	i := start + 1
	if i >= len(p.r) {
		return nil, 0, p.fail("PATH_SYNTAX", i)
	}
	if isDigit(p.r[i]) || (p.r[i] == '-' && i+1 < len(p.r) && isDigit(p.r[i+1])) {
		return nil, 0, p.fail("PATH_INDEX_SELECTOR", start)
	}
	if p.r[i] != '{' {
		return nil, 0, p.fail("PATH_SYNTAX", i)
	}
	i++
	var comps []KeyComp
	for {
		nameStart := i
		name, next, err := p.name(i, true)
		if err != nil {
			return nil, 0, err
		}
		if slices.ContainsFunc(comps, func(c KeyComp) bool { return c.Name == name }) {
			return nil, 0, p.fail("PATH_DUPLICATE_COMPONENT", nameStart)
		}
		i = next
		if i >= len(p.r) || p.r[i] != '=' {
			return nil, 0, p.fail("PATH_SYNTAX", i)
		}
		i++
		if i >= len(p.r) {
			return nil, 0, p.fail("PATH_SYNTAX", i)
		}
		comp := KeyComp{Name: name}
		if p.r[i] == '"' {
			comp.Str, i, err = p.str(i)
		} else {
			// anything else is read as an integer, which refuses what is not one
			comp.IsInt = true
			comp.Int, i, err = p.integer(i)
		}
		if err != nil {
			return nil, 0, err
		}
		comps = append(comps, comp)
		if i >= len(p.r) {
			return nil, 0, p.fail("PATH_SYNTAX", i)
		}
		if p.r[i] == ',' {
			i++
			continue
		}
		if p.r[i] != '}' {
			return nil, 0, p.fail("PATH_SYNTAX", i)
		}
		i++
		break
	}
	if i >= len(p.r) || p.r[i] != ']' {
		return nil, 0, p.fail("PATH_SYNTAX", i)
	}
	return comps, i + 1, nil
}

func (p *pathParser) integer(start int) (int64, int, *PathError) {
	i := start
	plus, neg := p.r[i] == '+', p.r[i] == '-'
	if plus || neg {
		i++
	}
	ds := i
	for i < len(p.r) && isDigit(p.r[i]) {
		i++
	}
	if i == ds {
		return 0, 0, p.fail("PATH_SYNTAX", i)
	}
	digits := string(p.r[ds:i])
	switch {
	case plus, len(digits) > 1 && digits[0] == '0', neg && digits == "0":
		return 0, 0, p.fail("PATH_NONCANONICAL", start)
	}
	text := digits
	if neg {
		text = "-" + digits
	}
	n, err := strconv.ParseInt(text, 10, 64)
	if err != nil {
		return 0, 0, p.fail("PATH_INTEGER_RANGE", start)
	}
	return n, i, nil
}

// str reads a canonical double-quoted JSON string: \" \\ \b \f \n \r \t, \u00xx in lower-case hex for the other control
// characters, everything else literal.
func (p *pathParser) str(start int) (string, int, *PathError) {
	var b strings.Builder
	for i := start + 1; i < len(p.r); {
		c := p.r[i]
		switch {
		case c == '"':
			return b.String(), i + 1, nil
		case c < 0x20:
			return "", 0, p.fail("PATH_NONCANONICAL", i)
		case c != '\\':
			b.WriteRune(c)
			i++
			continue
		}
		if i+1 >= len(p.r) {
			return "", 0, p.fail("PATH_BAD_ESCAPE", i)
		}
		switch e := p.r[i+1]; e {
		case '"', '\\':
			b.WriteRune(e)
			i += 2
		case 'b', 'f', 'n', 'r', 't':
			b.WriteRune(map[rune]rune{'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}[e])
			i += 2
		case '/':
			return "", 0, p.fail("PATH_NONCANONICAL", i)
		case 'u':
			if i+6 > len(p.r) {
				return "", 0, p.fail("PATH_BAD_ESCAPE", i)
			}
			hex := string(p.r[i+2 : i+6])
			v, err := strconv.ParseUint(hex, 16, 32)
			if err != nil || strings.ContainsAny(hex, "+-_ ") {
				return "", 0, p.fail("PATH_BAD_ESCAPE", i)
			}
			if hex != strings.ToLower(hex) || v >= 0x20 || slices.Contains([]uint64{8, 9, 10, 12, 13}, v) {
				return "", 0, p.fail("PATH_NONCANONICAL", i)
			}
			b.WriteRune(rune(v))
			i += 6
		default:
			return "", 0, p.fail("PATH_BAD_ESCAPE", i)
		}
	}
	return "", 0, p.fail("PATH_SYNTAX", len(p.r))
}

// FormatPath is the canonical text of segments, or the error of the one that cannot be written: an empty field or component
// name (PATH_EMPTY_NAME), a control character in a name (PATH_SYNTAX), a key value with no encoding (PATH_KEY_UNSUPPORTED,
// PATH_INTEGER_RANGE).
func FormatPath(segs []PathSeg) (string, *PathError) {
	fail := func(code string) (string, *PathError) { return "", &PathError{Code: code, Phase: PhasePathFormat} }
	var b strings.Builder
	b.WriteByte('$')
	check := func(name string) string {
		switch {
		case name == "":
			return "PATH_EMPTY_NAME"
		case strings.IndexFunc(name, func(c rune) bool { return c <= 0x1f || c == 0x7f }) >= 0:
			return "PATH_SYNTAX"
		}
		return ""
	}
	for _, s := range segs {
		if !s.IsItem {
			if code := check(s.Field); code != "" {
				return fail(code)
			}
			b.WriteByte('.')
			writeName(&b, s.Field)
			continue
		}
		if len(s.Item) == 0 {
			return fail("PATH_SYNTAX")
		}
		for _, c := range s.Item {
			if code := check(c.Name); code != "" {
				return fail(code)
			}
			switch c.Bad {
			case "boolean":
				return fail("PATH_KEY_UNSUPPORTED")
			case "range":
				return fail("PATH_INTEGER_RANGE")
			}
		}
		if !writeSelector(&b, s.Item) {
			return fail("PATH_SYNTAX")
		}
	}
	return b.String(), nil
}

// Resolved says what a path addresses in a schema: Target is root, scalar, composite, list, set, opaque or item, and
// Primitive whether it is a primitive position (a field is; the root and a list element are not, ADR 0002).
type Resolved struct {
	Target    string
	Primitive bool
}

// ResolvePath resolves segments against root (a composite) by the identity of the schema (ADR 0006 "Resolution against
// the schema"): the key of the list, or of the item_choice case the component names select. The error carries the logical
// path up to and including the segment at fault.
func ResolvePath(root Shape, segs []PathSeg) (Resolved, *PathError) {
	cur, target := root, "root"
	fields := root.Fields
	var upTo []PathSeg
	fail := func(code string) (Resolved, *PathError) {
		text, _ := FormatPath(upTo)
		return Resolved{}, &PathError{Code: code, Phase: PhasePathResolve, LogicalPath: text}
	}
	for _, s := range segs {
		upTo = append(upTo, s)
		if !s.IsItem {
			switch cur.Kind {
			case ShapeScalar, ShapeSet, ShapeOpaque:
				return fail("PATH_NO_CHILDREN")
			case ShapeDeclaredList, ShapeList:
				return fail("PATH_LIST_NOT_SELECTED")
			}
			i := slices.IndexFunc(fields, func(f NamedShape) bool { return f.Name == s.Field })
			if i < 0 {
				return fail("UNKNOWN_FIELD")
			}
			cur = fields[i].Shape
			target = targetOf(cur)
			fields = cur.Fields
			continue
		}
		switch cur.Kind {
		case ShapeSet:
			return fail("PATH_SET_ELEMENT")
		case ShapeDeclaredList:
		default:
			return fail("PATH_NOT_A_LIST")
		}
		var c *Case
		for i := range cur.Elements.Cases {
			names := make([]string, len(s.Item))
			for j, k := range s.Item {
				names[j] = k.Name
			}
			if slices.Equal(names, cur.Elements.Cases[i].Key) {
				c = &cur.Elements.Cases[i]
			}
		}
		if c == nil {
			return fail("PATH_SELECTOR_MISMATCH")
		}
		for _, k := range s.Item {
			f, _ := fieldOf(c, k.Name)
			if f.Kind != ShapeScalar || f.Scalar == ScalarBoolean {
				return fail("PATH_KEY_UNSUPPORTED")
			}
			if k.IsInt != (f.Scalar == ScalarInteger) {
				return fail("PATH_KEY_TYPE")
			}
		}
		cur, target, fields = Shape{Kind: ShapeComposite, Fields: c.Fields}, "item", c.Fields
	}
	return Resolved{Target: target, Primitive: target != "root" && target != "item"}, nil
}

func targetOf(s Shape) string {
	switch s.Kind {
	case ShapeScalar:
		return "scalar"
	case ShapeComposite:
		return "composite"
	case ShapeSet:
		return "set"
	case ShapeOpaque:
		return "opaque"
	}
	return "list"
}
