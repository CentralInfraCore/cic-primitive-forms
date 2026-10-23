package forms_test

// edit_scalar (P3b, step E3) at the places a vector of corpus/edit cannot reach (its trees have no nullable): null, the styles at their edges, the
// plain-safe rule, a document that does not validate, the parsed value without text. The Rust peer is rust/tests/editscalar.rs.

import (
	"errors"
	"math"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func editScalarShape(typ forms.ScalarType, nullable bool) forms.Shape {
	return forms.Shape{Kind: forms.ShapeScalar, Scalar: typ, Presence: forms.Optional, Nullable: nullable}
}

// nullableRoot has n (a nullable string), count (an integer) and ok (a boolean).
func nullableRoot() forms.Shape {
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "n", Shape: editScalarShape(forms.ScalarString, true)},
		{Name: "count", Shape: editScalarShape(forms.ScalarInteger, false)},
		{Name: "ok", Shape: editScalarShape(forms.ScalarBoolean, false)},
	}}
}

const bomMark = "\xef\xbb\xbf"

func str(s string) *forms.Node    { return &forms.Node{Kind: forms.KindStr, Str: s} }
func integer(i int64) *forms.Node { return &forms.Node{Kind: forms.KindInt, Int: i} }
func null() *forms.Node           { return &forms.Node{Kind: forms.KindNull} }

func editWire(t *testing.T, err error) wire {
	t.Helper()
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || len(fe.Violations) != 1 {
		t.Fatalf("one violation of the contract was expected, got %v", err)
	}
	return wireOf(fe.Violations[0])
}

func TestEditScalarWritesAndReadsNull(t *testing.T) {
	root := nullableRoot()
	cases := []struct {
		text, path string
		value      *forms.Node
		want       string
	}{
		{"n: abc\n", "$.n", null(), "n: null\n"}, // null is written in the canonical spelling
		{"n: ~\n", "$.n", str("x"), "n: x\n"},    // an explicit ~ is a scalar with text
		{"n: ~\n", "$.n", null(), "n: ~\n"},      // and the same value keeps its token
		{"n: null\n", "$.n", null(), "n: null\n"},
		{"n: Null\n", "$.n", str("y"), "n: y\n"},
		{"count: 3\n", "$.count", integer(math.MinInt64), "count: -9223372036854775808\n"},
		{"count: 3\n", "$.count", integer(math.MaxInt64), "count: 9223372036854775807\n"},
		{"count: +5\n", "$.count", integer(5), "count: +5\n"}, // the same value: a non-canonical spelling stays
		{"count: +5\n", "$.count", integer(6), "count: 6\n"},
	}
	for _, c := range cases {
		got, err := forms.EditScalar(c.text, root, c.path, keySet, c.value)
		if err != nil || got.Text != c.want {
			t.Errorf("%q %s: got %q %v, want %q", c.text, c.path, got.Text, err, c.want)
		}
	}
}

func TestEditScalarPlainSafeAndTheStyles(t *testing.T) {
	root := nullableRoot()
	ok := []string{"a:b", "a#b", "yes", "x y z", "é", "a-b", "a'b", `a"b`, "Null1", "1a"}
	for _, v := range ok {
		got, err := forms.EditScalar("n: old\n", root, "$.n", keySet, str(v))
		if err != nil || got.Text != "n: "+v+"\n" {
			t.Errorf("plain %q: got %q %v", v, got.Text, err)
		}
	}
	conflict := []string{"a:", "a ", "!x", "%x", "@x", "`x", "?x", "[x", "{x", "*x", "&x", "|x", ">x", "'x", `"x`, ",x", "#x", "null", "~", "False", "1.5", "0x1F"}
	for _, v := range conflict {
		_, err := forms.EditScalar("n: old\n", root, "$.n", keySet, str(v))
		if w := editWire(t, err); w.code != "EDIT_STYLE_CONFLICT" {
			t.Errorf("plain %q: %v", v, w)
		}
	}
	// the quoted styles carry what the plain one cannot, and keep their quotes
	quoted := []struct {
		text, want string
		v          string
	}{
		{"n: 'old'\n", "n: 'null'\n", "null"},
		{"n: 'old'\n", "n: ''\n", ""},
		{"n: \"old\"\n", "n: \"\"\n", ""},
		{"n: \"old\"\n", "n: \"a: b\"\n", "a: b"},
		{"n: 'old'\n", "n: '#x'\n", "#x"},
		{"n: \"old\" # c\n", "n: \"é\" # c\n", "é"},
	}
	for _, c := range quoted {
		got, err := forms.EditScalar(c.text, root, "$.n", keySet, str(c.v))
		if err != nil || got.Text != c.want {
			t.Errorf("%q <- %q: got %q %v, want %q", c.text, c.v, got.Text, err, c.want)
		}
	}
	// an existing token with an escaped control character is read and can be replaced; a new value never holds one
	got, err := forms.EditScalar("n: \"a\\tb\"\n", root, "$.n", keySet, str("c"))
	if err != nil || got.Text != "n: \"c\"\n" {
		t.Errorf("an escaped control character in the old token: %q %v", got.Text, err)
	}
	// a quoted string cannot carry null or a number; the style is never changed
	_, err = forms.EditScalar("n: \"old\"\n", root, "$.n", keySet, null())
	if w := editWire(t, err); w.code != "EDIT_STYLE_CONFLICT" {
		t.Errorf("null into a quoted scalar: %v", w)
	}
}

func TestEditScalarTheKindsOfTheValue(t *testing.T) {
	root := nullableRoot()
	for _, c := range []struct {
		path  string
		value *forms.Node
	}{
		{"$.count", &forms.Node{Kind: forms.KindBool, Bool: true}}, // a boolean for an integer
		{"$.ok", integer(1)},
		{"$.count", null()}, // not nullable
		{"$.n", integer(5)},
		{"$.n", &forms.Node{Kind: forms.KindSeq}}, // a collection is no scalar
		{"$.n", &forms.Node{Kind: forms.KindMap}},
	} {
		_, err := forms.EditScalar("n: a\ncount: 3\nok: true\n", root, c.path, keySet, c.value)
		if w := editWire(t, err); w.code != "EDIT_TYPE_MISMATCH" {
			t.Errorf("%s <- %v: %v", c.path, c.value, w)
		}
	}
	if _, err := forms.EditScalar("n: a\n", root, "$.n", keySet, nil); editWire(t, err).code != "EDIT_VALUE_NOT_REPRESENTABLE" {
		t.Errorf("no value")
	}
	for _, v := range []string{"a" + string(rune(0x2028)) + "b", "a" + string(rune(0x2029)) + "b", "a" + string(rune(0x85)) + "b", "a\x7fb", "a\x00b", "a\tb", "a\rb"} {
		_, err := forms.EditScalar("n: a\n", root, "$.n", keySet, str(v))
		if w := editWire(t, err); w.code != "EDIT_VALUE_NOT_REPRESENTABLE" {
			t.Errorf("%q: %v", v, w)
		}
	}
	if _, err := forms.EditScalar("n: a\n", root, "$.n", keySet, str("a\xffb")); editWire(t, err).code != "EDIT_VALUE_NOT_REPRESENTABLE" {
		t.Errorf("invalid UTF-8")
	}
}

func TestEditScalarRefusesWhatEarlierPhasesRefuse(t *testing.T) {
	root := nullableRoot()
	// a document that does not validate: the violations of the validate phase, as they are
	_, err := forms.EditScalar("bogus: 1\n", root, "$.n", keySet, str("x"))
	if w := editWire(t, err); w.phase != "validate" || w.code != "UNKNOWN_FIELD" {
		t.Errorf("an unknown field: %v", w)
	}
	// a path that does not parse or resolve
	_, err = forms.EditScalar("n: a\n", root, "$.n.", keySet, str("x"))
	if w := editWire(t, err); w.phase != "path_parse" {
		t.Errorf("a path error: %v", w)
	}
	_, err = forms.EditScalar("n: a\n", root, "$.zzz", keySet, str("x"))
	if w := editWire(t, err); w.phase != "path_resolve" {
		t.Errorf("an unknown field in the path: %v", w)
	}
	// the parsed value: the document and the path are accepted first, then there is no text
	doc := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "n", Value: str("a")}}}
	if w := editWire(t, forms.EditParsed(doc, root, "$.n", keySet, str("x"))); w.code != "EDIT_NO_SOURCE_TEXT" || w.phase != "edit" || w.loc != "unavailable" {
		t.Errorf("a parsed value: %v", w)
	}
	bad := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "bogus", Value: str("a")}}}
	if w := editWire(t, forms.EditParsed(bad, root, "$.n", keySet, str("x"))); w.phase != "validate" {
		t.Errorf("a parsed value that does not validate: %v", w)
	}
	if w := editWire(t, forms.EditParsed(doc, root, "$.zzz", keySet, str("x"))); w.phase != "path_resolve" {
		t.Errorf("a parsed value and a bad path: %v", w)
	}
}

func TestEditScalarKeepsEverythingElse(t *testing.T) {
	root := nullableRoot()
	text := bomMark + "# top\nn:   abc   # keep this\r\n\r\ncount: 3\n# end"
	got, err := forms.EditScalar(text, root, "$.count", keySet, integer(10))
	if err != nil || got.Text != bomMark+"# top\nn:   abc   # keep this\r\n\r\ncount: 10\n# end" {
		t.Fatalf("%q %v", got.Text, err)
	}
	if got.Span.Start != len(bomMark+"# top\nn:   abc   # keep this\r\n\r\ncount: ") || got.Span.End != got.Span.Start+2 || !got.Changed {
		t.Errorf("the span of the result: %v", got.Span)
	}
}

// listRoot has routes, a list whose elements are identified by `to` and hold a scalar `metric` and a composite `net` with a scalar `ip`.
func listRoot() forms.Shape {
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "routes", Shape: forms.Shape{Kind: forms.ShapeDeclaredList, Presence: forms.Optional, Elements: &forms.Elements{Cases: []forms.Case{{
			Name: "route", Key: []string{"to"}, Fields: []forms.NamedShape{
				{Name: "to", Shape: editScalarShape(forms.ScalarString, false)},
				{Name: "metric", Shape: editScalarShape(forms.ScalarInteger, false)},
				{Name: "net", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
					{Name: "ip", Shape: editScalarShape(forms.ScalarString, false)},
				}}},
			}}}}}},
	}}
}

func TestEditScalarReachesAScalarBelowACompositeInAListElement(t *testing.T) {
	text := "routes:\n  - to: a\n    net:\n      ip: 1.1.1.1\n  - to: b\n    metric: 2\n    net:\n      ip: 2.2.2.2\n"
	got, err := forms.EditScalar(text, listRoot(), `$.routes[{to="b"}].net.ip`, keySet, str("9.9.9.9"))
	want := "routes:\n  - to: a\n    net:\n      ip: 1.1.1.1\n  - to: b\n    metric: 2\n    net:\n      ip: 9.9.9.9\n"
	if err != nil || got.Text != want {
		t.Fatalf("%q %v", got.Text, err)
	}
	// a kind the position does not have is found through the same walk
	_, err = forms.EditScalar(text, listRoot(), `$.routes[{to="b"}].net.ip`, keySet, integer(5))
	if w := editWire(t, err); w.code != "EDIT_TYPE_MISMATCH" {
		t.Errorf("%v", w)
	}
}

func TestEditScalarThroughAnExpandedWrapperOfAnInteger(t *testing.T) {
	// the validator reads the wrapper as its `value`: an integer position holds the integer, not the first member that is not `value`
	wrapper := "count:\n  access: inherit\n  value: 3\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n"
	got, err := forms.EditScalar(wrapper, nullableRoot(), "$.count", keySet, integer(4))
	want := "count:\n  access: inherit\n  value: 4\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n"
	if err != nil || got.Text != want {
		t.Fatalf("%q %v", got.Text, err)
	}
}

func TestEditParsedRefusesABadPathBeforeItRefusesTheMissingText(t *testing.T) {
	doc := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "n", Value: str("a")}}}
	if w := editWire(t, forms.EditParsed(doc, nullableRoot(), "$.n.", keySet, str("x"))); w.phase != "path_parse" {
		t.Errorf("%v", w)
	}
}

func TestEditScalarThroughAnExpandedWrapperInAListElement(t *testing.T) {
	wrapper := "      access: inherit\n      value: 1\n      modify: inherit\n      inherit: true\n      default_injection: ~\n      conformance: implemented\n"
	text := "routes:\n  - to: a\n    metric:\n" + wrapper
	got, err := forms.EditScalar(text, listRoot(), `$.routes[{to="a"}].metric`, keySet, integer(7))
	if want := "routes:\n  - to: a\n    metric:\n" + strings.Replace(wrapper, "value: 1", "value: 7", 1); err != nil || got.Text != want {
		t.Fatalf("%q %v", got.Text, err)
	}
}
