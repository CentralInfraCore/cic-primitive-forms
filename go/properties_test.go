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
	"errors"
	"fmt"
	"reflect"
	"regexp"
	"slices"
	"sort"
	"strings"
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

// ---------------------------------------------------------------------------
// Presence validation (ADR 0003, ADR 0007). The Rust peer checks the same properties.
//
//   - a document built to conform to a shape has no violation;
//   - leaving out one mandatory field gives only MISSING_MANDATORY errors, at most one at its path;
//   - default candidates are exactly the absent optional fields that declare a default;
//   - for any shape and any document: an unsupported answer or a response that is sorted, unique by
//     (path, code) and of one phase, and the input is left alone.
// ---------------------------------------------------------------------------

func presenceGen() *rapid.Generator[forms.Presence] {
	return rapid.SampledFrom([]forms.Presence{forms.Mandatory, forms.Optional})
}

func shapeGen(depth int) *rapid.Generator[forms.Shape] {
	return rapid.Custom(func(t *rapid.T) forms.Shape {
		s := forms.Shape{
			Presence:   presenceGen().Draw(t, "presence"),
			Nullable:   rapid.Bool().Draw(t, "nullable"),
			HasDefault: rapid.Bool().Draw(t, "default"),
		}
		kind := rapid.IntRange(0, 4).Draw(t, "kind")
		if depth == 0 && kind == 4 {
			kind = 0
		}
		switch kind {
		case 0, 1, 2:
			s.Kind, s.Scalar = forms.ShapeScalar, forms.ScalarType(kind)
		case 3:
			s.Kind = forms.ShapeList
			s.HasMinItems = rapid.Bool().Draw(t, "hasMin")
			s.MinItems = rapid.IntRange(0, 2).Draw(t, "min")
		default:
			s.Kind = forms.ShapeComposite
			names := rapid.SliceOfNDistinct(rapid.StringMatching(`[a-d]{1,2}`), 0, 3, func(s string) string { return s }).Draw(t, "names")
			sort.Strings(names)
			for _, n := range names {
				s.Fields = append(s.Fields, forms.NamedShape{Name: n, Shape: shapeGen(depth-1).Draw(t, n)})
			}
		}
		return s
	})
}

type xorshift uint64

func (x *xorshift) next() uint64 {
	*x ^= *x << 13
	*x ^= *x >> 7
	*x ^= *x << 17
	return uint64(*x)
}

func (x *xorshift) coin() bool { return x.next()%2 == 0 }

// conforming builds a node that conforms to shape; omit is a path whose field is left out.
func conforming(s forms.Shape, rng *xorshift, path []string, omit []string) *forms.Node {
	if s.Nullable && rng.coin() {
		return &forms.Node{Kind: forms.KindNull}
	}
	switch s.Kind {
	case forms.ShapeScalar:
		switch s.Scalar {
		case forms.ScalarString:
			return &forms.Node{Kind: forms.KindStr, Str: fmt.Sprintf("s%d", rng.next()%9)}
		case forms.ScalarInteger:
			return &forms.Node{Kind: forms.KindInt, Int: int64(rng.next() % 9)}
		default:
			return &forms.Node{Kind: forms.KindBool, Bool: rng.coin()}
		}
	case forms.ShapeList:
		n := int(rng.next() % 2)
		if s.HasMinItems {
			n += s.MinItems
		}
		list := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for i := 0; i < n; i++ {
			list.Items = append(list.Items, &forms.Node{Kind: forms.KindInt, Int: int64(i)})
		}
		return list
	case forms.ShapeComposite:
		m := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		for _, f := range s.Fields {
			p := append(slices.Clone(path), f.Name)
			present := !slices.Equal(p, omit) && (f.Shape.Presence == forms.Mandatory || rng.coin())
			if present {
				m.Entries = append(m.Entries, forms.Entry{Key: f.Name, Value: conforming(f.Shape, rng, p, omit)})
			}
		}
		return m
	}
	return &forms.Node{Kind: forms.KindStr, Str: "any"}
}

func mandatoryPaths(s forms.Shape, n *forms.Node, prefix []string, out *[][]string) {
	if s.Kind != forms.ShapeComposite || n.Kind != forms.KindMap {
		return
	}
	for _, f := range s.Fields {
		p := append(slices.Clone(prefix), f.Name)
		for _, e := range n.Entries {
			if e.Key == f.Name {
				if f.Shape.Presence == forms.Mandatory {
					*out = append(*out, p)
				}
				mandatoryPaths(f.Shape, e.Value, p, out)
			}
		}
	}
}

func TestPropertyAConformingDocumentHasNoViolation(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		s := shapeGen(2).Draw(t, "shape")
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		n := conforming(s, &seed, nil, nil)
		before := forms.Emit(n)
		v, err := forms.Validate(n, s, keySet)
		if err != nil || len(v.Violations) != 0 {
			t.Fatalf("%v %+v", err, v)
		}
		if forms.Emit(n) != before {
			t.Fatal("the input changed")
		}
	})
}

func TestPropertyLeavingOutOneMandatoryFieldAddsExactlyItsMissingError(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		s := shapeGen(2).Draw(t, "shape")
		start := rapid.Uint64Min(1).Draw(t, "seed")
		seed := xorshift(start)
		n := conforming(s, &seed, nil, nil)
		var paths [][]string
		mandatoryPaths(s, n, nil, &paths)
		if len(paths) == 0 {
			return // nothing to leave out in this document
		}
		omit := paths[rapid.IntRange(0, len(paths)-1).Draw(t, "pick")]
		seed = xorshift(start)
		without := conforming(s, &seed, nil, omit)
		v, err := forms.Validate(without, s, keySet)
		if err != nil {
			t.Fatal(err)
		}
		text := "$." + strings.Join(omit, ".")
		hits := 0
		for _, x := range v.Violations {
			if x.Code != "MISSING_MANDATORY" {
				t.Fatalf("unexpected %+v", x)
			}
			if x.LogicalPath == text {
				hits++
			}
		}
		if hits > 1 {
			t.Fatalf("%d errors at %s", hits, text)
		}
	})
}

func TestPropertyDefaultCandidatesAreTheAbsentOptionalFieldsWithADefault(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		s := shapeGen(2).Draw(t, "shape")
		if s.Kind != forms.ShapeComposite {
			return // only a composite has fields to leave absent
		}
		v, err := forms.Validate(&forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}, s, keySet)
		if err != nil {
			t.Fatal(err)
		}
		want := []string{}
		for _, f := range s.Fields {
			if f.Shape.Presence == forms.Optional && f.Shape.HasDefault {
				want = append(want, "$."+f.Name)
			}
		}
		sort.Strings(want)
		if !reflect.DeepEqual(v.DefaultCandidates, want) {
			t.Fatalf("%v, want %v", v.DefaultCandidates, want)
		}
	})
}

func TestPropertyAnyDocumentAgainstAnyShapeGivesAnOrderedUniqueResponse(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		s := shapeGen(2).Draw(t, "shape")
		n := nodeGen(3).Draw(t, "node")
		before := forms.Emit(n)
		v, err := forms.Validate(n, s, keySet)
		if forms.Emit(n) != before {
			t.Fatal("the input changed")
		}
		if err != nil {
			return
		}
		type key struct{ path, code string }
		seen := map[key]bool{}
		phases := map[forms.Phase]bool{}
		escaped := false
		var last []string
		for _, x := range v.Violations {
			k := key{x.LogicalPath, x.Code}
			if seen[k] {
				t.Fatalf("duplicate %+v", k)
			}
			seen[k] = true
			phases[x.Phase] = true
			escaped = escaped || strings.Contains(x.LogicalPath, `\`)
			if !escaped {
				segs := append(strings.Split(x.LogicalPath, "."), x.Code)
				if last != nil && slices.Compare(last, segs) > 0 && x.LogicalPath != "" {
					t.Fatalf("not sorted: %v after %v", segs, last)
				}
				last = segs
			}
		}
		if len(phases) > 1 {
			t.Fatal("more than one phase in one response")
		}
	})
}

// ---------------------------------------------------------------------------
// Reading access-policy/1 (cic-primitives D-018). The Rust peer checks the same properties.
//
//   - type preservation: whatever reads is written back to the same spelling, to the type, and a value
//     is never valid in both grammars (false and 0 stay two values);
//   - version binding agrees with an independently written oracle for any pair of candidates;
//   - CertPattern agrees with the upstream regular expression for any string;
//   - reading never changes its input.
// ---------------------------------------------------------------------------

func grammarOf(t interface{ Fatal(args ...any) }, v int64) forms.Grammar {
	n := &forms.Node{Kind: forms.KindInt, Int: v}
	g, err := forms.BindGrammar(n, n)
	if err != nil {
		t.Fatal(err)
	}
	return g
}

// upstreamPattern is the upstream PATTERN_RE with Python's \s spelled out: the Unicode White_Space
// property (\p{Z}, the controls U+0009..U+000D and U+0085) and U+001C..U+001F.
var upstreamPattern = func() *regexp.Regexp {
	pair := `[A-Za-z][A-Za-z0-9.-]*=[^=,\p{Z}\t-\r\x{85}\x{1c}-\x{1f}][^=,]*`
	return regexp.MustCompile("^" + pair + "(," + pair + ")*$")
}()

func candidateGen() *rapid.Generator[*forms.Node] {
	return rapid.OneOf(
		rapid.Just((*forms.Node)(nil)),
		rapid.Just(&forms.Node{Kind: forms.KindNull}),
		rapid.Map(rapid.Int64Range(-2, 4), func(n int64) *forms.Node { return &forms.Node{Kind: forms.KindInt, Int: n} }),
		rapid.Map(rapid.Bool(), func(b bool) *forms.Node { return &forms.Node{Kind: forms.KindBool, Bool: b} }),
		rapid.Map(rapid.StringMatching(`[0-9a-z]{0,2}`), func(s string) *forms.Node { return &forms.Node{Kind: forms.KindStr, Str: s} }),
		rapid.Just(&forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}),
	)
}

// oracle: missing, then unknown, then conflict - written without the library's helpers.
func oracle(c, p *forms.Node) (int64, string) {
	missing := func(n *forms.Node) bool { return n == nil || n.Kind == forms.KindNull }
	if missing(c) || missing(p) {
		return 0, "GRAMMAR_VERSION_MISSING"
	}
	known := func(n *forms.Node) bool { return n.Kind == forms.KindInt && (n.Int == 1 || n.Int == 2) }
	switch {
	case !known(c) || !known(p):
		return 0, "UNKNOWN_GRAMMAR_VERSION"
	case c.Int != p.Int:
		return 0, "GRAMMAR_VERSION_CONFLICT"
	}
	return c.Int, ""
}

func TestPropertyWhatReadsIsWrittenBackToTheSameSpelling(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := nodeGen(2).Draw(t, "node")
		v := rapid.Int64Range(1, 2).Draw(t, "version")
		g := grammarOf(t, v)
		if mode, viol := forms.ReadMode(n, g); viol == nil && !forms.WriteMode(mode, g).Same(n) {
			t.Fatalf("%+v was read as %v and written back differently", n, mode)
		}
		_, v1 := forms.ReadMode(n, grammarOf(t, 1))
		_, v2 := forms.ReadMode(n, grammarOf(t, 2))
		if v1 == nil && v2 == nil {
			t.Fatalf("%+v is a valid mode in both grammars", n)
		}
	})
}

func TestPropertyEveryModeHasOneSpellingPerVersionAndReadsBack(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		mode := forms.Mode(rapid.IntRange(0, 2).Draw(t, "mode"))
		v := rapid.Int64Range(1, 2).Draw(t, "version")
		g := grammarOf(t, v)
		w := forms.WriteMode(mode, g)
		if back, viol := forms.ReadMode(w, g); viol != nil || back != mode {
			t.Fatalf("%v: %v %v", mode, back, viol)
		}
		if _, viol := forms.ReadMode(w, grammarOf(t, 3-v)); viol == nil {
			t.Fatal("a spelling is valid in the other grammar")
		}
	})
}

func TestPropertyBindingAgreesWithTheOracle(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		c, p := candidateGen().Draw(t, "context"), candidateGen().Draw(t, "pinned")
		g, err := forms.BindGrammar(c, p)
		want, wantCode := oracle(c, p)
		if wantCode == "" {
			if err != nil || g.Version() != want {
				t.Fatalf("%v %v, want %d", g, err, want)
			}
			return
		}
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Code() != wantCode {
			t.Fatalf("%v, want %s", err, wantCode)
		}
	})
}

func TestPropertyCertPatternAgreesWithTheUpstreamExpression(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		s := rapid.OneOf(
			rapid.String(),
			rapid.StringMatching("[A-Za-z0-9.=,  \u001c\t*-]{0,14}"),
			rapid.StringMatching(`[A-Za-z][A-Za-z0-9.-]{0,3}=[A-Za-z0-9*]{1,3}(,[A-Za-z][A-Za-z0-9.-]{0,3}=[A-Za-z0-9*]{1,3}){0,2}`),
		).Draw(t, "pattern")
		if got, want := forms.IsCertPattern(s), upstreamPattern.MatchString(s); got != want {
			t.Fatalf("%q: library %v, upstream expression %v", s, got, want)
		}
	})
}

func TestPropertyReadingNeverChangesItsInput(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := nodeGen(2).Draw(t, "node")
		before := forms.Emit(n)
		_, _ = forms.ReadRule(n)
		_, _ = forms.ReadMode(n, grammarOf(t, 1))
		_, _ = forms.ReadMode(n, grammarOf(t, 2))
		if forms.Emit(n) != before {
			t.Fatal("the input changed")
		}
	})
}
