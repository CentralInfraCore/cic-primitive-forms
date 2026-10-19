package forms

import (
	"errors"
	"io"
	"regexp"
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
	if err := prescan(body); err != nil {
		return nil, err
	}
	return read(body, bomLen, limits)
}

// read is the tree of a text that prescan let through. A function of its own so a test can feed
// it what prescan refuses and reach the checks behind it.
func read(body string, bomLen int, limits Limits) (*Node, error) {
	dec := yaml.NewDecoder(strings.NewReader(body))
	var doc yaml.Node
	if err := dec.Decode(&doc); err != nil {
		if errors.Is(err, io.EOF) {
			return &Node{Kind: KindNull}, nil
		}
		return nil, &Error{Kind: ErrSyntax, Detail: err.Error()}
	}
	var second yaml.Node
	switch err := dec.Decode(&second); {
	case err == nil:
		return nil, unsupported("more than one document")
	case !errors.Is(err, io.EOF):
		return nil, &Error{Kind: ErrSyntax, Detail: err.Error()}
	}
	w := &walker{table: newTable(body, bomLen), limits: limits, endsWithBreak: strings.HasSuffix(body, "\n") || strings.HasSuffix(body, "\r")}
	return w.node(doc.Content[0], 0)
}

type walker struct {
	table  *table
	limits Limits
	nodes  int
	// endsWithBreak: the text ends with a line break (block scalars are refused otherwise).
	endsWithBreak bool
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
		out := &Node{}
		if n.Style&(yaml.LiteralStyle|yaml.FoldedStyle) != 0 {
			// The two readers disagree on the value of a block scalar that ends the text
			// without a line break and on one made of line breaks only.
			if !w.endsWithBreak {
				return nil, unsupported("block scalar in a text without a final line break")
			}
			if strings.Trim(n.Value, "\n") == "" {
				return nil, unsupported("block scalar without content")
			}
		}
		// A plain empty scalar has no text of its own, so no position (ADR 0007).
		if n.Value != "" || !isPlain(n) {
			out.Pos = w.at(n)
		}
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
	seen := map[string]struct{}{}
	for i := range len(n.Content) / 2 {
		k, v := n.Content[2*i], n.Content[2*i+1]
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
	for c := 1; c < col; c++ {
		_, size := utf8.DecodeRuneInString(t.body[off:])
		off += size
	}
	return &Pos{Line: line, Column: col, Offset: t.bomLen + off}
}

var (
	decimalRe = regexp.MustCompile(`^[+-]?([0-9]+)$`)
	// A float spelling: a decimal point with digits on either side, or an exponent, or an
	// infinity or a NaN.
	floatRe = regexp.MustCompile(`^[+-]?(\.[0-9]+|[0-9]+\.[0-9]*)([eE][+-]?[0-9]+)?$|^[+-]?[0-9]+[eE][+-]?[0-9]+$|^[+-]?\.(inf|Inf|INF)$|^\.(nan|NaN|NAN)$`)
	radixRe = regexp.MustCompile(`^0[xo][0-9a-fA-F]+$`)
)

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
	if m := decimalRe.FindStringSubmatch(s); m != nil {
		if len(m[1]) > 1 && m[1][0] == '0' {
			return unsupported("integer with a leading zero: %s", s)
		}
		i, err := strconv.ParseInt(strings.TrimPrefix(s, "+"), 10, 64)
		if err != nil {
			return unsupported("integer beyond int64: %s", s)
		}
		out.Kind, out.Int = KindInt, i
		return nil
	}
	if floatRe.MatchString(s) || radixRe.MatchString(s) {
		return unsupported("number spelling: %s", s)
	}
	out.Kind, out.Str = KindStr, s
	return nil
}

// prescan refuses on the text alone what the two YAML readers disagree on, before either is
// asked. It is the same list as the Rust peer, in the same order. One refusal anywhere in the
// text hides a YAML syntax error anywhere in it.
func prescan(body string) error {
	for _, c := range body {
		if c <= 0x08 || c == 0x0B || c == 0x0C || (c >= 0x0E && c <= 0x1F) || c == 0x7F {
			return unsupported("control character U+%04X", c)
		}
	}
	if strings.Contains(body, "\t") {
		return unsupported("tab character")
	}
	if strings.Contains(body, "\uFEFF") {
		return unsupported("byte order mark inside the text")
	}
	return scanWords([]rune(body))
}

// scanWords refuses a word that starts with `!`, `&` or `*`, or a `?` that is not the emitter's
// explicit key: a tag, an anchor, an alias or an explicit key. The readers do not agree on the odd
// spellings of these (yaml.v3 drops a bare `!` and reads the value as if it were not there). A
// comment that touches a closing `]` or `}` is refused too. Directive lines and document markers
// (`---` other than the first content line, `...`, indented either way) are refused as lines.
// Quoted scalars, comments and block scalar content are skipped: the words and lines there are
// text, `---` and `...` among them. It is the Rust peer's scan_words.
func scanWords(c []rune) error {
	var quote rune
	block := -1
	lineStart := true
	var indent int
	// No content line has been seen yet: the one place a `---` is the start of the document.
	leading := true
	// Depth of the flow collections: `[`, `{` and `,` only separate words inside one (`a[&` is one
	// plain scalar in block context).
	flow := 0
	for i := 0; i < len(c); {
		if lineStart {
			lineStart = false
			indent = 0
			for i+indent < len(c) && c[i+indent] == ' ' {
				indent++
			}
			blank := i+indent >= len(c) || c[i+indent] == '\n' || c[i+indent] == '\r'
			if block >= 0 {
				if blank || indent > block {
					for i < len(c) && c[i] != '\n' && c[i] != '\r' {
						i++
					}
					lineStart = true
					i++
					continue
				}
				block = -1
			}
			if err := checkLine(c, i, indent, quote != 0, &leading); err != nil {
				return err
			}
		}
		ch := c[i]
		if ch == '\n' || ch == '\r' {
			lineStart = true
			i++
			continue
		}
		if quote != 0 {
			if quote == '"' && ch == '\\' {
				i += 2
				continue
			}
			if ch == quote {
				if quote == '\'' && i+1 < len(c) && c[i+1] == '\'' {
					i += 2
					continue
				}
				quote = 0
			}
			i++
			continue
		}
		prev := '\n'
		if i > 0 {
			prev = c[i-1]
		}
		word := startsScalar(c, i, flow)
		if ch == '#' && (prev == ']' || prev == '}') {
			return unsupported("comment without a space before it")
		}
		if (ch == '[' || ch == '{') && word {
			flow++
		} else if (ch == ']' || ch == '}') && flow > 0 {
			flow--
		}
		switch {
		case (ch == '"' || ch == '\'') && word:
			quote = ch
		case (ch == '|' || ch == '>') && word && blockHeader(c, i):
			block = indent
		case word:
			next := ' '
			if i+1 < len(c) {
				next = c[i+1]
			}
			blank := next == ' ' || next == '\n' || next == '\r'
			switch {
			case ch == '!':
				return unsupported("tag")
			case ch == '?' && !blockExplicitKey(c, i):
				return unsupported("explicit mapping key")
			case (ch == '&' || ch == '*') && !blank:
				return unsupported("anchor or alias")
			}
		}
		i++
	}
	return nil
}

// checkLine: a line that is not inside a block scalar: a directive, or a document marker. Inside a
// quoted scalar only a marker at the start of the line counts (the spec ends the document there,
// the readers disagree on what they make of it); an indented `---` or `...` is text.
func checkLine(c []rune, i, indent int, quoted bool, leading *bool) error {
	if !quoted && c[i] == '%' {
		return unsupported("directive")
	}
	at := i + indent
	marker := ""
	for _, m := range []string{"---", "..."} {
		if strings.HasPrefix(string(c[at:min(at+3, len(c))]), m) &&
			(at+3 >= len(c) || c[at+3] == ' ' || c[at+3] == '\n' || c[at+3] == '\r') {
			marker = m
			break
		}
	}
	if marker != "" {
		refused := marker == "..." || indent > 0 || !*leading
		if quoted {
			refused = indent == 0
		}
		if refused {
			return unsupported("document marker")
		}
	}
	if !quoted && at < len(c) && c[at] != '\n' && c[at] != '\r' && c[at] != '#' {
		*leading = false
	}
	return nil
}

// startsScalar: where a scalar can start: after the start of a line, a `:`, `-` or `?` followed by
// a space, or inside a flow collection after `[`, `{` or `,`. Anywhere else a `!`, `&`, `*`, `?`,
// `"` or `'` is a character of a plain scalar (`a *b` is one scalar).
func startsScalar(c []rune, i, flow int) bool {
	j := i
	for j > 0 && c[j-1] == ' ' {
		j--
	}
	if j == 0 {
		return true
	}
	switch c[j-1] {
	case '\n', '\r':
		return true
	case '[', '{', ',':
		return flow > 0
	case ':', '-', '?':
		return j < i
	}
	return false
}

// blockHeader: `|` or `>` at i followed by header digits and signs, then nothing but a comment.
func blockHeader(c []rune, i int) bool {
	end := i + 1
	for end < len(c) && c[end] != '\n' && c[end] != '\r' {
		end++
	}
	rest := strings.TrimLeft(string(c[i+1:end]), "+-123456789")
	rest = strings.TrimLeft(rest, " ")
	return rest == "" || rest[0] == '#'
}

// blockExplicitKey: the `? key` form the emitter writes for a key past the implicit-key limit,
// the only explicit key the subset reads: the `?` starts its line, one space follows, then the
// key's own text. The text may begin with `-`, `?` or `:` (`-x` is a plain scalar) but not with one
// of them followed by a space or the end of the line (`- x` is a block sequence, a key the subset
// does not read).
func blockExplicitKey(runes []rune, i int) bool {
	for j := i - 1; j >= 0 && runes[j] != '\n' && runes[j] != '\r'; j-- {
		if runes[j] != ' ' {
			return false
		}
	}
	if i+2 >= len(runes) || runes[i+1] != ' ' {
		return false
	}
	if strings.ContainsRune(" \n\r", runes[i+2]) {
		return false
	}
	if !strings.ContainsRune("-?:", runes[i+2]) {
		return true
	}
	return i+3 < len(runes) && !strings.ContainsRune(" \n\r", runes[i+3])
}
