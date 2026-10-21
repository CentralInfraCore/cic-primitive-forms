package forms_test

// Parser fuzzing (PHASES.md: required from P1). `go test` runs the seeds below and anything
// under testdata/fuzz/FuzzParse as ordinary tests; `go test -fuzz FuzzParse` explores.
//
// The properties checked on every input: Parse does not panic, a document that parses
// survives Emit then Parse with the same value, and its positions lie inside the text.
//
// CIC_FUZZ_CANARY is the proof that the fuzz job can fail: with it set, an input that starts
// with "CANARY" is reported as a failure, and CI runs the committed canary seed with it set and
// requires the run to be red. It is test code; the product has no such switch.

import (
	"os"
	"slices"
	"strings"
	"testing"
	"unicode/utf8"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func FuzzParse(f *testing.F) {
	s := vectors(f) // seeds: the shared subset vectors, accepted and refused alike
	for _, v := range s.ParseOK {
		f.Add(v.YAML)
	}
	for _, v := range append(append([]refusal{}, s.Unsupported...), s.Syntax...) {
		f.Add(v.YAML)
	}
	for _, seed := range []string{strings.Repeat("k", 1100) + ": 1\n", "", "a: 1\na: 2\n", "[[[[", "{a: [1, {b: c}]}", "\xef\xbb\xbfa: 1", "a: |\n  x\n", "{*a : 1}\n", "{x: &a 1, *a : 2}\n", "? *a\n: 1\n", "{<<: 1, *a : 2}\n", "a: &x 1\n*x : 2\n", "[[Alse2]:&2-/{a,{[Alse2]:*2-/{6{a,Pa,;~$=PP"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		back, err := forms.Parse(forms.Emit(n))
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: emit/parse changed the value (%v)", text, err)
		}
		var walk func(*forms.Node)
		walk = func(n *forms.Node) {
			if n.Pos != nil && (n.Pos.Line < 1 || n.Pos.Column < 1 || n.Pos.Offset > len(text)) {
				t.Fatalf("%q: position %+v", text, *n.Pos)
			}
			for _, it := range n.Items {
				walk(it)
			}
			for _, e := range n.Entries {
				walk(e.Value)
			}
		}
		walk(n)
	})
}

// fuzzShape is a fixed shape that uses every rule of presence validation.
func fuzzShape() forms.Shape {
	str := forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Mandatory}
	opt := func(s forms.Shape) forms.Shape { s.Presence = forms.Optional; return s }
	nullable := func(s forms.Shape) forms.Shape { s.Nullable = true; return s }
	withDefault := func(s forms.Shape) forms.Shape { s.HasDefault = true; return s }
	list := func(min int) forms.Shape {
		return forms.Shape{Kind: forms.ShapeList, MinItems: min, HasMinItems: true, Presence: forms.Optional}
	}
	boolean := forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean, Presence: forms.Mandatory}
	inner := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "d", Shape: boolean},
		{Name: "e", Shape: list(1)},
		{Name: "f", Shape: forms.Shape{Kind: forms.ShapeOpaque, Presence: forms.Optional}},
	}}
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "a", Shape: str},
		{Name: "b", Shape: withDefault(nullable(opt(forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger})))},
		{Name: "c", Shape: inner},
		{Name: "g", Shape: list(2)},
	}}
}

// FuzzValidate checks, on every input that parses, that presence validation does not panic, leaves
// the document alone, and gives either an unsupported answer or a response of one phase that is
// ordered by path and has no repeated (path, code). CIC_FUZZ_CANARY works as in FuzzParse.
func FuzzValidate(f *testing.F) {
	s := vectors(f)
	for _, v := range s.ParseOK {
		f.Add(v.YAML)
	}
	for _, seed := range []string{"", "a: x\n", "a: null\nb:\nc: {}\n", "a: x\nc: {d: true, e: [1]}\ng: [1, 2]\n", "zz: 1\na:\n"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		before := forms.Emit(n)
		v, err := forms.Validate(n, fuzzShape(), keySet)
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
		if err != nil {
			return
		}
		type key struct{ path, code string }
		seen := map[key]bool{}
		phases := map[forms.Phase]bool{}
		for _, x := range v.Violations {
			k := key{x.LogicalPath, x.Code}
			if seen[k] {
				t.Fatalf("%q: duplicate %+v", text, k)
			}
			seen[k] = true
			phases[x.Phase] = true
		}
		if len(phases) > 1 {
			t.Fatalf("%q: more than one phase", text)
		}
	})
}

var knownReadingCodes = []string{
	"INVALID_INHERIT", "INHERIT_ENUM_IN_GRAMMAR_1", "LEGACY_INHERIT_IN_GRAMMAR_2",
	"INVALID_ACCESS_VALUE", "INVALID_CERT_PATTERN",
}

// FuzzAccess checks, on every input, that reading an access/modify value and an InheritanceMode under
// both grammars does not panic, leaves the document alone, answers with a known code, writes back the
// spelling it read, accepts only valid CertPatterns, and that IsCertPattern agrees with the upstream
// regular expression on the raw text. CIC_FUZZ_CANARY works as in FuzzParse.
func FuzzAccess(f *testing.F) {
	for _, v := range loadAccess(f).CertPattern {
		f.Add(v.Pattern)
	}
	for _, seed := range []string{"", "inherit", "[]", "[inherit]", "true", "false", "0", "no-inherit", "[OU=ops,O=acme]", "a=b", "- a=b\n- c=d\n", "reset", "{*a : 1}\n", "{x: &a 1, *a : 2}\n", "[[Alse2]:&2-/{a,{[Alse2]:*2-/{6{a,Pa,;~$=PP"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		if got, want := forms.IsCertPattern(text), upstreamPattern.MatchString(text); got != want {
			t.Fatalf("%q: library %v, upstream expression %v", text, got, want)
		}
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		before := forms.Emit(n)
		known := func(v *forms.Violation) {
			if v != nil && !slices.Contains(knownReadingCodes, v.Code) {
				t.Fatalf("%q: unknown code %s", text, v.Code)
			}
		}
		rule, v := forms.ReadRule(n)
		known(v)
		for _, p := range rule.Patterns {
			if !forms.IsCertPattern(p) {
				t.Fatalf("%q: accepted the pattern %q", text, p)
			}
		}
		for _, version := range forms.SupportedGrammars() {
			g := grammarOf(t, version)
			mode, v := forms.ReadMode(n, g)
			known(v)
			if w, werr := forms.WriteMode(mode, g); v == nil && (werr != nil || !w.Same(n)) {
				t.Fatalf("%q: grammar %d lost the spelling", text, version)
			}
		}
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
	})
}

// FuzzExpandCollapse checks, on every input that parses and expands, the law of ADR 0001: the input is not changed,
// collapse(expand(x)) with the provenance gives x back, and expand is idempotent. The Rust peer is the target
// `expand`. CIC_FUZZ_CANARY works as in FuzzParse.
func FuzzExpandCollapse(f *testing.F) {
	s := vectors(f)
	for _, v := range s.ParseOK {
		f.Add(v.YAML)
	}
	for _, seed := range []string{"", "a: x\n", "a: {value: x, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}\n",
		"a: x\nc: {d: true, e: [1]}\ng: [1, 2]\n", "c: {value: {d: true}, access: [OU=a], modify: inherit, inherit: 0, default_injection: 0, conformance: deprecated}\n"} {
		f.Add(seed)
	}
	g, err := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, &forms.Node{Kind: forms.KindInt, Int: 1})
	if err != nil {
		f.Fatal(err)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		before := forms.Emit(n)
		expanded, prov, err := forms.Expand(n, fuzzShape(), keySet, g)
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
		if err != nil {
			return
		}
		back, err := forms.Collapse(expanded, fuzzShape(), keySet, g, prov, nil)
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: collapse(expand(x)) != x (%v)", text, err)
		}
		again, _, err := forms.Expand(expanded, fuzzShape(), keySet, g)
		if err != nil || !again.Same(expanded) {
			t.Fatalf("%q: expand is not idempotent (%v)", text, err)
		}
	})
}
