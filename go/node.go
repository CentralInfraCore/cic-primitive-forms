// Package forms is schema-driven processing of CIC primitive documents: the P1
// vertical slice, the Go peer of the Rust crate in ../rust.
//
// Supported subset (docs/P1-SUPPORTED-SUBSET.md): Parse reads a single YAML document
// into a Node tree with source positions, Recognize applies the ADR 0001 rule and the
// position validation of ADR 0003/0007 at the positions of a synthetic Position schema,
// and Emit writes a deterministic block-style YAML text. Nothing here modifies its input.
//
// The expanded-form key set A is never hardcoded: the caller passes it in.
package forms

import "slices"

// Pos is a source position inside the YAML text (ADR 0007): Line 1-based, Column
// 1-based in Unicode code points, Offset 0-based in bytes of the original UTF-8 input (a
// leading BOM included).
type Pos struct {
	Line, Column, Offset int
}

// Kind is the type of a Node's value. Absent is not a value (ADR 0003): it is a
// missing entry.
type Kind int

// The kinds of value.
const (
	KindNull Kind = iota
	KindBool
	KindInt
	KindStr
	KindSeq
	KindMap
	// KindUnsupported is a number the document model does not carry: a float, or an integer beyond signed 64 bits. Parse never
	// produces it (it refuses such a spelling); only a caller that builds a view from another source can, and the diff refuses it
	// (DIFF_UNSUPPORTED_VALUE). Str holds its spelling.
	KindUnsupported
)

// Entry is one mapping entry. Keys are the text of their scalar.
type Entry struct {
	Key    string
	KeyPos *Pos
	Value  *Node
}

// Node is a value and, when it came from source text, where it starts.
type Node struct {
	Kind    Kind
	Bool    bool
	Int     int64
	Str     string
	Items   []*Node
	Entries []Entry
	Pos     *Pos
}

// Same reports whether two nodes hold the same value. Positions and mapping order are
// not part of the value.
func (n *Node) Same(o *Node) bool {
	if n.Kind != o.Kind {
		return false
	}
	switch n.Kind {
	case KindNull:
		return true
	case KindBool:
		return n.Bool == o.Bool
	case KindInt:
		return n.Int == o.Int
	case KindStr, KindUnsupported:
		return n.Str == o.Str
	case KindSeq:
		if len(n.Items) != len(o.Items) {
			return false
		}
		for i := range n.Items {
			if !n.Items[i].Same(o.Items[i]) {
				return false
			}
		}
		return true
	default:
		if len(n.Entries) != len(o.Entries) {
			return false
		}
		// The other mapping by key, read once (#85). The entries are compared as a multiset: each entry is matched to a distinct
		// entry of the other mapping with the same key and the same value, so a repeated key (only a hand-built node has one)
		// cannot make Same asymmetric (#90). Without a repeated key every list holds one value.
		byKey := make(map[string][]*Node, len(o.Entries))
		for _, f := range o.Entries {
			byKey[f.Key] = append(byKey[f.Key], f.Value)
		}
		for _, e := range n.Entries {
			vs := byKey[e.Key]
			i := slices.IndexFunc(vs, e.Value.Same)
			if i < 0 {
				return false
			}
			byKey[e.Key] = slices.Delete(vs, i, i+1)
		}
		return true
	}
}
