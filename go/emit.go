package forms

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Emit writes n as deterministic block-style YAML ending with a newline.
//
// Mapping entries keep their order; indentation is two spaces; a string is plain only
// when reading it back yields the same string, otherwise it is double-quoted with
// escapes. The output always parses back (with Parse) to a node with the same value.
// Comments, anchors, quoting style and source layout are not kept; that is
// source-preserving editing (P3b).
func Emit(n *Node) string {
	var b strings.Builder
	switch {
	case n.Kind == KindSeq && len(n.Items) > 0:
		blockSeq(&b, n.Items, 0)
	case n.Kind == KindMap && len(n.Entries) > 0:
		blockMap(&b, n, 0)
	default:
		b.WriteString(inline(n))
		b.WriteByte('\n')
	}
	return b.String()
}

func indent(b *strings.Builder, n int) {
	b.WriteString(strings.Repeat(" ", n))
}

// inline is a node written on the rest of the current line.
func inline(n *Node) string {
	switch n.Kind {
	case KindNull:
		return "null"
	case KindBool:
		return strconv.FormatBool(n.Bool)
	case KindInt:
		return strconv.FormatInt(n.Int, 10)
	case KindStr:
		return str(n.Str)
	case KindSeq:
		return "[]"
	default:
		return "{}"
	}
}

func isBlock(n *Node) bool {
	return (n.Kind == KindSeq && len(n.Items) > 0) || (n.Kind == KindMap && len(n.Entries) > 0)
}

func blockSeq(b *strings.Builder, items []*Node, level int) {
	for _, it := range items {
		indent(b, level)
		b.WriteByte('-')
		itemTail(b, it, level)
	}
}

func blockMap(b *strings.Builder, n *Node, level int) {
	for _, e := range n.Entries {
		indent(b, level)
		b.WriteString(str(e.Key))
		b.WriteByte(':')
		itemTail(b, e.Value, level)
	}
}

// itemTail is what follows "-" or "key:" for a value at level.
func itemTail(b *strings.Builder, v *Node, level int) {
	if !isBlock(v) {
		b.WriteByte(' ')
		b.WriteString(inline(v))
		b.WriteByte('\n')
		return
	}
	b.WriteByte('\n')
	if v.Kind == KindSeq {
		blockSeq(b, v.Items, level+2)
	} else {
		blockMap(b, v, level+2)
	}
}

// str is a YAML string scalar that reads back as the same string.
func str(s string) string {
	if plainSafe(s) {
		return s
	}
	return quoted(s)
}

// yaml11Bools are the spellings YAML 1.1 reads as booleans; quoted so no reader takes them
// for one.
var yaml11Bools = []string{"y", "Y", "yes", "Yes", "YES", "n", "N", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF"}

func plainSafe(s string) bool {
	if contains(yaml11Bools, s) {
		return false
	}
	first, _ := utf8.DecodeRuneInString(s)
	if s == "" || strings.ContainsRune("-?:,[]{}#&*!|>'\"%@`", first) || first == ' ' || strings.HasSuffix(s, " ") {
		return false
	}
	if strings.Contains(s, ": ") || strings.Contains(s, " #") || strings.HasSuffix(s, ":") {
		return false
	}
	for _, c := range s {
		if unicode.IsControl(c) || isLineOrBOM(c) {
			return false
		}
	}
	n, err := Parse(s)
	return err == nil && n.Kind == KindStr && n.Str == s
}

func isLineOrBOM(c rune) bool {
	return c == 0x85 || c == 0x2028 || c == 0x2029 || c == 0xFEFF
}

func quoted(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, c := range s {
		switch {
		case c == '"':
			b.WriteString(`\"`)
		case c == '\\':
			b.WriteString(`\\`)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c == '\t':
			b.WriteString(`\t`)
		case unicode.IsControl(c) || isLineOrBOM(c):
			fmt.Fprintf(&b, `\u%04X`, c)
		default:
			b.WriteRune(c)
		}
	}
	b.WriteByte('"')
	return b.String()
}
