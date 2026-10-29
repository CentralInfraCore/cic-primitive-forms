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
	"bytes"
	"errors"
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

func FuzzSourceSpan(f *testing.F) {
	for _, seed := range []string{"", "a: x\n", "a: 'x'\nb: 3\n", "a: \"x\"\nc:\n  d: y\n  e: true\n", "a: x # c\n", "a: |\n  x\n", "a: x\n  y\n", "c: {d: y}\n",
		"a:\n  value: x\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n",
		"a: --- ](e-+.(-f", "a: ---\n", "a: ... x\n"} { // values that alone would be document markers (#87)
		f.Add(seed)
	}
	str := func() forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional}
	}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "a", Shape: str()},
		{Name: "b", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}},
		{Name: "c", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
			{Name: "d", Shape: str()},
			{Name: "e", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean, Presence: forms.Optional}},
		}}},
	}}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		if _, err := forms.Parse(text); err != nil {
			return
		}
		for _, path := range []string{"$.a", "$.b", "$.c", "$.c.d", "$.c.e"} {
			span, err := forms.SourceSpan(text, root, path, keySet)
			if err == nil {
				if span.Start >= span.End || span.End > len(text) {
					t.Fatalf("%s: a span outside the text in %q", path, text)
				}
				token := text[span.Start:span.End]
				if !utf8.ValidString(token) || strings.ContainsAny(token, "\r\n") {
					t.Fatalf("%s: the span %q is not a whole single line in %q", path, token, text)
				}
				// as the value of a mapping entry, the context it came from: alone, "--- x" would be a document marker (#87)
				if _, err := forms.Parse("k: " + token); err != nil {
					t.Fatalf("%s: the span %q does not parse as a value", path, token)
				}
				continue
			}
			var fe *forms.Error
			if errors.As(err, &fe) && fe.Kind == forms.ErrContract && fe.Violations[0].Code == "EDIT_SPAN_MISMATCH" {
				t.Fatalf("%s: the read-back refused in %q", path, text)
			}
		}
	})
}

// FuzzEditScalar (P3b, step E3): on every text that parses, for each scalar position of a fixed shape and a value of the kind the position has
// (a string from the second input, a number and a boolean from its length), the call returns an edit or a refusal of the contract and never
// panics; a refusal is never EDIT_SPAN_MISMATCH or EDIT_INVALID_RESULT (here a defect); an edit keeps the bytes outside the span, parses to
// the old tree with that one scalar replaced, and an unchanged edit is the input byte for byte.
func FuzzEditScalar(f *testing.F) {
	for _, seed := range []struct{ text, val string }{
		{"", ""}, {"a: x\n", "y"}, {"a: 'x'\nb: 3\n", "it's"}, {"a: \"x\"\nc:\n  d: y\n  e: true\n", "say \"hi\""}, {"a: x # c\n", "z"},
		{"a: |\n  x\n", "y"}, {"a: x\n  y\n", "z"}, {"c: {d: y}\n", "w"}, {"a: ~\n", "q"}, {"b: +5\n", "123"}, {"a: x\r\nb: 1\r\n", "yes"},
		{"a:\n  value: x\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n", "v"},
	} {
		f.Add(seed.text, seed.val)
	}
	str := func() forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, Nullable: true}
	}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "a", Shape: str()},
		{Name: "b", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}},
		{Name: "c", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
			{Name: "d", Shape: str()},
			{Name: "e", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean, Presence: forms.Optional}},
		}}},
	}}
	f.Fuzz(func(t *testing.T, text, val string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) || !utf8.ValidString(val) {
			return
		}
		if _, err := forms.Parse(text); err != nil {
			return
		}
		values := map[string]*forms.Node{
			"$.a":   {Kind: forms.KindStr, Str: val},
			"$.b":   {Kind: forms.KindInt, Int: int64(len(val)) - 3},
			"$.c.d": {Kind: forms.KindStr, Str: val},
			"$.c.e": {Kind: forms.KindBool, Bool: len(val)%2 == 0},
		}
		for _, path := range []string{"$.a", "$.b", "$.c.d", "$.c.e"} {
			span, serr := forms.SourceSpan(text, root, path, keySet)
			res, eerr := forms.EditScalar(text, root, path, keySet, values[path])
			if eerr != nil {
				var fe *forms.Error
				if errors.As(eerr, &fe) && fe.Kind == forms.ErrContract {
					switch fe.Violations[0].Code {
					case "EDIT_SPAN_MISMATCH", "EDIT_INVALID_RESULT":
						t.Fatalf("%s: %s for %q <- %q", path, fe.Violations[0].Code, text, val)
					}
				}
				continue
			}
			if serr != nil {
				t.Fatalf("%s: an edit where the span was refused: %v in %q", path, serr, text)
			}
			if !strings.HasPrefix(res.Text, text[:span.Start]) || !strings.HasSuffix(res.Text, text[span.End:]) {
				t.Fatalf("%s: the untouched bytes changed: %q -> %q", path, text, res.Text)
			}
			if !res.Changed && res.Text != text {
				t.Fatalf("%s: an unchanged edit rewrote %q to %q", path, text, res.Text)
			}
			if _, perr := forms.Parse(res.Text); perr != nil {
				t.Fatalf("%s: the result does not parse: %v in %q", path, perr, res.Text)
			}
		}
	})
}

// FuzzDiff (P4, step D3b): two texts, both parsed; the diff of any pair is a result or a refusal of the contract, never a panic, and when it is a result the
// views are equal to themselves, the diff turned round has as many events, and the bytes are the canonical writing. The Rust peer is rust/fuzz/fuzz_targets/diff.rs.
func FuzzDiff(f *testing.F) {
	for _, seed := range []struct{ a, b string }{
		{"", ""}, {"a: x\n", "a: y\n"}, {"a: x\nb: 1\n", "b: 2\n"}, {"s: [x, y]\n", "s: [y, z]\n"}, {"l:\n  - {k: a, v: 1}\n", "l:\n  - {k: b, v: 1}\n  - {k: a, v: 2}\n"},
		{"c: {d: x, e: true}\n", "c: {d: x, e: false}\n"}, {"a:\n  value: x\n  access: [OU=a]\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n", "a: x\n"},
		{"a: ~\n", "a: ''\n"}, {"b: 1\n", "b: true\n"}, {"l:\n  - {k: a}\n  - {k: a}\n", "a: x\n"},
	} {
		f.Add(seed.a, seed.b)
	}
	str := func() forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, Nullable: true}
	}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{
		{Name: "a", Shape: str()},
		{Name: "b", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}},
		{Name: "c", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
			{Name: "d", Shape: str()},
			{Name: "e", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean, Presence: forms.Optional}},
		}}},
		{Name: "s", Shape: forms.Shape{Kind: forms.ShapeSet, Scalar: forms.ScalarString, Presence: forms.Optional}},
		{Name: "l", Shape: forms.Shape{Kind: forms.ShapeDeclaredList, Presence: forms.Optional, Elements: &forms.Elements{Cases: []forms.Case{{
			Name: "e", Key: []string{"k"}, Fields: []forms.NamedShape{
				{Name: "k", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Mandatory}},
				{Name: "v", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}}}}}}}},
	}}
	f.Fuzz(func(t *testing.T, a, b string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(a, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		da, errA := forms.Parse(a)
		db, errB := forms.Parse(b)
		if errA != nil || errB != nil {
			return
		}
		g, gerr := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, &forms.Node{Kind: forms.KindInt, Int: 1})
		if gerr != nil {
			t.Fatal(gerr)
		}
		diff := func(x, y *forms.Node) (*forms.DiffResult, error) {
			return forms.Diff(forms.DiffView{Doc: x}, forms.DiffView{Doc: y}, root, keySet, g, nil)
		}
		ab, err := diff(da, db)
		if err != nil {
			var fe *forms.Error
			if errors.As(err, &fe) && fe.Kind == forms.ErrContract && fe.Violations[0].Code == "DIFF_UNSUPPORTED_VALUE" {
				t.Fatalf("an unsupported number from a parsed text: %q %q", a, b)
			}
			return
		}
		ba, err := diff(db, da)
		if err != nil {
			t.Fatalf("A to B was a result and B to A is %v: %q %q", err, a, b)
		}
		if len(ab.Changes) != len(ba.Changes) || len(ab.Representation) != len(ba.Representation) {
			t.Fatalf("the diff turned round has other events: %q %q", a, b)
		}
		aa, err := diff(da, da)
		if err != nil || len(aa.Changes) != 0 || len(aa.Representation) != 0 {
			t.Fatalf("A against A: %v %q", err, a)
		}
		if got := ab.Canonical(); !utf8.ValidString(got) || !strings.HasPrefix(got, `{"changes":[`) || !strings.HasSuffix(got, `"version":1}`) {
			t.Fatalf("not the canonical writing: %q", got)
		}
	})
}

// FuzzPatch (P4, step P4-3): two texts, both parsed; the diff of A to B is applied to the text of A. The patch is a result or a refusal of the contract and never a
// panic, never a conflict or an absence (the diff was made from the same text); a result parses, the diff of the result to B has no changes (ADR 0013, S+), it
// replaced one scalar per event, the same diff on the result is refused, and the empty diff gives A back. The Rust peer is rust/fuzz/fuzz_targets/patch.rs.
func FuzzPatch(f *testing.F) {
	for _, seed := range []struct{ a, b string }{
		{"", ""}, {"a: x\n", "a: y\n"}, {"a: x\nb: 1\n", "a: z\nb: 2\n"}, {"a: 'x'\nb: 3\n", "a: it's\nb: 3\n"}, {"a: x # c\r\nb: 1\r\n", "a: y\nb: 1\n"},
		{"c:\n  d: x\n  e: true\n", "c:\n  d: y\n  e: false\n"}, {"l:\n  - k: a\n    v: 1\n", "l:\n  - k: a\n    v: 2\n"}, {"a: |\n  x\n", "a: y\n"},
		{"s: [x]\n", "s: [y]\n"}, {"a: x\n", "a: x\nb: 1\n"}, {"c: {d: x}\n", "c: {d: y}\n"},
	} {
		f.Add(seed.a, seed.b)
	}
	str := func() forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, Nullable: true}
	}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{
		{Name: "a", Shape: str()},
		{Name: "b", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}},
		{Name: "c", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
			{Name: "d", Shape: str()},
			{Name: "e", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarBoolean, Presence: forms.Optional}},
		}}},
		{Name: "s", Shape: forms.Shape{Kind: forms.ShapeSet, Scalar: forms.ScalarString, Presence: forms.Optional}},
		{Name: "l", Shape: forms.Shape{Kind: forms.ShapeDeclaredList, Presence: forms.Optional, Elements: &forms.Elements{Cases: []forms.Case{{
			Name: "e", Key: []string{"k"}, Fields: []forms.NamedShape{
				{Name: "k", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Mandatory}},
				{Name: "v", Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarInteger, Presence: forms.Optional}}}}}}}},
	}}
	f.Fuzz(func(t *testing.T, a, b string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(a, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		da, errA := forms.Parse(a)
		db, errB := forms.Parse(b)
		if errA != nil || errB != nil {
			return
		}
		g, gerr := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, &forms.Node{Kind: forms.KindInt, Int: 1})
		if gerr != nil {
			t.Fatal(gerr)
		}
		ctx := forms.DiffContext{}
		d, err := forms.Diff(forms.DiffView{Doc: da}, forms.DiffView{Doc: db}, root, keySet, g, nil)
		if err != nil {
			return
		}
		pd := forms.PatchDiff{Version: 1, Changes: d.Changes}
		res, err := forms.Patch(a, root, keySet, g, ctx, ctx, pd)
		if err != nil {
			var fe *forms.Error
			if errors.As(err, &fe) && fe.Kind == forms.ErrContract {
				switch fe.Violations[0].Code {
				case "PATCH_CONFLICT", "PATCH_TARGET_ABSENT", "PATCH_CONTEXT_MISMATCH", "EDIT_SPAN_MISMATCH", "EDIT_INVALID_RESULT":
					t.Fatalf("%s for a diff made from the same text: %q -> %q", fe.Violations[0].Code, a, b)
				}
			}
			return
		}
		patched, perr := forms.Parse(res.Text)
		if perr != nil {
			t.Fatalf("the result does not parse: %v in %q", perr, res.Text)
		}
		rt, err := forms.Diff(forms.DiffView{Doc: patched}, forms.DiffView{Doc: db}, root, keySet, g, nil)
		if err != nil || len(rt.Changes) != 0 {
			t.Fatalf("the diff of the result to B has changes or fails (%v): %q -> %q gives %q", err, a, b, res.Text)
		}
		if len(res.Replaced) != len(d.Changes) {
			t.Fatalf("replaced %v for %d events", res.Replaced, len(d.Changes))
		}
		if _, err := forms.Patch(res.Text, root, keySet, g, ctx, ctx, pd); len(d.Changes) > 0 && err == nil {
			t.Fatalf("a replay was accepted: %q", res.Text)
		}
		if empty, err := forms.Patch(a, root, keySet, g, ctx, ctx, forms.PatchDiff{Version: 1}); err != nil || empty.Text != a {
			t.Fatalf("the empty diff did not give A back: %v", err)
		}
	})
}

// FuzzReadDiff (P4, after #107 and the differential): the bytes handed to ReadDiff. The reader never panics; a refusal names a reason of the closed set and an offset
// that is not beyond the input, and ReadDiffVersion refuses with the same reason and offset when it refuses at all, and names a version other than 1 exactly when
// ReadDiff refuses with the reason "version". A diff that is read is valid UTF-8, has version 1, is written back as the bytes it was read from, reads again to the
// same writing, and gives the patch as many events as it has changes. CIC_FUZZ_CANARY works as in FuzzParse. The Rust peer is rust/fuzz/fuzz_targets/read_diff.rs.
func FuzzReadDiff(f *testing.F) {
	for _, seed := range []string{
		`{"changes":[],"representation":[],"version":1}`,
		`{"changes":[{"kind":"value_change","next":1600,"path":"$.mtu","previous":1500}],"representation":[],"version":1}`,
		`{"changes":[{"changed":{"access":{"next":["OU=ops"],"previous":"inherit"}},"kind":"policy_declaration_change","path":"$.mtu"}],"representation":[{"kind":"representation_change","next":"expanded","path":"$.mtu","previous":"short"}],"version":1}`,
		`{"changes":[{"added":[2],"kind":"set_change","path":"$.tags","removed":[]}],"representation":[],"version":1}`,
		`{"changes":[],"representation":[],"version":2}`,
		`{"changes":[],"representation":null,"version":1}`, `{}`, ``, "\xff",
	} {
		f.Add([]byte(seed))
	}
	reasons := []string{forms.ReadNotUTF8, forms.ReadSyntax, forms.ReadDepth, forms.ReadDuplicateKey, forms.ReadNumber, forms.ReadShape, forms.ReadPath, forms.ReadOrder,
		forms.ReadNoChange, forms.ReadNotCanonical, forms.ReadVersion}
	f.Fuzz(func(t *testing.T, data []byte) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && bytes.HasPrefix(data, []byte("CANARY")) {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		version, verr := forms.ReadDiffVersion(data)
		d, err := forms.ReadDiff(data)
		if err != nil {
			var re *forms.DiffReadError
			if !errors.As(err, &re) || !slices.Contains(reasons, re.Reason) || re.Offset < 0 || re.Offset > len(data) {
				t.Fatalf("a refusal outside the contract: %v %q", err, data)
			}
			if verr != nil {
				var ve *forms.DiffReadError
				if !errors.As(verr, &ve) || ve.Reason != re.Reason || ve.Offset != re.Offset {
					t.Fatalf("ReadDiff refuses with %v and ReadDiffVersion with %v: %q", err, verr, data)
				}
			} else if (version != 1) != (re.Reason == forms.ReadVersion) {
				t.Fatalf("version %d and the reason %s: %q", version, re.Reason, data)
			}
			return
		}
		if verr != nil || version != 1 || d.Version != 1 || !utf8.Valid(data) {
			t.Fatalf("a diff was read and the version is %d (%v), valid UTF-8 %v: %q", version, verr, utf8.Valid(data), data)
		}
		if got := len(d.PatchDiff().Changes); got != len(d.Changes) {
			t.Fatalf("%d events for the patch, %d changes: %q", got, len(d.Changes), data)
		}
		written := (&forms.DiffResult{Changes: d.Changes, Representation: d.Representation}).Canonical()
		if written != string(data) {
			t.Fatalf("not written back as it was read: %q %q", data, written)
		}
		again, err := forms.ReadDiff([]byte(written))
		if err != nil {
			t.Fatalf("the writing of a diff does not read again: %v %q", err, written)
		}
		if twice := (&forms.DiffResult{Changes: again.Changes, Representation: again.Representation}).Canonical(); twice != written {
			t.Fatalf("read twice, written differently: %q %q", written, twice)
		}
	})
}
