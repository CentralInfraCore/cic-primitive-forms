package forms_test

// Property tests for Parse and Emit (PHASES.md: required for parse/emit from P1). The Rust
// peer (rust/tests/properties.rs) checks the same properties.
//
//   - Emit then Parse gives back the value, for any value of the supported subset, including
//     strings with every kind of character;
//   - Emit is a fixed point under Parse;
//   - Parse never panics, whatever the text, and the positions it reports lie inside the text;
//   - recognition depends on the key set only, and Recognize does not change its input.

import (
	"reflect"
	"testing"
	"unicode/utf8"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"pgregory.net/rapid"
)

func keyGen() *rapid.Generator[string] {
	return rapid.OneOf(
		rapid.StringMatching(`[a-z]{1,4}`),
		rapid.String(),
		rapid.StringOfN(rapid.Just('k'), 500, 1100, -1),
		rapid.Just("value"),
		rapid.Just("a: b"),
		rapid.Just("yes"),
		rapid.Just("<<"),
		rapid.Just(""),
	)
}

func scalarGen() *rapid.Generator[*forms.Node] {
	return rapid.OneOf(
		rapid.Just(&forms.Node{Kind: forms.KindNull}),
		rapid.Map(rapid.Bool(), func(b bool) *forms.Node { return &forms.Node{Kind: forms.KindBool, Bool: b} }),
		rapid.Map(rapid.Int64(), func(i int64) *forms.Node { return &forms.Node{Kind: forms.KindInt, Int: i} }),
		rapid.Map(rapid.String(), func(s string) *forms.Node { return &forms.Node{Kind: forms.KindStr, Str: s} }),
	)
}

func nodeGen(depth int) *rapid.Generator[*forms.Node] {
	if depth == 0 {
		return scalarGen()
	}
	inner := rapid.Deferred(func() *rapid.Generator[*forms.Node] { return nodeGen(depth - 1) })
	return rapid.OneOf(
		scalarGen(),
		rapid.Map(rapid.SliceOfN(inner, 0, 3), func(items []*forms.Node) *forms.Node {
			return &forms.Node{Kind: forms.KindSeq, Items: append([]*forms.Node{}, items...)}
		}),
		rapid.Map(rapid.SliceOfN(rapid.Custom(func(t *rapid.T) forms.Entry {
			return forms.Entry{Key: keyGen().Draw(t, "key"), Value: inner.Draw(t, "value")}
		}), 0, 3), func(entries []forms.Entry) *forms.Node {
			seen := map[string]bool{}
			out := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
			for _, e := range entries {
				if !seen[e.Key] {
					seen[e.Key] = true
					out.Entries = append(out.Entries, e)
				}
			}
			return out
		}),
	)
}

func TestPropertyEmitThenParseGivesBackTheValue(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := nodeGen(3).Draw(t, "node")
		text := forms.Emit(n)
		back, err := forms.Parse(text)
		if err != nil || !back.Same(n) {
			t.Fatalf("%q -> %v / %+v", text, err, back)
		}
	})
}

func TestPropertyEmitIsAFixedPointUnderParse(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		once := forms.Emit(nodeGen(3).Draw(t, "node"))
		back, err := forms.Parse(once)
		if err != nil {
			t.Fatal(err)
		}
		if twice := forms.Emit(back); twice != once {
			t.Fatalf("%q != %q", once, twice)
		}
	})
}

func checkPositions(t *rapid.T, n *forms.Node, text string) {
	if n.Pos != nil {
		if n.Pos.Line < 1 || n.Pos.Column < 1 || n.Pos.Offset > len(text) {
			t.Fatalf("%+v in %q", *n.Pos, text)
		}
		if n.Pos.Offset < len(text) && !utf8.RuneStart(text[n.Pos.Offset]) {
			t.Fatalf("%+v splits a character of %q", *n.Pos, text)
		}
	}
	for _, it := range n.Items {
		checkPositions(t, it, text)
	}
	for _, e := range n.Entries {
		if e.KeyPos == nil || e.KeyPos.Offset > len(text) {
			t.Fatalf("key %q has no sane position in %q", e.Key, text)
		}
		checkPositions(t, e.Value, text)
	}
}

func TestPropertyParseNeverPanicsAndItsPositionsLieInsideTheText(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		text := rapid.OneOf(rapid.String(), rapid.StringMatching(`[ -~\n\r\t]{0,64}`)).Draw(t, "text")
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		checkPositions(t, n, text)
		back, err := forms.Parse(forms.Emit(n))
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: emit/parse changed the value (%v)", text, err)
		}
	})
}

func TestPropertyRecognitionFollowsTheKeySetAndLeavesTheInputAlone(t *testing.T) {
	a := []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}
	rapid.Check(t, func(t *rapid.T) {
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		for _, k := range a {
			n.Entries = append(n.Entries, forms.Entry{Key: k, Value: nodeGen(2).Draw(t, k)})
		}
		want := forms.ClassExpanded
		if extra := rapid.OneOf(rapid.Just(""), keyGen()).Draw(t, "extra"); extra != "" {
			known := false
			for _, k := range a {
				known = known || k == extra
			}
			if !known {
				n.Entries = append(n.Entries, forms.Entry{Key: extra, Value: &forms.Node{Kind: forms.KindNull}})
				want = forms.ClassError
			}
		}
		before := forms.Emit(n)
		snapshot := reflect.ValueOf(n).Elem().Interface()
		out, err := forms.Recognize(n, forms.Position{Kind: forms.PosOpaque}, a)
		if err != nil || out.Class != want {
			t.Fatalf("%v %+v, want %s", err, out, want)
		}
		if forms.Emit(n) != before || !reflect.DeepEqual(snapshot, reflect.ValueOf(n).Elem().Interface()) {
			t.Fatal("the input changed")
		}
	})
}
