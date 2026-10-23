package forms_test

// source_span (P3b, step E2): for every scalar of a generated document written by the emitter, the span is one line of the text, the span parsed
// alone is the value the parser gave for that node, and the only refusals are the ones a document can earn. The read-back never has to refuse
// (EDIT_SPAN_MISMATCH is a defect), and nothing panics. The Rust peer is `the_span_of_every_scalar_reads_back_to_its_value`.

import (
	"errors"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"pgregory.net/rapid"
)

// scalarLeaves are the field-name paths of the scalars below composites.
func scalarLeaves(s forms.Shape, prefix []string, out *[][]string) {
	if s.Kind != forms.ShapeComposite {
		return
	}
	for _, f := range s.Fields {
		p := append(append([]string{}, prefix...), f.Name)
		switch f.Shape.Kind {
		case forms.ShapeScalar:
			*out = append(*out, p)
		case forms.ShapeComposite:
			scalarLeaves(f.Shape, p, out)
		}
	}
}

func nodeAt(n *forms.Node, names []string) *forms.Node {
	for _, name := range names {
		if n == nil || n.Kind != forms.KindMap {
			return nil
		}
		var next *forms.Node
		for _, e := range n.Entries {
			if e.Key == name {
				next = e.Value
				break
			}
		}
		n = next
	}
	return n
}

func isSimple(n *forms.Node) bool {
	switch n.Kind {
	case forms.KindInt, forms.KindBool:
		return true
	case forms.KindStr:
		if len(n.Str) < 2 || n.Str[0] != 's' {
			return false
		}
		for _, c := range n.Str[1:] {
			if c < '0' || c > '9' {
				return false
			}
		}
		return true
	}
	return false
}

func TestPropertyTheSpanOfEveryScalarReadsBackToItsValue(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		root := rootShape(t)
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		text := forms.Emit(conforming(root, &seed, nil, nil))
		doc, err := forms.Parse(text)
		if err != nil {
			t.Fatalf("the emitter writes what the parser reads: %v", err)
		}
		var leaves [][]string
		scalarLeaves(root, nil, &leaves)
		for _, names := range leaves {
			segs := make([]forms.PathSeg, len(names))
			for i, n := range names {
				segs[i] = forms.PathSeg{Field: n}
			}
			path, perr := forms.FormatPath(segs)
			if perr != nil {
				continue
			}
			span, err := forms.SourceSpan(text, root, path, keySet)
			want := nodeAt(doc, names)
			if err == nil {
				token := text[span.Start:span.End]
				if token == "" || strings.ContainsAny(token, "\r\n") {
					t.Fatalf("%s: the span %q is empty or holds a line break in %q", path, token, text)
				}
				// read as the value of a mapping entry, the context it came from (#87)
				parsed, perr := forms.Parse("k: " + token)
				if perr != nil || want == nil || parsed.Kind != forms.KindMap || !parsed.Entries[0].Value.Same(want) {
					t.Fatalf("%s: %q is not the value of the node in %q", path, token, text)
				}
				continue
			}
			var fe *forms.Error
			if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
				t.Fatalf("%s: outside the contract: %v in %q", path, err, text)
			}
			switch fe.Violations[0].Code {
			case "EDIT_NO_SOURCE_VALUE", "EDIT_BLOCK_SCALAR", "EDIT_MULTILINE_SCALAR", "EDIT_FLOW_CONTEXT":
			default:
				t.Fatalf("%s: %v in %q", path, fe.Violations[0], text)
			}
			if want != nil && isSimple(want) { // not vacuous: a short plain word, a number or a boolean is always found
				t.Fatalf("%s: a plain scalar was refused with %v in %q", path, fe.Violations[0], text)
			}
		}
	})
}
