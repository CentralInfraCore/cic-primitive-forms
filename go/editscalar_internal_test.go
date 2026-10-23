package forms

// The guarantee check of edit_scalar, directly: no input of the public function can make it fail on purpose (ADR 0012, EDIT_SPAN_MISMATCH is an
// internal assertion), so each condition is given a text that breaks only that condition. The Rust peer is the unit tests in rust/src/editscalar.rs.

import "testing"

var editKeys = []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}

func editShape() Shape {
	return Shape{Kind: ShapeComposite, Presence: Optional, Fields: []NamedShape{
		{Name: "n", Shape: Shape{Kind: ShapeScalar, Scalar: ScalarString, Presence: Optional}},
		{Name: "count", Shape: Shape{Kind: ShapeScalar, Scalar: ScalarInteger, Presence: Optional}},
	}}
}

func mustParse(t *testing.T, text string) *Node {
	t.Helper()
	n, err := Parse(text)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestVerifyEditNamesWhichGuaranteeFailed(t *testing.T) {
	text := "n: abc\ncount: 3\n"
	sp := Span{Start: 3, End: 6, Style: SpanPlain}
	want := mustParse(t, "n: xyz\ncount: 3\n")
	for _, c := range []struct {
		name, out, code string
	}{
		{"the guarantees hold", "n: xyz\ncount: 3\n", ""},
		{"a byte before the span changed", "m: xyz\ncount: 3\n", "EDIT_SPAN_MISMATCH"},
		{"a byte after the span changed", "n: xyz\ncount: 4\n", "EDIT_SPAN_MISMATCH"},
		{"the result does not parse", "n: 'xyz\ncount: 3\n", "EDIT_INVALID_RESULT"},
		{"the result does not validate", "n: xyz\nbogus: 1\ncount: 3\n", "EDIT_INVALID_RESULT"},
		{"the result is another tree", "n: xyw\ncount: 3\n", "EDIT_SPAN_MISMATCH"},
	} {
		if got := verifyEdit(text, sp, c.out, editShape(), editKeys, want); got != c.code {
			t.Errorf("%s: got %q, want %q", c.name, got, c.code)
		}
	}
	// only the suffix is wrong (the prefix is right, and the tree is the expected one), and only the prefix is wrong (the suffix is right, the tree is the
	// expected one): each half of the byte condition is read on its own, and the comparison of the trees cannot make up for it
	short := mustParse(t, "n: xyz\n")
	if verifyEdit("n: abc\n", Span{Start: 3, End: 6}, "n: xyz", editShape(), editKeys, short) != "EDIT_SPAN_MISMATCH" {
		t.Error("a changed suffix")
	}
	if verifyEdit("n: abc\n", Span{Start: 3, End: 6}, "m: xyz\n", editShape(), editKeys, mustParse(t, "m: xyz\n")) != "EDIT_SPAN_MISMATCH" {
		t.Error("a changed prefix")
	}
}

func TestReplacedKeepsTheTreeAndReplacesOneScalar(t *testing.T) {
	doc := mustParse(t, "n: abc\nlist:\n  - abc\n  - 4\n")
	// the scalar of `n` starts at byte 3; the item `abc` at 17 is the same text and stays
	got := replaced(doc, 3, &Node{Kind: KindStr, Str: "xyz"})
	if !got.Same(mustParse(t, "n: xyz\nlist:\n  - abc\n  - 4\n")) {
		t.Errorf("the scalar at 3 was not the only change")
	}
	if doc.Same(got) || !doc.Same(mustParse(t, "n: abc\nlist:\n  - abc\n  - 4\n")) {
		t.Errorf("the original tree was changed")
	}
	if !replaced(doc, 17, &Node{Kind: KindInt, Int: 9}).Same(mustParse(t, "n: abc\nlist:\n  - 9\n  - 4\n")) {
		t.Errorf("a scalar inside a sequence")
	}
	if !replaced(doc, 999, &Node{Kind: KindStr, Str: "zzz"}).Same(doc) {
		t.Errorf("an offset where no scalar starts changes nothing")
	}
}

func TestCarriesAndRepresentableAtTheirEdges(t *testing.T) {
	str := Shape{Kind: ShapeScalar, Scalar: ScalarString}
	if carries(nil, &Node{Kind: KindStr}) {
		t.Error("no shape holds nothing")
	}
	if !carries(&str, &Node{Kind: KindStr}) || carries(&str, &Node{Kind: KindSeq}) {
		t.Error("a string position and a collection")
	}
	for _, v := range []*Node{{Kind: KindStr, Str: "ok é"}, {Kind: KindInt}, {Kind: KindBool}, {Kind: KindNull}} {
		if !representable(v) {
			t.Errorf("%v", v)
		}
	}
	for _, s := range []string{"a\x00", "a\tb", "a\nb", "a\x7f", "a\xc2\x85", "a\xe2\x80\xa8", "a\xe2\x80\xa9", "a\xff"} {
		if representable(&Node{Kind: KindStr, Str: s}) {
			t.Errorf("%q", s)
		}
	}
}
