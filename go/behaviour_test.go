package forms_test

// Behaviour of the slice beyond the corpus: limits, positions, ordering, refusals and the
// guarantee that validation changes nothing. The Rust peer has the same tests.

import (
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

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

	// A lone CR is a line break.
	n = mustParse(t, "a: 1\rb: [ z]\r")
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

type wireV struct {
	code, path string
	loc        forms.Location
}

func violationsOf(t *testing.T, text string, p forms.Position) []wireV {
	t.Helper()
	a := []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}
	out, err := forms.Recognize(mustParse(t, text), p, a)
	if err != nil {
		t.Fatal(err)
	}
	got := []wireV{}
	for _, v := range out.Violations {
		got = append(got, wireV{v.Code, v.LogicalPath, v.Location})
	}
	return got
}

func TestEveryUnknownFieldIsReportedWithItsCanonicalPathInAFixedOrder(t *testing.T) {
	structured := forms.Position{Kind: forms.PosStructured, Fields: []string{"host", "port"}}
	// Source order is value, access; the response order is by path: $.access, $.value.
	got := violationsOf(t, "value: 1\naccess: inherit\n", structured)
	want := []wireV{
		{"UNKNOWN_FIELD", "$.access", forms.Location{Available: true, Pos: forms.Pos{Line: 2, Column: 1, Offset: 9}}},
		{"UNKNOWN_FIELD", "$.value", forms.Location{Available: true, Pos: forms.Pos{Line: 1, Column: 1, Offset: 0}}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v", got)
	}
	// From a parsed value (no text) the same two errors have no coordinates.
	node := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{
		{Key: "value", Value: &forms.Node{Kind: forms.KindInt, Int: 1}},
		{Key: "access", Value: &forms.Node{Kind: forms.KindStr, Str: "inherit"}},
	}}
	out, err := forms.Recognize(node, structured, []string{"value", "access", "modify", "inherit", "default_injection", "conformance"})
	if err != nil || len(out.Violations) != 2 ||
		out.Violations[0].LogicalPath != "$.access" || out.Violations[1].LogicalPath != "$.value" ||
		out.Violations[0].Location.Available || out.Violations[1].Location.Available {
		t.Errorf("%v %+v", err, out)
	}
}

func TestLogicalPathsAreCanonicalADR0006TextOrAbsent(t *testing.T) {
	structured := forms.Position{Kind: forms.PosStructured, Fields: []string{"host"}}
	pathOf := func(text string) string { return violationsOf(t, text, structured)[0].path }
	cases := map[string]string{
		"a.b: 1":          `$.a\.b`,
		"x[1]: 1":         `$.x\[1\]`,
		`'a=b,c{d}\e': 1`: `$.a\=b\,c\{d\}\\e`,
		"'a b': 1":        "$.a b",
		`"\u0085": 1`:     "$.\u0085",
		`"a\u0001b": 1`:   "",
		`"a\u007Fb": 1`:   "",
		"'': 1":           "",
	}
	for text, want := range cases {
		if got := pathOf(text); got != want {
			t.Errorf("%s: got %q, want %q", text, got, want)
		}
	}
	// No structure around a single position: no path.
	if got := violationsOf(t, "{a: 1}", forms.Position{Kind: forms.PosScalar})[0].path; got != "" {
		t.Errorf("scalar: %q", got)
	}
	// A list element has no declared identity in the synthetic schema: no path, and the errors
	// that cannot be told apart collapse to one per code.
	list := forms.Position{Kind: forms.PosListOf, Record: []forms.RecordField{{Name: "host", Position: forms.Position{Kind: forms.PosScalar}}}}
	got := violationsOf(t, "- host: {a: 1}\n- host: {b: 2}\n- colour: x\n- shade: y\n", list)
	if len(got) != 2 || got[0].code != "MAP_AT_SCALAR_POSITION" || got[1].code != "UNKNOWN_FIELD" || got[0].path != "" || got[1].path != "" {
		t.Errorf("%+v", got)
	}
}

func manyKeys(n int) string {
	var b strings.Builder
	for i := 0; i < n; i++ {
		fmt.Fprintf(&b, "k%d: 1\n", i)
	}
	return b.String()
}

func TestALateDuplicateIsStillFoundAndManyKeysDoNotCostQuadraticTime(t *testing.T) {
	limits := forms.Limits{MaxBytes: 16 << 20, MaxDepth: 64, MaxNodes: 1_000_000}
	n := 100_000
	start := time.Now()

	ok, err := forms.ParseWith(manyKeys(n), limits)
	if err != nil || len(ok.Entries) != n || ok.Entries[0].Key != "k0" || ok.Entries[n-1].Key != fmt.Sprintf("k%d", n-1) {
		t.Fatalf("distinct keys: %v", err)
	}

	// The duplicate of the first key, at the very end, is reported at its own position.
	_, err = forms.ParseWith(manyKeys(n)+"k0: 2\n", limits)
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || len(fe.Violations) != 1 {
		t.Fatalf("%v", err)
	}
	v := fe.Violations[0]
	if v.Code != "DUPLICATE_KEY" || !v.Location.Available ||
		v.Location.Pos.Line != n+1 || v.Location.Pos.Column != 1 || v.Location.Pos.Offset != len(manyKeys(n)) {
		t.Errorf("%+v", v)
	}
	// A comparison of every key with every earlier key takes minutes at this size; the bound is
	// generous so a slow machine does not fail it, a quadratic implementation does.
	if d := time.Since(start); d > 5*time.Second {
		t.Errorf("took %v", d)
	}
}

// Both parsers rank their errors alike: a syntax error anywhere in the text wins over an
// earlier duplicate key. yaml.v3 reads the whole text before the tree is walked; the Rust peer
// keeps the first non-syntax error and goes on scanning (its test of the same name asserts the
// same answer, and tools/parity compares the two on generated inputs).
func TestASyntaxErrorAfterADuplicateKeyWinsInBothLanguages(t *testing.T) {
	_, err := forms.Parse("a: 1\na: 2\nb: [\n")
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrSyntax {
		t.Fatalf("%v", err)
	}
	// The same duplicate with no syntax error after it is the same answer in both languages.
	_, err = forms.Parse("a: 1\na: 2\nb: []\n")
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || fe.Violations[0].Code != "DUPLICATE_KEY" {
		t.Fatalf("%v", err)
	}
}

func TestTheDefaultLimitsAreThePublishedOnes(t *testing.T) {
	if got := forms.DefaultLimits(); got != (forms.Limits{MaxBytes: 1 << 20, MaxDepth: 64, MaxNodes: 100_000}) {
		t.Errorf("%+v", got)
	}
}

func TestLimitBoundariesAreInclusive(t *testing.T) {
	text := "a: 12345" // 8 bytes
	if _, err := forms.ParseWith(text, forms.Limits{MaxBytes: 8, MaxDepth: 64, MaxNodes: 100}); err != nil {
		t.Errorf("exactly MaxBytes is allowed: %v", err)
	}
	if _, err := forms.ParseWith(text, forms.Limits{MaxBytes: 7, MaxDepth: 64, MaxNodes: 100}); kindOf(err) != forms.ErrLimit {
		t.Errorf("one byte over: %v", err)
	}
	// A mapping as deep as MaxDepth is allowed, one deeper is not.
	if _, err := forms.ParseWith("a: {b: 1}", forms.Limits{MaxBytes: 1 << 20, MaxDepth: 2, MaxNodes: 100}); err != nil {
		t.Errorf("depth 2: %v", err)
	}
}

func TestSameComparesEveryPartOfTheValue(t *testing.T) {
	cases := []struct{ a, b string }{
		{"[1, 2]", "[1, 2, 3]"},    // sequence length
		{"[1, 2]", "[1, 3]"},       // sequence item
		{"{a: 1}", "{a: 1, b: 2}"}, // mapping size
		{"{a: 1}", "{b: 1}"},       // mapping key
		{"{a: 1}", "{a: 2}"},       // mapping value
		{"true", "false"},          // bool
		{"1", "'1'"},               // type
		{"null", "[]"},             // kind
	}
	for _, c := range cases {
		if mustParse(t, c.a).Same(mustParse(t, c.b)) || mustParse(t, c.b).Same(mustParse(t, c.a)) {
			t.Errorf("%s vs %s", c.a, c.b)
		}
	}
	for _, text := range []string{"[1, [2]]", "{a: [1], b: {c: 'x'}}", "'s'", "7", "~", "true"} {
		if !mustParse(t, text).Same(mustParse(t, text)) {
			t.Errorf("%s", text)
		}
	}
	// With a repeated key (only a hand-built node can have one) the first match decides.
	dup := func(vals ...int64) *forms.Node {
		n := &forms.Node{Kind: forms.KindMap}
		for _, v := range vals {
			n.Entries = append(n.Entries, forms.Entry{Key: "a", Value: &forms.Node{Kind: forms.KindInt, Int: v}})
		}
		return n
	}
	if !dup(1, 1).Same(dup(1, 2)) {
		t.Error("the first entry with the key decides")
	}
}

func TestPositionsAfterLeadingLineBreaks(t *testing.T) {
	for text, want := range map[string]forms.Pos{
		"\na: 1":     {Line: 2, Column: 1, Offset: 1},
		"\r\na: 1":   {Line: 2, Column: 1, Offset: 2},
		"\n\r\na: 1": {Line: 3, Column: 1, Offset: 3},
		"\r\ra: 1":   {Line: 3, Column: 1, Offset: 2},
	} {
		n := mustParse(t, text)
		if got := *n.Entries[0].KeyPos; got != want {
			t.Errorf("%q: %+v, want %+v", text, got, want)
		}
	}
}

func TestACodeIsTheFirstViolationsCode(t *testing.T) {
	e := &forms.Error{Kind: forms.ErrContract, Violations: []forms.Violation{{Code: "FIRST"}, {Code: "SECOND"}}}
	if e.Code() != "FIRST" {
		t.Error(e.Code())
	}
}

func TestAKnownFieldDoesNotHideAnUnknownOneAfterIt(t *testing.T) {
	structured := forms.Position{Kind: forms.PosStructured, Fields: []string{"host", "port"}}
	got := violationsOf(t, "host: a\ncolour: red\nport: 1\nshade: x\n", structured)
	if len(got) != 2 || got[0].path != "$.colour" || got[1].path != "$.shade" {
		t.Errorf("%+v", got)
	}
	list := forms.Position{Kind: forms.PosListOf, Record: []forms.RecordField{{Name: "host", Position: forms.Position{Kind: forms.PosScalar}}}}
	got = violationsOf(t, "- host: a\n  colour: red\n", list)
	if len(got) != 1 || got[0].code != "UNKNOWN_FIELD" {
		t.Errorf("%+v", got)
	}
	// An unknown field does not stop the fields after it from being checked.
	got = violationsOf(t, "- colour: x\n  host: {a: 1}\n", list)
	if len(got) != 2 || got[0].code != "UNKNOWN_FIELD" || got[1].code != "MAP_AT_SCALAR_POSITION" {
		t.Errorf("%+v", got)
	}
}

func TestAnEmptyExpandedKeySetNeverMakesAScalarExpanded(t *testing.T) {
	for _, text := range []string{"x", "[1]", "~"} {
		out, err := forms.Recognize(mustParse(t, text), forms.Position{Kind: forms.PosOpaque}, nil)
		if err != nil || out.Class != forms.ClassShort {
			t.Errorf("%s: %v %+v", text, err, out)
		}
	}
}

func TestTheLowestWritableNameCharacterAndTheLastUnwritableOne(t *testing.T) {
	structured := forms.Position{Kind: forms.PosStructured, Fields: []string{"host"}}
	pathOf := func(text string) string { return violationsOf(t, text, structured)[0].path }
	if got := pathOf(`"a\u001Fb": 1`); got != "" {
		t.Errorf("U+001F is unwritable: %q", got)
	}
	if got := pathOf(`"a b": 1`); got != "$.a b" {
		t.Errorf("U+0020 is writable: %q", got)
	}
}

func classOf(err error) string {
	if err == nil {
		return "ok"
	}
	switch kindOf(err) {
	case forms.ErrSyntax:
		return "syntax"
	case forms.ErrUnsupported:
		return "unsupported"
	case forms.ErrContract:
		return "contract"
	case forms.ErrLimit:
		return "limit"
	}
	return "other"
}

func classOfText(text string) string {
	_, err := forms.Parse(text)
	return classOf(err)
}

func firstString(t *testing.T, text string) string {
	t.Helper()
	n := mustParse(t, text)
	if n.Kind != forms.KindMap || n.Entries[0].Value.Kind != forms.KindStr {
		t.Fatalf("%q: %+v", text, n)
	}
	return n.Entries[0].Value.Str
}

// ---, ... and % inside a block or a quoted scalar are text, not a document marker or a
// directive. The expected values are the YAML 1.2 ones, not "what the other language gives".
func TestMarkerLikeTextInsideScalarsIsContent(t *testing.T) {
	for text, want := range map[string]string{
		"a: |\n  ---\n  ...\n":     "---\n...\n",
		"a: >\n  x\n  ---\n  y\n":  "x --- y\n",
		"a: |+\n  ...\n\n":         "...\n\n",
		"a: |\n  %YAML 1.2\n":      "%YAML 1.2\n",
		"a: \"x\n  ---\n  ...\"\n": "x --- ...",
		"a: 'x\n  ---\n  ...'\n":   "x --- ...",
		"a: \"x\n  --- y\"\n":      "x --- y",
		"a: 'x\n  ... y'\n":        "x ... y",
		"a: \"---\"\n":             "---",
		"a: '...'\n":               "...",
	} {
		if got := firstString(t, text); got != want {
			t.Errorf("%q: %q, want %q", text, got, want)
		}
	}
	// The leading marker of a document is allowed, after comments and blank lines too.
	for _, text := range []string{"---\na: 1\n", "# c\n\n---\na: 1\n", "--- \na: 1\n"} {
		if got := classOfText(text); got != "ok" {
			t.Errorf("%q: %s", text, got)
		}
	}
}

// A marker or a directive anywhere else is refused: ..., a second ---, an indented one, one at the
// start of a line inside a quoted scalar, one that ends a block scalar.
func TestMarkersAndDirectivesOutsideContentAreRefused(t *testing.T) {
	for _, text := range []string{
		"a: x\n  ---\n",
		"a: |\n  x\n---\n",
		"a: |\n  x\n...\n",
		"a: \"x\n---\n  y\"\n",
		"a: 'x\n...\n  y'\n",
		"a: \"x\n--- y\"\n",
		"a: 1\n---\nb: 2\n",
		"---\n---\n",
		"a: 1\n...\n",
		"  ---\n",
		"%YAML 1.2\n---\na: 1\n",
		"a: 1\n%TAG ! x\n",
	} {
		if got := classOfText(text); got != "unsupported" {
			t.Errorf("%q: %s", text, got)
		}
	}
}

// The order in which the problems of a text are reported, whatever else the text holds:
// 1. a size over the limit; 2. U+0085, U+2028, U+2029; 3. the text scan, which comes before the YAML
// reader: control characters, tabs, a BOM inside the text, then in text order directives, document
// markers, tags, anchors, aliases, explicit keys, a comment against ] or }: one of these anywhere
// hides a YAML syntax error anywhere; 4. a YAML syntax error anywhere, which beats everything below;
// 5. a second document; 6. the first in document order of an unsupported construct, a duplicate key
// and a node or depth limit. The Rust peer has the same test.
func TestTheOrderInWhichProblemsAreReportedIsFixed(t *testing.T) {
	_, err := forms.ParseWith("a: [\n", forms.Limits{MaxBytes: 4, MaxDepth: 64, MaxNodes: 100})
	if classOf(err) != "limit" {
		t.Errorf("size: %v", err)
	}
	_, err = forms.ParseWith("a: [\n\u0085", forms.Limits{MaxBytes: 4, MaxDepth: 64, MaxNodes: 100})
	if classOf(err) != "limit" {
		t.Errorf("size before separator: %v", err)
	}
	if got := classOfText("a: [\u0085\n"); got != "unsupported" {
		t.Errorf("separator: %s", got)
	}
	for _, text := range []string{
		"a: 1\ta\nb: [\n",
		"b: [\na: \t1\n",
		"a: \x01\nb: [\n",
		"b: [\na: \x01\n",
		"a: !x\nb: [\n",
		"b: [\na: !x\n",
		"a: *x\nb: [\n",
		"a: [? c]\nb: [\n",
		"%YAML 1.2\nb: [\n",
		"b: [\n%YAML 1.2\n",
		"a: 1\n---\nb: [\n",
		"b: [\n---\n",
		"b: [\n...\n",
	} {
		if got := classOfText(text); got != "unsupported" {
			t.Errorf("%q: %s", text, got)
		}
	}
	for _, text := range []string{
		"a: 1.5\nb: [\n",
		"b: [\na: 1.5\n",
		"a: 0o7\nb: ]\n",
		"a: 1\na: 2\nb: [\n",
		"b: [\na: 1\na: 2\n",
		"<<: 1\nb: [\n",
		"[a]: 1\nb: [\n",
	} {
		if got := classOfText(text); got != "syntax" {
			t.Errorf("%q: %s", text, got)
		}
	}
	one := forms.Limits{MaxBytes: 1 << 20, MaxDepth: 64, MaxNodes: 1}
	if _, err := forms.ParseWith("a: 1\nb: [\n", one); classOf(err) != "syntax" {
		t.Errorf("nodes and syntax: %v", err)
	}
	if _, err := forms.ParseWith("a: 1\nb: 2\n", one); classOf(err) != "limit" {
		t.Errorf("nodes: %v", err)
	}
	if got := classOfText("a: 1.5\na: 2\n"); got != "unsupported" {
		t.Errorf("first problem: %s", got)
	}
	if got := classOfText("a: 1\na: 2\nb: 1.5\n"); got != "contract" {
		t.Errorf("first problem: %s", got)
	}
	if got := classOfText("a: 1\na: 2\n"); got != "contract" {
		t.Errorf("duplicate: %s", got)
	}
}

// Found by the fuzz job: a key past the implicit-key limit is emitted as "? key", and a plain key
// that starts with -, ? or : (not followed by a space) must read back.
func TestALongKeyThatStartsWithAnIndicatorReadsBack(t *testing.T) {
	for _, first := range []string{"-", "-x", ":", ":x", "?x", "k", "\u00a0"} {
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{
			Key:   first + strings.Repeat("k", 600),
			Value: &forms.Node{Kind: forms.KindInt, Int: 1},
		}}}
		back, err := forms.Parse(forms.Emit(n))
		if err != nil || !back.Same(n) {
			t.Errorf("%q: %v", first, err)
		}
	}
}

// Boundaries of the marker, explicit-key and block-end checks, with the YAML 1.2 answer for each.
// The Rust peer has the same test.
func TestTheEdgesOfTheTextScanGiveTheYamlAnswer(t *testing.T) {
	reason := func(text string) string {
		_, err := forms.Parse(text)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Fatalf("%q: %v", text, err)
		}
		return fe.Detail
	}
	// A marker is --- or ... followed by a blank or the end of the text, nothing else.
	for text, want := range map[string]string{
		"a: 1\n---": "document marker",
		"a: 1\n...": "document marker",
		" ---\n":    "document marker",
		"k:\n  a: |\n    x\n  b: 1\n  c: [? q]\n": "explicit mapping key",
		"k:\n  a: |\n    x\n  b:\n    - [? q]\n":  "explicit mapping key",
	} {
		if got := reason(text); got != want {
			t.Errorf("%q: %q, want %q", text, got, want)
		}
	}
	// The emitter's explicit key, at the end of the text and with a first character that is an indicator.
	for _, text := range []string{"---x", "x\n...y", "? x", "? -x", "? :x", "a: 1 # [? c]\n", "# [? c]\na: 1\n", "a: |\n  x\n  # [? c]\nb: 1\n"} {
		if got := classOfText(text); got != "ok" {
			t.Errorf("%q: %s", text, got)
		}
	}
	for _, text := range []string{"? -", "? :", "? - x", "? : x", "? ?x"} {
		if got := classOfText(text); got != "unsupported" {
			t.Errorf("%q: %s", text, got)
		}
	}
}

// Found by the fuzz job: "a? *" is one plain scalar (a ? after text is no indicator), and the emitter writes it as
// a key unquoted, so the text scan must not take its * for an alias. The Rust peer has the same test.
func TestAnIndicatorInsideAPlainScalarIsTextAndAtAScalarStartIsNot(t *testing.T) {
	for _, ok := range []string{"a? *: 1\n", "a- &: 1\n", ":- *: 1\n", "-? &: 1\n", "a: b- *c\n"} {
		if got := classOfText(ok); got != "ok" {
			t.Errorf("%q: %s", ok, got)
		}
	}
	for _, refused := range []string{"- - *a\n", "- &a b\n", "a: - *b\n"} {
		if got := classOfText(refused); got != "unsupported" {
			t.Errorf("%q: %s", refused, got)
		}
	}
}

// Every string of up to three characters from the alphabet of YAML indicators, as a value, a sequence item, a
// key and in a nested sequence, reads back from what Emit wrote.
func TestEveryShortStringOfIndicatorsRoundTrips(t *testing.T) {
	alphabet := []rune("a*&!?:-#[]{},\"'\\ |>%@`")
	strs := []string{""}
	frontier := []string{""}
	for range 3 {
		var next []string
		for _, s := range frontier {
			for _, c := range alphabet {
				next = append(next, s+string(c))
			}
		}
		strs = append(strs, next...)
		frontier = next
	}
	one := func(key string, v *forms.Node) *forms.Node {
		return &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: key, Value: v}}}
	}
	for _, s := range strs {
		v := &forms.Node{Kind: forms.KindStr, Str: s}
		for _, n := range []*forms.Node{
			one("k", v),
			{Kind: forms.KindSeq, Items: []*forms.Node{v}},
			one(s, &forms.Node{Kind: forms.KindInt, Int: 1}),
			one("k", &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{v, v}}),
		} {
			text := forms.Emit(n)
			back, err := forms.Parse(text)
			if err != nil || !back.Same(n) {
				t.Fatalf("%q: %v", text, err)
			}
		}
	}
}
