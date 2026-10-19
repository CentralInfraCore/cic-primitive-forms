package forms

import (
	"errors"
	"io"
	"strconv"
	"strings"
	"unicode/utf8"

	"gopkg.in/yaml.v3"
)

// Limits are resource limits. They are an implementation boundary, not a contract value.
type Limits struct {
	MaxBytes, MaxDepth, MaxNodes int
}

// DefaultLimits is what Parse uses.
func DefaultLimits() Limits {
	return Limits{MaxBytes: 1 << 20, MaxDepth: 64, MaxNodes: 100_000}
}

// Parse reads one YAML document with the default Limits. See ParseWith.
func Parse(text string) (*Node, error) {
	return ParseWith(text, DefaultLimits())
}

// ParseWith reads one YAML document into a Node tree with source positions.
//
// The supported subset is deliberately narrow and everything outside it is an explicit
// Unsupported error, never a silent reinterpretation: one document; no anchors, aliases,
// tags or merge keys; mapping keys are scalars (a key is the scalar's text); plain scalars
// resolve as null/~/empty, true/false (the three YAML 1.2 spellings of case) and decimal
// integers that fit int64; quoted and block scalars are strings. A plain scalar that looks
// like a float, a hex/octal number, an integer with a leading zero or an integer beyond
// int64 is unsupported rather than guessed. The characters U+0085, U+2028 and U+2029 are line
// breaks to some YAML readers and not to others, so a text holding one is refused rather than
// read two ways.
//
// A duplicate mapping key is the one parse-phase error of the contract (DUPLICATE_KEY,
// anchored at the second occurrence); only the first, in source order, is reported. The
// YAML library parses the whole text before the tree is walked, so a syntax error later in
// the text is reported instead of an earlier duplicate key.
//
// The error, when not nil, is an *Error.
func ParseWith(text string, limits Limits) (*Node, error) {
	if len(text) > limits.MaxBytes {
		return nil, limit("input is %d bytes, the limit is %d", len(text), limits.MaxBytes)
	}
	bomLen := 0
	if strings.HasPrefix(text, "\xEF\xBB\xBF") {
		bomLen = 3
	}
	body := text[bomLen:]
	if i := strings.IndexAny(body, "\u0085\u2028\u2029"); i >= 0 {
		r, _ := utf8.DecodeRuneInString(body[i:])
		return nil, unsupported("line separator U+%04X (a line break to some YAML readers only)", r)
	}

	dec := yaml.NewDecoder(strings.NewReader(body))
	var doc yaml.Node
	if err := dec.Decode(&doc); err != nil {
		if errors.Is(err, io.EOF) {
			return &Node{Kind: KindNull}, nil
		}
		return nil, &Error{Kind: ErrSyntax, Detail: err.Error()}
	}
	var second yaml.Node
	if err := dec.Decode(&second); !errors.Is(err, io.EOF) {
		return nil, unsupported("more than one document")
	}
	w := &walker{table: newTable(body, bomLen), limits: limits}
	return w.node(doc.Content[0], 0)
}

type walker struct {
	table  *table
	limits Limits
	nodes  int
}

func (w *walker) count() error {
	w.nodes++
	if w.nodes > w.limits.MaxNodes {
		return limit("more than %d nodes", w.limits.MaxNodes)
	}
	return nil
}

func isPlain(n *yaml.Node) bool {
	return n.Style&(yaml.DoubleQuotedStyle|yaml.SingleQuotedStyle|yaml.LiteralStyle|yaml.FoldedStyle) == 0
}

// common rejects what the subset never reads, in the order the Rust peer checks it.
func common(n *yaml.Node) error {
	switch {
	case n.Kind == yaml.AliasNode:
		return unsupported("alias")
	case n.Anchor != "":
		return unsupported("anchor")
	case n.Style&yaml.TaggedStyle != 0:
		return unsupported("tag")
	}
	return nil
}

func (w *walker) at(n *yaml.Node) *Pos {
	return w.table.pos(n.Line, n.Column)
}

// node converts a value node; depth is the number of containers around it.
func (w *walker) node(n *yaml.Node, depth int) (*Node, error) {
	if err := common(n); err != nil {
		return nil, err
	}
	if err := w.count(); err != nil {
		return nil, err
	}
	switch n.Kind {
	case yaml.ScalarNode:
		out := &Node{Pos: w.at(n)}
		if !isPlain(n) {
			out.Kind, out.Str = KindStr, n.Value
			return out, nil
		}
		return out, resolvePlain(n.Value, out)
	case yaml.SequenceNode:
		if depth >= w.limits.MaxDepth {
			return nil, limit("nesting is deeper than %d", w.limits.MaxDepth)
		}
		out := &Node{Kind: KindSeq, Pos: w.at(n), Items: []*Node{}}
		for _, c := range n.Content {
			item, err := w.node(c, depth+1)
			if err != nil {
				return nil, err
			}
			out.Items = append(out.Items, item)
		}
		return out, nil
	default:
		if depth >= w.limits.MaxDepth {
			return nil, limit("nesting is deeper than %d", w.limits.MaxDepth)
		}
		return w.mapping(n, depth)
	}
}

func (w *walker) mapping(n *yaml.Node, depth int) (*Node, error) {
	out := &Node{Kind: KindMap, Pos: w.at(n), Entries: []Entry{}}
	// The keys so far: a duplicate is found in expected O(1), not by comparing with every
	// earlier key.
	seen := make(map[string]struct{}, len(n.Content)/2)
	for i := 0; i+1 < len(n.Content); i += 2 {
		k, v := n.Content[i], n.Content[i+1]
		if err := common(k); err != nil {
			return nil, err
		}
		if k.Kind != yaml.ScalarNode {
			return nil, unsupported("non-scalar mapping key")
		}
		if isPlain(k) && k.Value == "<<" {
			return nil, unsupported("merge key")
		}
		if _, dup := seen[k.Value]; dup {
			return nil, &Error{Kind: ErrContract, Violations: []Violation{{
				Code:     "DUPLICATE_KEY",
				Phase:    PhaseParse,
				Location: Location{Available: true, Pos: *w.at(k)},
			}}}
		}
		seen[k.Value] = struct{}{}
		val, err := w.node(v, depth+1)
		if err != nil {
			return nil, err
		}
		out.Entries = append(out.Entries, Entry{Key: k.Value, KeyPos: w.at(k), Value: val})
	}
	return out, nil
}

// table turns the YAML library's line and column into the ADR 0007 coordinates.
type table struct {
	body       string
	bomLen     int
	lineStarts []int
}

func newTable(body string, bomLen int) *table {
	starts := []int{0}
	for i := 0; i < len(body); i++ {
		switch body[i] {
		case '\n':
			starts = append(starts, i+1)
		case '\r':
			if i+1 < len(body) && body[i+1] == '\n' {
				i++
			}
			starts = append(starts, i+1)
		}
	}
	return &table{body: body, bomLen: bomLen, lineStarts: starts}
}

func (t *table) pos(line, col int) *Pos {
	if line > len(t.lineStarts) {
		line = len(t.lineStarts)
	}
	off := t.lineStarts[line-1]
	for c := 1; c < col && off < len(t.body); c++ {
		_, size := utf8.DecodeRuneInString(t.body[off:])
		off += size
	}
	return &Pos{Line: line, Column: col, Offset: t.bomLen + off}
}

func resolvePlain(s string, out *Node) error {
	switch s {
	case "", "~", "null", "Null", "NULL":
		out.Kind = KindNull
		return nil
	case "true", "True", "TRUE":
		out.Kind, out.Bool = KindBool, true
		return nil
	case "false", "False", "FALSE":
		out.Kind, out.Bool = KindBool, false
		return nil
	}
	if digits, ok := decimalDigits(s); ok {
		if len(digits) > 1 && digits[0] == '0' {
			return unsupported("integer with a leading zero: %s", s)
		}
		i, err := strconv.ParseInt(strings.TrimPrefix(s, "+"), 10, 64)
		if err != nil {
			return unsupported("integer beyond int64: %s", s)
		}
		out.Kind, out.Int = KindInt, i
		return nil
	}
	if isFloatLike(s) || isRadixNumber(s) {
		return unsupported("number spelling: %s", s)
	}
	out.Kind, out.Str = KindStr, s
	return nil
}

func stripSign(s string) string {
	if s != "" && (s[0] == '+' || s[0] == '-') {
		return s[1:]
	}
	return s
}

func allDigits(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// decimalDigits returns the digits of [+-]?[0-9]+, if s is exactly that.
func decimalDigits(s string) (string, bool) {
	d := stripSign(s)
	return d, d != "" && allDigits(d)
}

func isHex(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
			return false
		}
	}
	return true
}

func isRadixNumber(s string) bool {
	for _, p := range []string{"0x", "0o"} {
		if rest, ok := strings.CutPrefix(s, p); ok && isHex(rest) {
			return true
		}
	}
	return false
}

func isFloatLike(s string) bool {
	t := stripSign(s)
	switch t {
	case ".inf", ".Inf", ".INF":
		return true
	}
	switch s {
	case ".nan", ".NaN", ".NAN":
		return true
	}
	mantissa, exp, hasExp := t, "", false
	if i := strings.IndexAny(t, "eE"); i >= 0 {
		mantissa, exp, hasExp = t[:i], t[i+1:], true
	}
	if hasExp {
		e := stripSign(exp)
		if e == "" || !allDigits(e) {
			return false
		}
	}
	intPart, frac, hasFrac := mantissa, "", false
	if i := strings.IndexByte(mantissa, '.'); i >= 0 {
		intPart, frac, hasFrac = mantissa[:i], mantissa[i+1:], true
	}
	if !allDigits(intPart) || (hasFrac && !allDigits(frac)) {
		return false
	}
	if hasFrac {
		return intPart != "" || frac != ""
	}
	return intPart != "" && hasExp
}
