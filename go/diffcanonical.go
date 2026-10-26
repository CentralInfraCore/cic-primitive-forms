package forms

import (
	"slices"
	"strconv"
	"strings"
)

// The canonical bytes of a diff (ADR 0008): UTF-8, no BOM, no whitespace outside strings, object keys sorted by their UTF-8 bytes, integers
// as 0 or -?[1-9][0-9]*, no floats, absence by omitting the field. The writer is here and not a JSON library, so the bytes do not depend on one.

func decl(c DeclChange) *Node {
	return &Node{Kind: KindMap, Entries: []Entry{{Key: "previous", Value: c.Previous}, {Key: "next", Value: c.Next}}}
}

func (e DiffEvent) node() *Node {
	str := func(s string) *Node { return &Node{Kind: KindStr, Str: s} }
	m := []Entry{{Key: "kind", Value: str(e.Kind)}, {Key: "path", Value: str(e.Path)}}
	switch e.Kind {
	case EventAdded:
		m = append(m, Entry{Key: "next", Value: e.Next})
	case EventRemoved:
		m = append(m, Entry{Key: "previous", Value: e.Previous})
	case EventValueChange:
		m = append(m, Entry{Key: "previous", Value: e.Previous}, Entry{Key: "next", Value: e.Next})
	case EventPolicyDeclarationChange:
		changed := &Node{Kind: KindMap}
		for _, c := range e.Changed {
			changed.Entries = append(changed.Entries, Entry{Key: c.Field, Value: decl(c)})
		}
		m = append(m, Entry{Key: "changed", Value: changed})
	default:
		m = append(m, Entry{Key: "added", Value: &Node{Kind: KindSeq, Items: e.Added}}, Entry{Key: "removed", Value: &Node{Kind: KindSeq, Items: e.Removed}})
	}
	return &Node{Kind: KindMap, Entries: m}
}

// Canonical is the exact bytes of the result: {"changes":[...],"representation":[...],"version":1}.
func (r *DiffResult) Canonical() string {
	changes := &Node{Kind: KindSeq, Items: make([]*Node, len(r.Changes))}
	for i, e := range r.Changes {
		changes.Items[i] = e.node()
	}
	rep := &Node{Kind: KindSeq, Items: make([]*Node, len(r.Representation))}
	for i, c := range r.Representation {
		rep.Items[i] = &Node{Kind: KindMap, Entries: []Entry{
			{Key: "kind", Value: &Node{Kind: KindStr, Str: "representation_change"}},
			{Key: "path", Value: &Node{Kind: KindStr, Str: c.Path}},
			{Key: "previous", Value: &Node{Kind: KindStr, Str: c.Previous}},
			{Key: "next", Value: &Node{Kind: KindStr, Str: c.Next}},
		}}
	}
	doc := &Node{Kind: KindMap, Entries: []Entry{{Key: "changes", Value: changes}, {Key: "representation", Value: rep}, {Key: "version", Value: &Node{Kind: KindInt, Int: 1}}}}
	var b strings.Builder
	writeCanonical(&b, doc)
	return b.String()
}

func writeCanonical(b *strings.Builder, n *Node) {
	switch n.Kind {
	case KindNull:
		b.WriteString("null")
	case KindBool:
		b.WriteString(strconv.FormatBool(n.Bool))
	case KindInt:
		b.WriteString(strconv.FormatInt(n.Int, 10))
	case KindStr:
		writeCanonicalString(b, n.Str)
	case KindSeq:
		b.WriteByte('[')
		for i, it := range n.Items {
			if i > 0 {
				b.WriteByte(',')
			}
			writeCanonical(b, it)
		}
		b.WriteByte(']')
	default:
		entries := slices.Clone(n.Entries)
		slices.SortFunc(entries, func(x, y Entry) int { return strings.Compare(x.Key, y.Key) })
		b.WriteByte('{')
		for i, e := range entries {
			if i > 0 {
				b.WriteByte(',')
			}
			writeCanonicalString(b, e.Key)
			b.WriteByte(':')
			writeCanonical(b, e.Value)
		}
		b.WriteByte('}')
	}
}

// writeCanonicalString writes a string as ADR 0008 fixes it: \" and \\, \b \f \n \r \t, the other controls below U+0020 as \u00xx in lowercase
// hex, everything else literal (no \/, no escaped non-ASCII, U+007F literal).
func writeCanonicalString(b *strings.Builder, s string) {
	const hex = "0123456789abcdef"
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
			b.WriteString(`\u00`)
			b.WriteByte(hex[r>>4])
			b.WriteByte(hex[r&15])
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
}
