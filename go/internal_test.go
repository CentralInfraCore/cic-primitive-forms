package forms

import (
	"errors"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func aliasNode() *yaml.Node { return &yaml.Node{Kind: yaml.AliasNode} }

// The position table clamps a line the YAML library should never report; this reaches the
// clamp, which no document can.
func TestThePositionTableClampsALinePastTheEnd(t *testing.T) {
	tab := newTable("a: 1\nb: 2\n", 0)
	if got := *tab.pos(99, 1); got.Line != 3 || got.Offset != 10 {
		t.Errorf("%+v", got)
	}
	if got := *tab.pos(3, 1); got.Line != 3 {
		t.Errorf("%+v", got)
	}
}

// An alias cannot follow its anchor in a document that gets this far (the anchor is refused
// first), so the alias refusal is reached directly.
func TestAnAliasNodeIsRefused(t *testing.T) {
	w := &walker{table: newTable("", 0), limits: DefaultLimits()}
	if _, err := w.node(aliasNode(), 0); err == nil || err.(*Error).Kind != ErrUnsupported {
		t.Errorf("%v", err)
	}
}

// Outside the package a Grammar can only come from BindGrammar, so a version the contract does not define
// has to be built here. Such a Grammar is not grammar 2: it reads nothing and writes nothing.
func TestAGrammarOfAnUnknownVersionIsNeitherReadNorWritten(t *testing.T) {
	for _, version := range []int64{-1, 0, 3, 100} {
		g := Grammar{version: version}
		for _, n := range []*Node{{Kind: KindStr, Str: "inherit"}, {Kind: KindBool, Bool: true}, {Kind: KindInt}} {
			mode, v := ReadMode(n, g)
			if v == nil || v.Code != "UNKNOWN_GRAMMAR_VERSION" || mode != ModeInvalid {
				t.Errorf("version %d read %+v: %v %+v", version, n, mode, v)
			}
		}
		for _, m := range []Mode{ModeInherit, ModeNoInherit, ModeReset} {
			if n, err := WriteMode(m, g); n != nil || err == nil {
				t.Errorf("version %d wrote mode %d: %+v", version, m, n)
			}
		}
	}
}

// prescan refuses these before the YAML library is asked; the checks behind it stay, in case a
// spelling gets past it, and are reached here directly.
func TestTheChecksBehindThePrescanStillRefuse(t *testing.T) {
	for text, what := range map[string]string{
		"a: &x 1\nb: *x\n":  "anchor",
		"a: !!str x\n":      "tag",
		"&x [a]\n":          "anchor",
		"!!seq [a]\n":       "tag",
		"a: 1\n---\nb: 2\n": "more than one document",
		"? [a]\n: b\n":      "non-scalar mapping key",
		"&k a: 1\n":         "anchor",
	} {
		_, err := read(text, 0, DefaultLimits())
		fe, ok := err.(*Error)
		if !ok || fe.Kind != ErrUnsupported || !strings.Contains(fe.Detail, what) {
			t.Errorf("%q: %v", text, err)
		}
	}
}

// What survived the mutation run: the reason of a refusal, and the places where a word scan has
// to go on or stop. It is the Rust peer's the_scans_stop_and_go_on_where_the_yaml_does.
func TestTheScansStopAndGoOnWhereTheYamlDoes(t *testing.T) {
	reason := func(text string) string {
		_, err := Parse(text)
		fe, ok := err.(*Error)
		if !ok || fe.Kind != ErrUnsupported {
			t.Fatalf("%q: %v", text, err)
		}
		return fe.Detail
	}
	// A marker ends the leading position, and so does content before it.
	for text, want := range map[string]string{
		"a: 1\n---\nb: 2\n":                   "document marker",
		"---\n---\n":                          "document marker",
		"a: 1\r---\rb: 2\r":                   "document marker",
		"a: 1\r%x\r":                          "directive",
		"a: 'x\\'\nb: [? c]\n":                "explicit mapping key",
		"a: \"x\"\nb: [? c]\n":                "explicit mapping key",
		"k:\n  a: |\n      x\n\n  b: [? c]\n": "explicit mapping key",
		"a: 1 # c\rb: [? c]\r":                "explicit mapping key",
		"a: |\r  x\rb: [? c]\r":               "explicit mapping key",
		"a: [x,?c]\n":                         "explicit mapping key",
		"a: x]\nb: [y,?c]\n":                  "explicit mapping key",
	} {
		if got := reason(text); got != want {
			t.Errorf("%q: %q, want %q", text, got, want)
		}
	}
	for text, want := range map[string]string{
		"a: \"x\\\"\"\n? - y\n": "explicit mapping key",
		"a: 'x'''\n? - y\n":     "explicit mapping key",
		"a: 1 # c\r? - y\r":     "explicit mapping key",
		"a: 1 # c\n? - y\n":     "explicit mapping key",
		"a: |\r  x\r? - y\r":    "explicit mapping key",
		"a: |\n  x\n? - y\n":    "explicit mapping key",
		"a|\n  ? - y\n":         "explicit mapping key",
		"]#\n":                  "comment without a space before it",
		"}#\n":                  "comment without a space before it",
		" ? - y\n":              "explicit mapping key",
		"  a: [? c]\n":          "explicit mapping key",
		"? ":                    "explicit mapping key",
		"?x":                    "explicit mapping key",
	} {
		if got := reason(text); got != want {
			t.Errorf("%q: %q, want %q", text, got, want)
		}
	}
	for _, text := range []string{
		"a: \"x\\\"\n  ? - y\"\n",
		"a: 'x''\n  ? - y'\n",
		"a: '\n  ? - y'\n",
		"a: >\n  ? - y\n",
		"a: | # c\n  ? - y\n",
		"a: 1\r? y\r: z\r",
		"a: \"x\\\"[? c]\"\n",
		"k:\n  a: |\n      x\n \n      !y\n",
		"k:\r  a: |\r      x\r \r      !y\r",
		"a: [x]\nb: z,?c\n",
		"a: {x: 1}\nb: z,?c\n",
		"\"a ?b\"\n",
		"a: |\r  x\r",
	} {
		if _, err := Parse(text); err != nil {
			t.Errorf("%q: %v", text, err)
		}
	}
}

// A grammar profile without item_choice refuses a schema that uses it, instead of reading it (ADR 0002,
// finding 3). The only pin the library knows defines it, so the refusal is tested with a profile table
// of its own; the primitives release v0.2.0 is not a known pin (it would be GRAMMAR_PIN_UNKNOWN).
func TestItemChoiceNeedsTheGrammarToDefineIt(t *testing.T) {
	schema := []byte("spec:\n  config_surface:\n    nodes:\n      - name: a\n        shape_type: collection\n        collection_variant: list\n        item_choice:\n          cases: []\n")
	profiles := map[string]grammarProfile{"old": {ItemChoice: false}, "new": {ItemChoice: true}}
	in := SchemaInput{Bytes: schema, SHA256: SHA256Hex(schema), Grammar: "old", Surface: "config_surface"}
	_, err := compileWith(in, profiles)
	var e *Error
	if !errors.As(err, &e) || e.Code() != SchemaFeatureNotInGrammar || e.Problems[0].Path != "$.a.item_choice" {
		t.Fatalf("got %v", err)
	}
	in.Grammar = "new"
	_, err = compileWith(in, profiles)
	if !errors.As(err, &e) || e.Code() != SchemaInvalid {
		t.Fatalf("with the feature the structure is checked instead: %v", err)
	}
}

// The walk never defaults a mandatory field. Normalize refuses a document without one before the walk (#88), so this reaches the
// guard without the validation; the Rust peer is `the_walk_never_defaults_a_mandatory_field` in rust/src/expand.rs.
func TestTheWalkNeverDefaultsAMandatoryField(t *testing.T) {
	x := &Node{Kind: KindStr, Str: "x"}
	root := Shape{Kind: ShapeComposite, Presence: Mandatory, Fields: []NamedShape{
		{Name: "must", Shape: Shape{Kind: ShapeScalar, Scalar: ScalarString, Presence: Mandatory, HasDefault: true, Default: x}},
	}}
	a := []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}
	w := &formWalker{a: a, g: Grammar{version: 1}, prov: Provenance{}, defaults: true}
	out, err := w.record(&Node{Kind: KindMap}, root, nil, true)
	if err != nil || len(out.Entries) != 0 || len(w.origins) != 0 {
		t.Fatalf("got %v / %+v / %v", err, out, w.origins)
	}
}
