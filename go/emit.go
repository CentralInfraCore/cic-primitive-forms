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
	case !isBlock(n):
		b.WriteString(inline(n))
		b.WriteByte('\n')
	case n.Kind == KindSeq:
		blockSeq(&b, n.Items, 0)
	default:
		blockMap(&b, n, 0)
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
	switch n.Kind {
	case KindSeq:
		return len(n.Items) > 0
	case KindMap:
		return len(n.Entries) > 0
	}
	return false
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
		key := str(e.Key)
		indent(b, level)
		if utf8.RuneCountInString(key) > implicitKeyMax {
			// YAML limits an implicit key ("key: value") to 1024 characters: a longer one is
			// written in the explicit form, which has no limit.
			b.WriteString("? ")
			b.WriteString(key)
			b.WriteByte('\n')
			indent(b, level)
		} else {
			b.WriteString(key)
		}
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

// implicitKeyMax: a key written longer than this many characters uses the explicit "? key"
// form. The YAML limit is 1024; staying well under it keeps the choice independent of how a
// reader counts quotes and escapes.
const implicitKeyMax = 512

// alwaysQuoted are strings written quoted although they read back fine as plain text: the
// spellings YAML 1.1 reads as booleans (so no reader takes them for one) and "<<", which as a
// mapping key is the merge key and is refused by Parse.
var alwaysQuoted = []string{"y", "Y", "yes", "Yes", "YES", "n", "N", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF", "<<"}

// plainSafe says whether s can be written without quotes. It is deliberately defined by the
// round trip: s is plain when reading it back as a document gives the same string. That one
// test covers a leading indicator, ": ", " #", surrounding spaces, anything that reads as another
// type or as a collection, and an unsupported spelling, so none of those is listed again here.
// What the round trip cannot see is excluded first: control characters (a tab or a newline
// reads back fine alone but not in every context) and the strings of alwaysQuoted.
func plainSafe(s string) bool {
	if contains(alwaysQuoted, s) {
		return false
	}
	for _, c := range s {
		if unicode.IsControl(c) {
			return false
		}
	}
	n, err := Parse(s)
	return err == nil && n.Kind == KindStr && n.Str == s
}

func isLineOrBOM(c rune) bool {
	return c == 0x2028 || c == 0x2029 || c == 0xFEFF
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
