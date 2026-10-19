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
	case KindStr:
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
		for _, e := range n.Entries {
			found := false
			for _, f := range o.Entries {
				if f.Key == e.Key {
					found = e.Value.Same(f.Value)
					break
				}
			}
			if !found {
				return false
			}
		}
		return true
	}
}
