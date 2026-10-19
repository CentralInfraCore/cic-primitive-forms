package forms_test

// Behaviour of presence validation beyond the corpus vectors. The Rust peer has the same tests
// (rust/tests/validate.rs).

import (
	"errors"
	"reflect"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

var keySet = []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}

func scalarShape(t forms.ScalarType, p forms.Presence) forms.Shape {
	return forms.Shape{Kind: forms.ShapeScalar, Scalar: t, Presence: p}
}

func compositeShape(fields ...forms.NamedShape) forms.Shape {
	return forms.Shape{Kind: forms.ShapeComposite, Fields: fields, Presence: forms.Optional}
}

func field(name string, s forms.Shape) forms.NamedShape {
	return forms.NamedShape{Name: name, Shape: s}
}

func validateText(t *testing.T, text string, s forms.Shape) (*forms.Validation, error) {
	t.Helper()
	return forms.Validate(mustParse(t, text), s, keySet)
}

func codesOf(t *testing.T, text string, s forms.Shape) []string {
	t.Helper()
	v, err := validateText(t, text, s)
	if err != nil {
		t.Fatalf("%q: %v", text, err)
	}
	out := []string{}
	for _, x := range v.Violations {
		out = append(out, x.Code)
	}
	return out
}

func refusedText(t *testing.T, text string, s forms.Shape) bool {
	t.Helper()
	_, err := validateText(t, text, s)
	return kindOf(err) == forms.ErrUnsupported
}

func TestAbsentAndNullAreDifferentStates(t *testing.T) {
	m := scalarShape(forms.ScalarString, forms.Mandatory)
	v, err := forms.Validate(nil, m, keySet)
	if err != nil || len(v.Violations) != 1 || v.Violations[0].Code != "MISSING_MANDATORY" {
		t.Fatalf("%v %+v", err, v)
	}
	for _, text := range []string{"null", "~"} {
		if got := codesOf(t, text, m); !reflect.DeepEqual(got, []string{"NULL_NOT_ALLOWED"}) {
			t.Errorf("%s: %v", text, got)
		}
	}
	nullable := m
	nullable.Nullable = true
	if got := codesOf(t, "null", nullable); len(got) != 0 {
		t.Errorf("nullable null: %v", got)
	}
	// A nullable field is still mandatory: absent is not null.
	if v, _ := forms.Validate(nil, nullable, keySet); len(v.Violations) != 1 || v.Violations[0].Code != "MISSING_MANDATORY" {
		t.Errorf("%+v", v)
	}
	// An optional field may be absent; the library creates no value for it.
	o := scalarShape(forms.ScalarString, forms.Optional)
	if v, _ := forms.Validate(nil, o, keySet); len(v.Violations) != 0 || len(v.DefaultCandidates) != 0 {
		t.Errorf("%+v", v)
	}
}

func TestADefaultIsACandidateOnlyForAnAbsentOptionalField(t *testing.T) {
	withDefault := func(p forms.Presence) forms.Shape {
		s := scalarShape(forms.ScalarInteger, p)
		s.HasDefault = true
		return s
	}
	cand := func(s forms.Shape, n *forms.Node) []string {
		v, err := forms.Validate(n, s, keySet)
		if err != nil {
			t.Fatal(err)
		}
		return v.DefaultCandidates
	}
	opt := withDefault(forms.Optional)
	if got := cand(opt, nil); !reflect.DeepEqual(got, []string{"$"}) {
		t.Errorf("%v", got)
	}
	// Never triggered by a value, null, zero or false.
	for _, text := range []string{"0", "null", "5"} {
		if got := cand(opt, mustParse(t, text)); len(got) != 0 {
			t.Errorf("%s: %v", text, got)
		}
	}
	// A mandatory field is not defaulted (the schema forbids the pair), an undeclared one has none.
	if len(cand(withDefault(forms.Mandatory), nil)) != 0 || len(cand(scalarShape(forms.ScalarInteger, forms.Optional), nil)) != 0 {
		t.Error("candidate where there must be none")
	}
	// Nested: candidates are listed by canonical path, in path order, and nothing is applied.
	d := func() forms.Shape {
		s := scalarShape(forms.ScalarString, forms.Optional)
		s.HasDefault = true
		return s
	}
	s := compositeShape(field("zz", d()), field("aa", d()), field("a.b", d()), field("", d()))
	n := mustParse(t, "{}")
	before := mustParse(t, "{}")
	if got := cand(s, n); !reflect.DeepEqual(got, []string{`$.a\.b`, "$.aa", "$.zz"}) {
		t.Errorf("%v", got)
	}
	if !reflect.DeepEqual(n, before) {
		t.Error("the input changed")
	}
}

func TestScalarTypesAreCheckedAndAMismatchWithoutACodeIsRefused(t *testing.T) {
	types := []forms.ScalarType{forms.ScalarString, forms.ScalarInteger, forms.ScalarBoolean}
	good := []string{"'x'", "7", "true"}
	for i, ty := range types {
		if got := codesOf(t, good[i], scalarShape(ty, forms.Mandatory)); len(got) != 0 {
			t.Errorf("%s: %v", good[i], got)
		}
		for j, text := range good {
			if j != i && !refusedText(t, text, scalarShape(ty, forms.Mandatory)) {
				t.Errorf("%s as %v", text, ty)
			}
		}
		if !refusedText(t, "[1]", scalarShape(ty, forms.Mandatory)) {
			t.Errorf("list as %v", ty)
		}
		// A mapping at a scalar position is the one mismatch that has a code.
		if got := codesOf(t, "{a: 1}", scalarShape(ty, forms.Mandatory)); !reflect.DeepEqual(got, []string{"MAP_AT_SCALAR_POSITION"}) {
			t.Errorf("map as %v: %v", ty, got)
		}
	}
	if forms.ScalarString.String() != "string" || forms.ScalarInteger.String() != "integer" || forms.ScalarBoolean.String() != "boolean" {
		t.Error("scalar type names")
	}
}

func TestMinItemsIsInclusiveAndANonListIsRefused(t *testing.T) {
	list := func(min int, has bool) forms.Shape {
		return forms.Shape{Kind: forms.ShapeList, MinItems: min, HasMinItems: has, Presence: forms.Optional}
	}
	cases := []struct {
		text string
		s    forms.Shape
		want int
	}{
		{"[1]", list(2, true), 1}, {"[1, 2]", list(2, true), 0}, {"[1, 2, 3]", list(2, true), 0},
		{"[]", list(0, false), 0}, {"[]", list(1, true), 1},
	}
	for _, c := range cases {
		if got := codesOf(t, c.text, c.s); len(got) != c.want {
			t.Errorf("%s: %v", c.text, got)
		}
	}
	if !refusedText(t, "5", list(0, false)) || !refusedText(t, "{a: 1}", list(0, false)) {
		t.Error("a non-list is refused")
	}
}

func TestACompositeReportsEveryErrorAtEveryDepthInPathOrder(t *testing.T) {
	inner := compositeShape(field("x", scalarShape(forms.ScalarString, forms.Mandatory)), field("y", scalarShape(forms.ScalarString, forms.Mandatory)))
	s := compositeShape(
		field("b", scalarShape(forms.ScalarString, forms.Mandatory)),
		field("a", inner),
		field("c", scalarShape(forms.ScalarInteger, forms.Optional)),
	)
	v, err := validateText(t, "a: {y: null, q: 1}\nc: null\nzz: 2\n", s)
	if err != nil {
		t.Fatal(err)
	}
	paths, codes := []string{}, []string{}
	for _, x := range v.Violations {
		paths, codes = append(paths, x.LogicalPath), append(codes, x.Code)
	}
	if !reflect.DeepEqual(paths, []string{"$.a.q", "$.a.x", "$.a.y", "$.b", "$.c", "$.zz"}) ||
		!reflect.DeepEqual(codes, []string{"UNKNOWN_FIELD", "MISSING_MANDATORY", "NULL_NOT_ALLOWED", "MISSING_MANDATORY", "NULL_NOT_ALLOWED", "UNKNOWN_FIELD"}) {
		t.Errorf("%v %v", paths, codes)
	}
	if !refusedText(t, "[1]", s) || !refusedText(t, "'x'", s) {
		t.Error("a composite must be a mapping")
	}
}

func TestAnchorsFollowADR0007(t *testing.T) {
	str := scalarShape(forms.ScalarString, forms.Mandatory)
	s := compositeShape(field("a", str), field("b", str), field("c", compositeShape(field("d", str))))
	// a: empty scalar -> its key; b: explicit null -> its value; c.d missing -> the parent c.
	v, err := validateText(t, "a:\nb: null\nc: {}\n", s)
	if err != nil {
		t.Fatal(err)
	}
	at := map[string]forms.Pos{}
	for _, x := range v.Violations {
		at[x.LogicalPath] = x.Location.Pos
	}
	if at["$.a"] != (forms.Pos{Line: 1, Column: 1, Offset: 0}) || at["$.b"] != (forms.Pos{Line: 2, Column: 4, Offset: 6}) ||
		at["$.c.d"] != (forms.Pos{Line: 3, Column: 4, Offset: 14}) {
		t.Errorf("%+v", at)
	}
	// A tilde is a null with text of its own: the value, not the key.
	one := compositeShape(field("a", str))
	v, _ = validateText(t, "a: ~\n", one)
	if v.Violations[0].Location != (forms.Location{Available: true, Pos: forms.Pos{Line: 1, Column: 4, Offset: 3}}) {
		t.Errorf("%+v", v.Violations[0].Location)
	}
	// No text, no coordinates.
	hand := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "a", Value: &forms.Node{Kind: forms.KindNull}}}}
	v, _ = forms.Validate(hand, one, keySet)
	if v.Violations[0].Location.Available {
		t.Error("invented a position")
	}
}

func TestRecognitionRunsFirstAndAnExpandedNodeIsNotValidatedYet(t *testing.T) {
	s := compositeShape(field("a", scalarShape(forms.ScalarString, forms.Optional)))
	expandedDoc := "value: 1\naccess: inherit\nmodify: inherit\ninherit: true\ndefault_injection: null\nconformance: implemented\n"
	indented := ""
	for _, l := range splitLines(expandedDoc) {
		indented += "  " + l + "\n"
	}
	if !refusedText(t, "a:\n"+indented, s) {
		t.Error("an expanded node is refused")
	}
	v, err := validateText(t, "zz: 1\na:\n"+indented+"  extra: 1\n", s)
	if err != nil || len(v.Violations) != 1 || v.Violations[0].Code != "EXPANDED_WITH_EXTRA_KEY" ||
		v.Violations[0].Phase != forms.PhaseRecognize || v.Violations[0].LogicalPath != "$.a" {
		t.Errorf("%v %+v", err, v)
	}
}

func splitLines(s string) []string {
	var out []string
	for len(s) > 0 {
		i := 0
		for i < len(s) && s[i] != '\n' {
			i++
		}
		out = append(out, s[:i])
		s = s[min(i+1, len(s)):]
	}
	return out
}

func TestAnOpaqueFieldTakesAnyValueAndValidationLeavesTheInputAlone(t *testing.T) {
	s := compositeShape(field("o", forms.Shape{Kind: forms.ShapeOpaque, Presence: forms.Mandatory}))
	for _, text := range []string{"o: 1", "o: [1, {a: b}]", "o: {x: 1}", "o: 'x'", "o: true"} {
		if got := codesOf(t, text, s); len(got) != 0 {
			t.Errorf("%s: %v", text, got)
		}
	}
	n := mustParse(t, "o: {x: [1, 2]}\nzz: 1\n")
	before := mustParse(t, "o: {x: [1, 2]}\nzz: 1\n")
	if _, err := forms.Validate(n, s, keySet); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(n, before) {
		t.Error("the input changed")
	}
}

func TestAnUnsupportedValidationIsAnError(t *testing.T) {
	var fe *forms.Error
	_, err := validateText(t, "5", compositeShape())
	if !errors.As(err, &fe) || fe.Code() != "UNSUPPORTED_SYNTAX" {
		t.Errorf("%v", err)
	}
}

func TestAMismatchBelowACompositeIsRefusedNotSwallowed(t *testing.T) {
	s := compositeShape(field("a", compositeShape(field("b", scalarShape(forms.ScalarInteger, forms.Mandatory)))))
	if !refusedText(t, "a: {b: 'x'}", s) {
		t.Error("nested mismatch")
	}
	if got := codesOf(t, "a: {b: 1}", s); len(got) != 0 {
		t.Errorf("%v", got)
	}
}

func TestWithARepeatedKeyTheFirstEntryDecides(t *testing.T) {
	// Only a hand-built mapping can repeat a key; Parse refuses it.
	s := compositeShape(field("a", scalarShape(forms.ScalarString, forms.Mandatory)))
	hand := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{
		{Key: "a", Value: &forms.Node{Kind: forms.KindStr, Str: "x"}},
		{Key: "a", Value: &forms.Node{Kind: forms.KindNull}},
	}}
	if v, err := forms.Validate(hand, s, keySet); err != nil || len(v.Violations) != 0 {
		t.Errorf("%v %+v", err, v)
	}
}

func TestAQuotedEmptyStringHasTextAndAPositionUnlikeAnEmptyScalar(t *testing.T) {
	n := mustParse(t, "a: ''\nb:\n")
	if n.Entries[0].Value.Pos == nil || *n.Entries[0].Value.Pos != (forms.Pos{Line: 1, Column: 4, Offset: 3}) {
		t.Errorf("%+v", n.Entries[0].Value.Pos)
	}
	if n.Entries[1].Value.Pos != nil {
		t.Errorf("%+v", n.Entries[1].Value.Pos)
	}
}

func TestTheRefusalNamesWhatWasFoundAndWhatWasDeclared(t *testing.T) {
	detail := func(text string, s forms.Shape) string {
		_, err := validateText(t, text, s)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Fatalf("%q: %v", text, err)
		}
		return fe.Detail
	}
	integer := scalarShape(forms.ScalarInteger, forms.Mandatory)
	for text, want := range map[string]string{"'x'": "string", "[1]": "list", "true": "boolean"} {
		if d := detail(text, integer); !strings.Contains(d, want) || !strings.Contains(d, "integer is declared") {
			t.Errorf("%s: %s", text, d)
		}
	}
	list := forms.Shape{Kind: forms.ShapeList, Presence: forms.Optional}
	if d := detail("5", list); !strings.Contains(d, "integer") || !strings.Contains(d, "list is declared") {
		t.Error(d)
	}
	if d := detail("{a: 1}", list); !strings.Contains(d, "mapping") {
		t.Error(d)
	}
	if d := detail("5", compositeShape()); !strings.Contains(d, "integer") || !strings.Contains(d, "mapping is declared") {
		t.Error(d)
	}
}
