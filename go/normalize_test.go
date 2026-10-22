package forms_test

// The shared normalize cases (testdata/p1/normalize.json): Normalize is Expand plus the declared defaults, with the origin of
// every value kept beside the object, and Denormalize gives the authored document back. The Rust peer is rust/tests/normalize.rs.

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type normalizeFile struct {
	GrammarPin string   `json:"grammar_pin"`
	AccessKeys []string `json:"access_keys"`
	Schema     string   `json:"schema"`
	Surface    string   `json:"surface"`
	Documents  []struct {
		ID         string            `json:"id"`
		Grammar    int64             `json:"grammar"`
		YAML       string            `json:"yaml"`
		Origins    [][]string        `json:"origins"`
		Provenance [][]any           `json:"provenance"`
		Inserted   map[string]string `json:"inserted"`
	} `json:"documents"`
	Attested struct {
		YAML         string   `json:"yaml"`
		Attested     []string `json:"attested"`
		Denormalized string   `json:"denormalized"`
	} `json:"attested"`
}

func loadNormalize(t *testing.T) (normalizeFile, *forms.CompiledSchema) {
	t.Helper()
	b, err := os.ReadFile("../testdata/p1/normalize.json")
	if err != nil {
		t.Fatal(err)
	}
	var f normalizeFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	c, err := compileDefaults(f.Schema, f.GrammarPin, f.Surface)
	if err != nil {
		t.Fatal(err)
	}
	return f, c
}

func bound(t *testing.T, v int64) forms.Grammar {
	t.Helper()
	n := &forms.Node{Kind: forms.KindInt, Int: v}
	g, err := forms.BindGrammar(n, n)
	if err != nil {
		t.Fatal(err)
	}
	return g
}

func originPairs(res *forms.NormalizationResult) [][]string {
	var out [][]string
	for _, o := range res.ValueOrigins {
		out = append(out, []string{o.Path, o.Origin})
	}
	return out
}

func provPairs(p forms.Provenance) [][]any {
	var out [][]any
	for k, v := range p {
		out = append(out, []any{k, v.AuthoredExpanded})
	}
	slices.SortFunc(out, func(a, b []any) int { return strings.Compare(a[0].(string), b[0].(string)) })
	return out
}

func TestSharedNormalize(t *testing.T) {
	f, c := loadNormalize(t)
	for _, d := range f.Documents {
		t.Run(d.ID, func(t *testing.T) {
			doc, err := forms.Parse(d.YAML)
			if err != nil {
				t.Fatal(err)
			}
			before, g := forms.Emit(doc), bound(t, d.Grammar)
			res, err := forms.Normalize(doc, c.Root, f.AccessKeys, g, forms.Context{})
			if err != nil {
				t.Fatal(err)
			}
			if forms.Emit(doc) != before {
				t.Fatal("the input was modified")
			}
			if got := originPairs(res); !reflect.DeepEqual(got, d.Origins) {
				t.Fatalf("origins\n got %v\nwant %v", got, d.Origins)
			}
			want := d.Provenance
			slices.SortFunc(want, func(a, b []any) int { return strings.Compare(a[0].(string), b[0].(string)) })
			if got := provPairs(res.Provenance); !reflect.DeepEqual(got, want) {
				t.Fatalf("provenance\n got %v\nwant %v", got, want)
			}
			text := forms.Emit(res.Object)
			for key, atom := range d.Inserted {
				if !strings.Contains(text, atom) {
					t.Fatalf("the inserted %s is not\n%s\nin\n%s", key, atom, text)
				}
			}
			// an inserted position has no provenance: the author did not write it
			for _, o := range res.ValueOrigins {
				if _, has := res.Provenance[o.Path]; has == (o.Origin == forms.ValueOriginDefault) {
					t.Fatalf("%s: origin %s but provenance entry %v", o.Path, o.Origin, has)
				}
			}
			// the round trip: the authored document comes back, as a value
			back, err := forms.Denormalize(res, c.Root, f.AccessKeys, g, forms.Context{})
			if err != nil || !back.Same(doc) {
				t.Fatalf("denormalize(normalize(x)) != x (%v):\n%s\nwant\n%s", err, forms.Emit(back), before)
			}
			// idempotence is a property of the pair
			again, err := forms.Renormalize(res, c.Root, f.AccessKeys, g, forms.Context{})
			if err != nil || forms.Emit(again.Object) != text || !reflect.DeepEqual(originPairs(again), d.Origins) ||
				!reflect.DeepEqual(provPairs(again.Provenance), provPairs(res.Provenance)) {
				t.Fatalf("not idempotent on the pair: %v", err)
			}
			// ... and not of the object alone: normalizing the bare object calls every inserted value authored
			bare, err := forms.Normalize(res.Object, c.Root, f.AccessKeys, g, forms.Context{})
			if err != nil || forms.Emit(bare.Object) != text {
				t.Fatalf("the bare object changed: %v", err)
			}
			for _, o := range bare.ValueOrigins {
				if o.Origin != forms.ValueOriginAuthored {
					t.Fatalf("%s is %s in a bare object", o.Path, o.Origin)
				}
			}
			// the defaulted positions are the default candidates of the validation (a short document only)
			if v, err := forms.Validate(doc, c.Root, f.AccessKeys); err == nil {
				var defaulted []string
				for _, o := range res.ValueOrigins {
					if o.Origin == forms.ValueOriginDefault {
						defaulted = append(defaulted, o.Path)
					}
				}
				if !sameSet(defaulted, v.DefaultCandidates) {
					t.Fatalf("defaulted %v, candidates %v", defaulted, v.DefaultCandidates)
				}
			}
		})
	}
}

func TestNormalizeIsIndependentOfListOrder(t *testing.T) {
	f, c := loadNormalize(t)
	g := bound(t, 1)
	run := func(y string) *forms.NormalizationResult {
		doc, _ := forms.Parse(y)
		res, err := forms.Normalize(doc, c.Root, f.AccessKeys, g, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		return res
	}
	a := run("type: t\nroutes:\n  - to: b\n    metric: 0\n  - to: a\n    via: g\n")
	b := run("type: t\nroutes:\n  - to: a\n    via: g\n  - to: b\n    metric: 0\n")
	if !reflect.DeepEqual(originPairs(a), originPairs(b)) || !reflect.DeepEqual(provPairs(a.Provenance), provPairs(b.Provenance)) {
		t.Fatal("reordering the list elements changed the origins or the provenance")
	}
}

func TestNormalizeRefusesWhatExpandRefuses(t *testing.T) {
	f, c := loadNormalize(t)
	doc, _ := forms.Parse("type: t\nbogus: 1\n")
	res, err := forms.Normalize(doc, c.Root, f.AccessKeys, bound(t, 1), forms.Context{})
	var e *forms.Error
	if res != nil || !errors.As(err, &e) || e.Kind != forms.ErrContract || e.Violations[0].Code != "UNKNOWN_FIELD" {
		t.Fatalf("got %v / %v", res, err)
	}
	if _, err := forms.Normalize(doc, c.Root, f.AccessKeys, bound(t, 1), forms.Context{Origin: "materialized"}); err == nil {
		t.Fatal("a materialized document without its attestation was normalized")
	}
}

func TestNormalizeNeverDefaultsAMandatoryFieldOrAbsentStructure(t *testing.T) {
	str := func(p forms.Presence, d *forms.Node) forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: p, HasDefault: d != nil, Default: d}
	}
	x := &forms.Node{Kind: forms.KindStr, Str: "x"}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{
		{Name: "must", Shape: str(forms.Mandatory, x)}, // a hand-built shape: the compiler never produces this
		{Name: "box", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{{Name: "in", Shape: str(forms.Optional, x)}}}},
	}}
	doc, _ := forms.Parse("{}\n")
	res, err := forms.Normalize(doc, root, keySet, bound(t, 1), forms.Context{})
	if err != nil || len(res.ValueOrigins) != 0 || forms.Emit(res.Object) != "{}\n" {
		t.Fatalf("got %v / %q", err, forms.Emit(res.Object))
	}
}

func TestDenormalizeKeepsAnAttestedPositionExpanded(t *testing.T) {
	f, c := loadNormalize(t)
	g := bound(t, 1)
	ctx := forms.Context{Origin: forms.OriginMaterialized, Attestation: "att", AttestedPositions: f.Attested.Attested}
	doc, _ := forms.Parse(f.Attested.YAML)
	res, err := forms.Normalize(doc, c.Root, f.AccessKeys, g, ctx)
	if err != nil {
		t.Fatal(err)
	}
	back, err := forms.Denormalize(res, c.Root, f.AccessKeys, g, ctx)
	if err != nil || forms.Emit(back) != f.Attested.Denormalized {
		t.Fatalf("got %v:\n%s\nwant\n%s", err, forms.Emit(back), f.Attested.Denormalized)
	}
}

func TestNormalizeAndDenormalizeCheckTheContext(t *testing.T) {
	f, c := loadNormalize(t)
	g := bound(t, 1)
	doc, _ := forms.Parse("type: t\n")
	bad := forms.Context{Origin: forms.OriginMaterialized} // materialized without its attestation and positions
	if res, err := forms.Normalize(doc, c.Root, f.AccessKeys, g, bad); res != nil || err == nil {
		t.Fatalf("a contradictory context was normalized: %v / %v", res, err)
	}
	res, err := forms.Normalize(doc, c.Root, f.AccessKeys, g, forms.Context{})
	if err != nil {
		t.Fatal(err)
	}
	if back, err := forms.Denormalize(res, c.Root, f.AccessKeys, g, bad); back != nil || err == nil {
		t.Fatalf("a contradictory context was denormalized: %v / %v", back, err)
	}
	if again, err := forms.Renormalize(res, c.Root, f.AccessKeys, g, bad); again != nil || err == nil {
		t.Fatalf("a contradictory context was renormalized: %v / %v", again, err)
	}
}

func TestRenormalizeOfAResultThatDoesNotKnowAPositionCallsItAuthored(t *testing.T) {
	f, c := loadNormalize(t)
	g := bound(t, 1)
	doc, _ := forms.Parse("type: t\n")
	res, _ := forms.Normalize(doc, c.Root, f.AccessKeys, g, forms.Context{})
	// a result with only the object: nothing is known about who wrote what
	bare := &forms.NormalizationResult{Object: res.Object}
	again, err := forms.Renormalize(bare, c.Root, f.AccessKeys, g, forms.Context{})
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range again.ValueOrigins {
		if o.Origin != forms.ValueOriginAuthored {
			t.Fatalf("%s is %s", o.Path, o.Origin)
		}
	}
	if len(again.Provenance) != len(again.ValueOrigins) {
		t.Fatalf("every authored position has a provenance entry: %d != %d", len(again.Provenance), len(again.ValueOrigins))
	}
	// an object the schema does not allow is refused, not repaired
	broken, _ := forms.Parse("bogus: 1\n")
	if r, err := forms.Renormalize(&forms.NormalizationResult{Object: broken}, c.Root, f.AccessKeys, g, forms.Context{}); r != nil || err == nil {
		t.Fatalf("got %v / %v", r, err)
	}
}

func TestDenormalizeDoesNotDependOnTheOrderOfTheObject(t *testing.T) {
	f, c := loadNormalize(t)
	g := bound(t, 1)
	doc, _ := forms.Parse("type: t\nlabel: y\n")
	res, _ := forms.Normalize(doc, c.Root, f.AccessKeys, g, forms.Context{})
	// the inserted values first, the authored ones after: an entry to be removed is not the last one
	slices.Reverse(res.Object.Entries)
	back, err := forms.Denormalize(res, c.Root, f.AccessKeys, g, forms.Context{})
	if err != nil || !back.Same(doc) {
		t.Fatalf("got %v:\n%s", err, forms.Emit(back))
	}
}

func TestADefaultIsAWholeValueNothingBelowItIsDefaultedOrAuthored(t *testing.T) {
	str := func(d *forms.Node) forms.Shape {
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, HasDefault: d != nil, Default: d}
	}
	inner := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, HasDefault: true,
		Default: &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}},
		Fields:  []forms.NamedShape{{Name: "in", Shape: str(&forms.Node{Kind: forms.KindStr, Str: "x"})}}}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{{Name: "box", Shape: inner}}}
	doc, _ := forms.Parse("{}\n")
	res, err := forms.Normalize(doc, root, keySet, bound(t, 1), forms.Context{})
	if err != nil {
		t.Fatal(err)
	}
	if got := originPairs(res); !reflect.DeepEqual(got, [][]string{{"$.box", "default"}}) || len(res.Provenance) != 0 {
		t.Fatalf("origins %v provenance %v", got, res.Provenance)
	}
	if text := forms.Emit(res.Object); strings.Contains(text, "in:") {
		t.Fatalf("a field inside an inserted default was defaulted:\n%s", text)
	}
}

// A field whose name cannot be written as an ADR 0006 path (a control character) has no key for its origin: a default must not
// be inserted there, and both operations say so the same way as Expand does for a written one, instead of inserting a value that
// Denormalize could not take out again. The Rust peer is `an_unwritable_name_gets_no_default` in rust/tests/normalize.rs.
func TestAnUnwritableNameGetsNoDefault(t *testing.T) {
	name := "a" + string(rune(7)) + "b" // built, not written: a control character in a source file is easy to lose
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{{Name: name,
		Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, HasDefault: true,
			Default: &forms.Node{Kind: forms.KindStr, Str: "x"}}}}}
	doc, _ := forms.Parse("{}\n")
	var e *forms.Error
	res, err := forms.Normalize(doc, root, keySet, bound(t, 1), forms.Context{})
	if res != nil || !errors.As(err, &e) || e.Kind != forms.ErrUnsupported {
		t.Fatalf("normalize: %v / %v", res, err)
	}
	applied, err := forms.ApplyDefaults(doc, root, keySet)
	if applied != nil || !errors.As(err, &e) || e.Kind != forms.ErrUnsupported {
		t.Fatalf("apply defaults: %v / %v", applied, err)
	}
	// when the field is there the position is the author's and Expand refuses it as before
	present, _ := forms.Parse("\"" + `a\a` + "b\": y\n")
	if r, err := forms.Normalize(present, root, keySet, bound(t, 1), forms.Context{}); r != nil || err == nil {
		t.Fatalf("a written unwritable name was normalized: %v / %v", r, err)
	}
}
