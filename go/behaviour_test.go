package forms_test

// Behaviour of the slice beyond the corpus: limits, positions, ordering, refusals and the
// guarantee that validation changes nothing. The Rust peer has the same tests.

import (
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// skipIfReduced drops the corpus-driven tests. CI runs the coverage gate once with them
// dropped to prove that the gate turns red on product code when tests are missing.
func skipIfReduced(t *testing.T) {
	t.Helper()
	if os.Getenv("CIC_FORMS_REDUCED") != "" {
		t.Skip("CIC_FORMS_REDUCED: corpus-driven tests dropped")
	}
}

const expanded = "value: 1\naccess: inherit\nmodify: inherit\ninherit: true\ndefault_injection: null\nconformance: implemented\n"

func mustParse(t *testing.T, text string) *forms.Node {
	t.Helper()
	n, err := forms.Parse(text)
	if err != nil {
		t.Fatalf("%q: %v", text, err)
	}
	return n
}

func kindOf(err error) forms.ErrorKind {
	var fe *forms.Error
	if !errors.As(err, &fe) {
		return -1
	}
	return fe.Kind
}

func TestLimitsAreEnforcedAndExplicit(t *testing.T) {
	small := forms.Limits{MaxBytes: 8, MaxDepth: 64, MaxNodes: 100}
	if _, err := forms.ParseWith("a: 1234567890", small); kindOf(err) != forms.ErrLimit {
		t.Errorf("bytes: %v", err)
	}
	shallow := forms.Limits{MaxBytes: 1 << 20, MaxDepth: 2, MaxNodes: 100}
	if _, err := forms.ParseWith("[[1]]", shallow); err != nil {
		t.Errorf("depth 2 is allowed: %v", err)
	}
	for _, text := range []string{"[[[1]]]", "a: {b: {c: 1}}"} {
		if _, err := forms.ParseWith(text, shallow); kindOf(err) != forms.ErrLimit {
			t.Errorf("%s: %v", text, err)
		}
	}
	few := forms.Limits{MaxBytes: 1 << 20, MaxDepth: 64, MaxNodes: 3}
	if _, err := forms.ParseWith("[1, 2]", few); err != nil {
		t.Errorf("3 nodes are allowed: %v", err)
	}
	_, err := forms.ParseWith("[1, 2, 3]", few)
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Code() != "LIMIT_EXCEEDED" {
		t.Errorf("nodes: %v", err)
	}
}

func TestDeepNestingIsRefusedNotOverflowed(t *testing.T) {
	// Past our limit but below the YAML library's own: ours answers.
	if _, err := forms.Parse(strings.Repeat("[", 200) + strings.Repeat("]", 200)); kindOf(err) != forms.ErrLimit {
		t.Errorf("200: %v", err)
	}
	// Far past both: whoever answers first, it is an error and not a stack overflow.
	if _, err := forms.Parse(strings.Repeat("[", 100_000) + strings.Repeat("]", 100_000)); err == nil {
		t.Error("100000: expected an error")
	}
}

func TestPositionsFollowADR0007(t *testing.T) {
	n := mustParse(t, "a: 1\r\nb:\r\n  - x\r\n")
	if *n.Pos != (forms.Pos{Line: 1, Column: 1, Offset: 0}) {
		t.Errorf("root %+v", *n.Pos)
	}
	if *n.Entries[1].KeyPos != (forms.Pos{Line: 2, Column: 1, Offset: 6}) {
		t.Errorf("key %+v", *n.Entries[1].KeyPos)
	}
	if *n.Entries[1].Value.Items[0].Pos != (forms.Pos{Line: 3, Column: 5, Offset: 14}) {
		t.Errorf("item %+v", *n.Entries[1].Value.Items[0].Pos)
	}

	// A lone CR is a line break; a tab is one column.
	n = mustParse(t, "a: 1\rb: [\tz]\r")
	if *n.Entries[1].KeyPos != (forms.Pos{Line: 2, Column: 1, Offset: 5}) {
		t.Errorf("key %+v", *n.Entries[1].KeyPos)
	}
	if *n.Entries[1].Value.Items[0].Pos != (forms.Pos{Line: 2, Column: 6, Offset: 10}) {
		t.Errorf("item %+v", *n.Entries[1].Value.Items[0].Pos)
	}
}

func TestTheFirstDuplicateInSourceOrderIsTheOneReported(t *testing.T) {
	_, err := forms.Parse("a: 1\nb: {x: 1, x: 2}\na: 3\n")
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || len(fe.Violations) != 1 {
		t.Fatalf("%v", err)
	}
	v := fe.Violations[0]
	if v.Code != "DUPLICATE_KEY" || v.Phase != forms.PhaseParse ||
		v.Location != (forms.Location{Available: true, Pos: forms.Pos{Line: 2, Column: 11, Offset: 15}}) {
		t.Errorf("%+v", v)
	}
}

func TestRecognitionIsByKeySetAndNeverByValue(t *testing.T) {
	a := []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}
	opaque := forms.Position{Kind: forms.PosOpaque}
	class := func(text string, a []string) forms.Class {
		out, err := forms.Recognize(mustParse(t, text), opaque, a)
		if err != nil {
			t.Fatal(err)
		}
		return out.Class
	}
	// Same keys, hostile values: still expanded.
	if class(strings.Replace(expanded, "inherit: true", "inherit: [1]", 1), a) != forms.ClassExpanded {
		t.Error("hostile values")
	}
	if class("value: 1\naccess: inherit\n", a) != forms.ClassShort {
		t.Error("one key short")
	}
	for _, text := range []string{"{}", "[]", "x"} {
		if class(text, a) != forms.ClassShort {
			t.Errorf("%s", text)
		}
	}
	// An empty A makes only the empty mapping expanded.
	if class("{}", nil) != forms.ClassExpanded {
		t.Error("empty A")
	}
}

func TestAListOfRecordsRecognizesEachFieldAndOrdersItsErrors(t *testing.T) {
	a := []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}
	record := forms.Position{Kind: forms.PosListOf, Record: []forms.RecordField{
		{Name: "host", Position: forms.Position{Kind: forms.PosScalar}},
		{Name: "port", Position: forms.Position{Kind: forms.PosScalar}},
	}}
	text := "- host: {a: 1}\n  port: 22\n- port: {b: 2}\n  colour: red\n  extra: 1\n- host: ok\n"
	out, err := forms.Recognize(mustParse(t, text), record, a)
	if err != nil || out.Class != forms.ClassShort {
		t.Fatalf("%v %+v", err, out)
	}
	// MAP_AT_SCALAR_POSITION and UNKNOWN_FIELD, one each (smallest path), by path: [0].host
	// sorts before [1].colour.
	codes := []string{}
	for _, v := range out.Violations {
		codes = append(codes, v.Code)
	}
	if !reflect.DeepEqual(codes, []string{"MAP_AT_SCALAR_POSITION", "UNKNOWN_FIELD"}) {
		t.Errorf("%v", codes)
	}
	if out.Violations[1].Location != (forms.Location{Available: true, Pos: forms.Pos{Line: 4, Column: 3, Offset: 43}}) {
		t.Errorf("%+v", out.Violations[1].Location)
	}

	// A recognition error anywhere hides the validation errors (phases do not mix).
	var b strings.Builder
	b.WriteString("- host: {a: 1}\n- host:\n")
	for _, l := range strings.Split(strings.TrimSuffix(expanded, "\n"), "\n") {
		b.WriteString("    " + l + "\n")
	}
	b.WriteString("    extra: 1\n")
	out, err = forms.Recognize(mustParse(t, b.String()), record, a)
	if err != nil || out.Class != forms.ClassError || len(out.Violations) != 1 ||
		out.Violations[0].Code != "EXPANDED_WITH_EXTRA_KEY" || out.Violations[0].Phase != forms.PhaseRecognize {
		t.Errorf("%v %+v", err, out)
	}
}

func TestShapesWithoutARuleAreRefusedNotAccepted(t *testing.T) {
	a := []string{"value"}
	structured := forms.Position{Kind: forms.PosStructured, Fields: []string{"host"}}
	list := forms.Position{Kind: forms.PosListOf, Record: []forms.RecordField{{Name: "host", Position: forms.Position{Kind: forms.PosScalar}}}}
	nested := forms.Position{Kind: forms.PosListOf, Record: []forms.RecordField{{Name: "inner", Position: list}}}
	for _, c := range []struct {
		text string
		pos  forms.Position
	}{{"5", structured}, {"5", list}, {"- 5", list}, {"- inner: [1]", nested}} {
		if _, err := forms.Recognize(mustParse(t, c.text), c.pos, a); kindOf(err) != forms.ErrUnsupported {
			t.Errorf("%s: %v", c.text, err)
		}
	}
}

func TestRecognizeDoesNotChangeItsInput(t *testing.T) {
	n := mustParse(t, expanded+"extra: 1\n")
	before := mustParse(t, expanded+"extra: 1\n")
	_, _ = forms.Recognize(n, forms.Position{Kind: forms.PosScalar}, []string{"value", "access", "modify", "inherit", "default_injection", "conformance"})
	if !reflect.DeepEqual(n, before) {
		t.Error("the node changed")
	}
}

func TestSameIgnoresPositionAndMappingOrderButNotValueOrType(t *testing.T) {
	x := mustParse(t, "a: 1\nb: [1, 2]\n")
	if !x.Same(mustParse(t, "b: [1, 2]\na: 1\n")) {
		t.Error("order")
	}
	for _, other := range []string{"a: 1\nb: [2, 1]\n", "a: 2\nb: [1, 2]\n", "a: '1'\nb: [1, 2]\n", "a: 1\n", "a: 1\nc: [1, 2]\n"} {
		if x.Same(mustParse(t, other)) {
			t.Errorf("%s", other)
		}
	}
	tr, nl := &forms.Node{Kind: forms.KindBool, Bool: true}, &forms.Node{Kind: forms.KindNull}
	if !tr.Same(&forms.Node{Kind: forms.KindBool, Bool: true}) || tr.Same(nl) || !nl.Same(&forms.Node{Kind: forms.KindNull}) ||
		(&forms.Node{Kind: forms.KindInt, Int: 1}).Same(&forms.Node{Kind: forms.KindInt, Int: 2}) {
		t.Error("scalars")
	}
}

func TestEmitIsStableUnderReparse(t *testing.T) {
	once := forms.Emit(mustParse(t, "z: [1, {y: 'a b', x: ''}]\nk: null\n"))
	if forms.Emit(mustParse(t, once)) != once {
		t.Error("not stable")
	}
}

func TestErrorCodesOutsideTheRegistryAreNamed(t *testing.T) {
	for kind, code := range map[forms.ErrorKind]string{forms.ErrUnsupported: "UNSUPPORTED_SYNTAX", forms.ErrSyntax: "YAML_SYNTAX", forms.ErrLimit: "LIMIT_EXCEEDED"} {
		e := &forms.Error{Kind: kind, Detail: "d"}
		if e.Code() != code || !strings.HasSuffix(e.Error(), ": d") {
			t.Errorf("%v", e)
		}
	}
}
