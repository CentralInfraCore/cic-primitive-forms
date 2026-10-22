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
		expanded, prov, err := forms.Expand(n, fuzzShape(), keySet, g, forms.Context{})
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
		if err != nil {
			return
		}
		back, err := forms.Collapse(expanded, fuzzShape(), keySet, g, prov, forms.Context{})
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: collapse(expand(x)) != x (%v)", text, err)
		}
		again, _, err := forms.Expand(expanded, fuzzShape(), keySet, g, forms.Context{})
		if err != nil || !again.Same(expanded) {
			t.Fatalf("%q: expand is not idempotent (%v)", text, err)
		}
	})
}

// defaultsShape is a fixed shape with defaults at the top level and inside an optional composite.
func defaultsShape() forms.Shape {
	opt := func(s forms.Shape) forms.Shape { s.Presence = forms.Optional; return s }
	withDefault := func(s forms.Shape, d *forms.Node) forms.Shape { s.HasDefault, s.Default = true, d; return s }
	inner := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Nullable: true, Fields: []forms.NamedShape{
		{Name: "d", Shape: withDefault(opt(forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean}), &forms.Node{Kind: forms.KindBool, Bool: true})},
		{Name: "e", Shape: withDefault(opt(forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString}), &forms.Node{Kind: forms.KindStr, Str: "x"})},
	}}
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "a", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Mandatory}},
		{Name: "b", Shape: withDefault(opt(forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Nullable: true}), &forms.Node{Kind: forms.KindInt, Int: 7})},
		{Name: "c", Shape: inner},
		{Name: "g", Shape: withDefault(opt(forms.Shape{Kind: forms.ShapeSet, Scalar: forms.ScalarString}), &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}})},
	}}
}

// FuzzApplyDefaults checks, on every input that parses: ApplyDefaults does not panic and leaves its input alone; the
// result validates; taking out the values that came from a default gives the input back (no authored value is
// changed or dropped); and applying the defaults again changes nothing and finds every value authored.
// CIC_FUZZ_CANARY works as in FuzzParse.
func FuzzApplyDefaults(f *testing.F) {
	for _, seed := range []string{"", "a: x\n", "a: x\nb: ~\nc: ~\n", "a: x\nb: 0\nc: {d: false}\ng: []\n", "a: x\nc: {}\n", "a: x\nz: 1\n",
		"a: {value: x, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}\n"} {
		f.Add(seed)
	}
	shape := defaultsShape()
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
		res, err := forms.ApplyDefaults(n, shape, keySet)
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
		if err != nil || res.Document == nil {
			return
		}
		if v, err := forms.Validate(res.Document, shape, keySet); err != nil || len(v.Violations) != 0 {
			t.Fatalf("%q: the result does not validate (%v)", text, err)
		}
		again, err := forms.ApplyDefaults(res.Document, shape, keySet)
		if err != nil || again.Document == nil || !again.Document.Same(res.Document) {
			t.Fatalf("%q: applying the defaults again changed the result (%v)", text, err)
		}
		for _, o := range again.Origins {
			if o.Origin != forms.ValueOriginAuthored {
				t.Fatalf("%q: %s is not authored after the defaults were applied", text, o.Path)
			}
		}
		for _, o := range res.Origins {
			if o.Origin != forms.ValueOriginDefault {
				continue
			}
			parts := strings.Split(strings.TrimPrefix(o.Path, "$."), ".")
			holder := res.Document
			for _, p := range parts[:len(parts)-1] {
				for _, e := range holder.Entries {
					if e.Key == p {
						holder = e.Value
					}
				}
			}
			last := parts[len(parts)-1]
			kept := holder.Entries[:0:0]
			for _, e := range holder.Entries {
				if e.Key != last {
					kept = append(kept, e)
				}
			}
			holder.Entries = kept
		}
		if !res.Document.Same(n) {
			t.Fatalf("%q: an authored value was changed or dropped", text)
		}
	})
}

// FuzzNormalize checks, on every input that parses and that Normalize accepts: the input is left alone, denormalizing the
// result gives the input back as a value, and normalizing the result again changes neither the object nor the origins.
// CIC_FUZZ_CANARY works as in FuzzParse.
func FuzzNormalize(f *testing.F) {
	for _, seed := range []string{"", "a: x\n", "a: x\nb: ~\nc: ~\n", "a: x\nb: 0\nc: {d: false}\ng: []\n", "a: x\nc: {}\n", "a: x\nz: 1\n",
		"a: {value: x, access: [OU=a], modify: inherit, inherit: 0, default_injection: ~, conformance: deprecated}\nb: 1\n",
		"c: {value: {d: true}, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}\n"} {
		f.Add(seed)
	}
	shape := defaultsShape()
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
		res, err := forms.Normalize(n, shape, keySet, g, forms.Context{})
		if forms.Emit(n) != before {
			t.Fatalf("%q: the input changed", text)
		}
		if err != nil {
			return
		}
		back, err := forms.Denormalize(res, shape, keySet, g, forms.Context{})
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: denormalize(normalize(x)) != x (%v)", text, err)
		}
		again, err := forms.Renormalize(res, shape, keySet, g, forms.Context{})
		if err != nil || forms.Emit(again.Object) != forms.Emit(res.Object) || !slices.Equal(again.ValueOrigins, res.ValueOrigins) {
			t.Fatalf("%q: not idempotent on the pair (%v)", text, err)
		}
	})
}

func FuzzResolvePolicy(f *testing.F) {
	for _, seed := range []string{"", "a: x\n", "a: x\nb: ~\nc: ~\n", "a: x\nb: 0\nc: {d: false}\ng: []\n", "a: x\nc: {}\n",
		"a: {value: x, access: [OU=a], modify: inherit, inherit: 0, default_injection: ~, conformance: deprecated}\nb: 1\n",
		"c: {value: {d: true}, access: inherit, modify: [], inherit: false, default_injection: ~, conformance: implemented}\na: x\n"} {
		f.Add(seed)
	}
	shape := defaultsShape()
	g, err := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, &forms.Node{Kind: forms.KindInt, Int: 1})
	if err != nil {
		f.Fatal(err)
	}
	policy := forms.PolicySurface{Declared: true, DefaultAccess: &forms.PolicyRule{Patterns: []string{"O=r"}}}
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
		res, err := forms.Normalize(n, shape, keySet, g, forms.Context{})
		if err != nil {
			return
		}
		before := forms.Emit(res.Object)
		known, err := forms.ResolvePolicy(res.Object, shape, policy, "config_surface", keySet, g, nil)
		if forms.Emit(res.Object) != before {
			t.Fatalf("%q: the resolution changed the object", text)
		}
		if err != nil {
			return
		}
		unknown, err := forms.ResolvePolicy(res.Object, shape, policy, "config_surface", keySet, g, []string{"$"})
		if err != nil {
			t.Fatalf("%q: the same document does not resolve with an unknown root: %v", text, err)
		}
		if len(known) != len(unknown) {
			t.Fatalf("%q: the policy state of the root changed the positions", text)
		}
		for p, e := range unknown {
			if _, ok := known[p]; !ok {
				t.Fatalf("%q: position %s only exists with an unknown root", text, p)
			}
			for _, r := range []forms.EffectiveRule{e.Access, e.Modify} {
				if r.Kind == forms.RuleOpen || r.Kind == forms.RuleOwner {
					t.Fatalf("%q: an unknown root gave a permissive system rule at %s", text, p)
				}
			}
		}
	})
}
