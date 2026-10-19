// canon-go is the second, independent implementation of the canonical JSON of the test evidence
// (docs/TEST-EVIDENCE.md, "Canonical form"). It shares no code and no parser with canon.py: it reads the same
// vectors (vectors/canon.json) and has to give the same canonical bytes, and the same class of refusal, for each.
//
//	canon-go VECTORS.json            check every vector, exit 1 on any difference
//	canon-go canon < DOCUMENT        write the canonical form of a document, or the class of its refusal on stderr
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

const (
	maxInt   = 1<<53 - 1
	maxDepth = 64
)

type refusal struct{ kind string }

func (r refusal) Error() string { return r.kind }

func fail(kind string) { panic(refusal{kind}) }

type parser struct {
	s []byte
	i int
}

// value kinds: nil, bool, int64, string, []any, *object
type object struct {
	keys []string
	vals map[string]any
}

func parse(data []byte) (v any, err error) {
	defer func() {
		if r := recover(); r != nil {
			if rf, ok := r.(refusal); ok {
				err = rf
				return
			}
			panic(r)
		}
	}()
	if bytes.HasPrefix(data, []byte{0xEF, 0xBB, 0xBF}) {
		fail("BOM")
	}
	if !utf8.Valid(data) {
		fail("INVALID_UTF8")
	}
	p := &parser{s: data}
	p.ws()
	if p.i >= len(p.s) {
		fail("EMPTY")
	}
	v = p.value(0)
	p.ws()
	if p.i != len(p.s) {
		fail("TRAILING")
	}
	return v, nil
}

func (p *parser) ws() {
	for p.i < len(p.s) && strings.IndexByte(" \t\n\r", p.s[p.i]) >= 0 {
		p.i++
	}
}

func (p *parser) peek() byte {
	if p.i < len(p.s) {
		return p.s[p.i]
	}
	return 0
}

func (p *parser) value(depth int) any {
	if c := p.peek(); (c == '{' || c == '[') && depth >= maxDepth { // depth counts the containers around the value
		fail("DEPTH")
	}
	switch c := p.peek(); {
	case c == '{':
		return p.object(depth)
	case c == '[':
		return p.array(depth)
	case c == '"':
		return p.str()
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	for _, lit := range []struct {
		text string
		val  any
	}{{"true", true}, {"false", false}, {"null", nil}} {
		if bytes.HasPrefix(p.s[p.i:], []byte(lit.text)) {
			p.i += len(lit.text)
			return lit.val
		}
	}
	fail("SYNTAX")
	return nil
}

func (p *parser) object(depth int) any {
	p.i++
	o := &object{vals: map[string]any{}}
	p.ws()
	if p.peek() == '}' {
		p.i++
		return o
	}
	for {
		p.ws()
		if p.peek() != '"' {
			fail("SYNTAX")
		}
		k := p.str()
		if _, dup := o.vals[k]; dup {
			fail("DUPLICATE_KEY")
		}
		p.ws()
		if p.peek() != ':' {
			fail("SYNTAX")
		}
		p.i++
		p.ws()
		o.vals[k] = p.value(depth + 1)
		o.keys = append(o.keys, k)
		p.ws()
		c := p.peek()
		p.i++
		if c == '}' {
			return o
		}
		if c != ',' {
			fail("SYNTAX")
		}
	}
}

func (p *parser) array(depth int) any {
	p.i++
	out := []any{}
	p.ws()
	if p.peek() == ']' {
		p.i++
		return out
	}
	for {
		p.ws()
		out = append(out, p.value(depth+1))
		p.ws()
		c := p.peek()
		p.i++
		if c == ']' {
			return out
		}
		if c != ',' {
			fail("SYNTAX")
		}
	}
}

func (p *parser) number() any {
	j := p.i
	neg := p.s[j] == '-'
	if neg {
		j++
	}
	if j >= len(p.s) || p.s[j] < '0' || p.s[j] > '9' {
		fail("NUMBER")
	}
	k := j
	for k < len(p.s) && p.s[k] >= '0' && p.s[k] <= '9' {
		k++
	}
	digits := string(p.s[j:k])
	if len(digits) > 1 && digits[0] == '0' {
		fail("NUMBER")
	}
	if k < len(p.s) && (p.s[k] == '.' || p.s[k] == 'e' || p.s[k] == 'E') {
		fail("NUMBER")
	}
	n, err := strconv.ParseInt(digits, 10, 64)
	if err != nil {
		fail("NUMBER")
	}
	if neg {
		if n == 0 {
			fail("NUMBER")
		}
		n = -n
	}
	if n > maxInt || n < -maxInt {
		fail("NUMBER")
	}
	p.i = k
	return n
}

func (p *parser) hex4() rune {
	if p.i+4 > len(p.s) {
		fail("ESCAPE")
	}
	var n rune
	for _, c := range p.s[p.i : p.i+4] {
		var d byte
		switch {
		case c >= '0' && c <= '9':
			d = c - '0'
		case c >= 'a' && c <= 'f':
			d = c - 'a' + 10
		case c >= 'A' && c <= 'F':
			d = c - 'A' + 10
		default:
			fail("ESCAPE")
		}
		n = n*16 + rune(d)
	}
	p.i += 4
	return n
}

func (p *parser) str() string {
	p.i++
	var out []rune
	for {
		if p.i >= len(p.s) {
			fail("SYNTAX")
		}
		r, size := utf8.DecodeRune(p.s[p.i:])
		p.i += size
		switch {
		case r == '"':
			return string(out)
		case r < 0x20:
			fail("CONTROL_CHAR")
		case r != '\\':
			out = append(out, r)
			continue
		}
		if p.i >= len(p.s) {
			fail("SYNTAX")
		}
		e := p.s[p.i]
		p.i++
		switch e {
		case '"', '\\', '/':
			out = append(out, rune(e))
		case 'b':
			out = append(out, '\b')
		case 'f':
			out = append(out, '\f')
		case 'n':
			out = append(out, '\n')
		case 'r':
			out = append(out, '\r')
		case 't':
			out = append(out, '\t')
		case 'u':
			cu := p.hex4()
			switch {
			case cu >= 0xD800 && cu <= 0xDBFF:
				if p.i+1 < len(p.s) && p.s[p.i] == '\\' && p.s[p.i+1] == 'u' {
					p.i += 2
					lo := p.hex4()
					if lo >= 0xDC00 && lo <= 0xDFFF {
						out = append(out, utf16.DecodeRune(cu, lo))
						continue
					}
				}
				fail("LONE_SURROGATE")
			case cu >= 0xDC00 && cu <= 0xDFFF:
				fail("LONE_SURROGATE")
			default:
				out = append(out, cu)
			}
		default:
			fail("ESCAPE")
		}
	}
}

func quote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\b':
			b.WriteString(`\b`)
		case r == '\f':
			b.WriteString(`\f`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r < 0x20:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

func lessUTF16(a, b string) bool {
	x, y := utf16.Encode([]rune(a)), utf16.Encode([]rune(b))
	for i := 0; i < len(x) && i < len(y); i++ {
		if x[i] != y[i] {
			return x[i] < y[i]
		}
	}
	return len(x) < len(y)
}

func canonical(v any) string {
	switch t := v.(type) {
	case nil:
		return "null"
	case bool:
		if t {
			return "true"
		}
		return "false"
	case int64:
		return strconv.FormatInt(t, 10)
	case string:
		return quote(t)
	case []any:
		parts := make([]string, len(t))
		for i, x := range t {
			parts[i] = canonical(x)
		}
		return "[" + strings.Join(parts, ",") + "]"
	case *object:
		keys := append([]string(nil), t.keys...)
		sort.Slice(keys, func(i, j int) bool { return lessUTF16(keys[i], keys[j]) })
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = quote(k) + ":" + canonical(t.vals[k])
		}
		return "{" + strings.Join(parts, ",") + "}"
	}
	panic(fmt.Sprintf("unexpected %T", v))
}

type vector struct {
	Name         string  `json:"name"`
	InputHex     string  `json:"input_hex"`
	CanonicalHex *string `json:"canonical_hex"`
	Refuse       *string `json:"refuse"`
}

func check(path string) int {
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	var doc struct {
		Cases []vector `json:"cases"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil || len(doc.Cases) == 0 {
		fmt.Fprintln(os.Stderr, "vectors unreadable or empty:", err)
		return 2
	}
	bad := 0
	for _, c := range doc.Cases {
		in, err := hex.DecodeString(c.InputHex)
		if err != nil {
			fmt.Printf("BAD  %s: input_hex\n", c.Name)
			bad++
			continue
		}
		v, perr := parse(in)
		switch {
		case c.Refuse != nil:
			if perr == nil {
				fmt.Printf("BAD  %s: accepted, want refusal %s\n", c.Name, *c.Refuse)
				bad++
			} else if perr.Error() != *c.Refuse {
				fmt.Printf("BAD  %s: refused %s, want %s\n", c.Name, perr, *c.Refuse)
				bad++
			}
		case perr != nil:
			fmt.Printf("BAD  %s: refused %s, want a document\n", c.Name, perr)
			bad++
		default:
			want, _ := hex.DecodeString(*c.CanonicalHex)
			if got := canonical(v); got != string(want) {
				fmt.Printf("BAD  %s: canonical %q, want %q\n", c.Name, got, want)
				bad++
			}
		}
	}
	fmt.Printf("canon-go: %d vectors, %d differences\n", len(doc.Cases), bad)
	if bad > 0 {
		return 1
	}
	return 0
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "canon" {
		in, _ := io.ReadAll(os.Stdin)
		v, err := parse(in)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Print(canonical(v))
		return
	}
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: canon-go VECTORS.json | canon-go canon < DOCUMENT")
		os.Exit(2)
	}
	os.Exit(check(os.Args[1]))
}
