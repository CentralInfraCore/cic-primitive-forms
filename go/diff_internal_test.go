package forms

import (
	"slices"
	"testing"
)

// The order of the elements of a set (ADR 0008): null, booleans, integers, strings, then by value. A validated set holds one kind, so the
// order across kinds is reached only here.
func TestSetElementsAreOrderedByKindThenByValue(t *testing.T) {
	null := &Node{Kind: KindNull}
	f, tr := &Node{Kind: KindBool}, &Node{Kind: KindBool, Bool: true}
	neg, one := &Node{Kind: KindInt, Int: -1}, &Node{Kind: KindInt, Int: 1}
	a, b := &Node{Kind: KindStr, Str: "a"}, &Node{Kind: KindStr, Str: "b"}
	want := []*Node{null, f, tr, neg, one, a, b}
	got := sortedScalars([]*Node{b, one, tr, a, null, neg, f, one, null})
	if !slices.EqualFunc(got, want, func(x, y *Node) bool { return x.Same(y) }) {
		t.Fatalf("got %v", got)
	}
	for i, x := range want {
		for j, y := range want {
			if c := compareScalars(x, y); (c < 0) != (i < j) || (c == 0) != (i == j) {
				t.Errorf("compare(%d, %d) = %d", i, j, c)
			}
		}
	}
}
