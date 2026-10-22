package forms_test

// NormalizeWithPolicy (docs/P3-NORMALIZE-DESIGN.md, section 2): the normalization and the resolution of its object, made from one
// compiled schema. The Rust peer is rust/tests/composed.rs.

import (
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func TestNormalizeWithPolicyIsNormalizeThenResolvePolicy(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			cs, err := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
			if err != nil {
				t.Fatal(err)
			}
			doc, _ := forms.Parse(c.YAML)
			g := bound(t, c.Grammar)
			one, err := forms.NormalizeWithPolicy(doc, cs, f.AccessKeys, g, forms.Context{}, c.Unknown)
			if err != nil {
				t.Fatal(err)
			}
			n, err := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{})
			if err != nil {
				t.Fatal(err)
			}
			r, err := forms.ResolvePolicy(n.Object, cs.Root, cs.Policy, cs.Surface, f.AccessKeys, g, c.Unknown)
			if err != nil {
				t.Fatal(err)
			}
			if forms.Emit(one.Normalization.Object) != forms.Emit(n.Object) || !slices.Equal(one.Normalization.ValueOrigins, n.ValueOrigins) {
				t.Fatal("the normalization differs from Normalize")
			}
			if !reflect.DeepEqual(one.PolicyResolution, r) {
				t.Fatal("the resolution differs from ResolvePolicy on the normalized object")
			}
			if one.SchemaDigest != cs.Digest {
				t.Fatalf("digest %s, want %s", one.SchemaDigest, cs.Digest)
			}
			// the authored document still comes back from the normalization part
			back, err := forms.Denormalize(one.Normalization, cs.Root, f.AccessKeys, g, forms.Context{})
			if err != nil || !back.Same(doc) {
				t.Fatalf("denormalize of the composed result: %v", err)
			}
		})
	}
}

func TestNormalizeWithPolicyGivesNoHalfResult(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	c := f.Cases[0]
	cs, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
	g := bound(t, 1)
	doc, _ := forms.Parse("type: t\n")
	// the resolution fails (an unknown object that does not exist): no result at all
	if r, err := forms.NormalizeWithPolicy(doc, cs, f.AccessKeys, g, forms.Context{}, []string{"$.nowhere"}); r != nil || err == nil {
		t.Fatalf("a failed resolution gave a result: %v / %v", r, err)
	}
	// the normalization fails (the document does not validate): no result at all
	bad, _ := forms.Parse("bogus: 1\n")
	if r, err := forms.NormalizeWithPolicy(bad, cs, f.AccessKeys, g, forms.Context{}, nil); r != nil || err == nil {
		t.Fatalf("a document that does not validate gave a result: %v / %v", r, err)
	}
}

func TestNormalizeWithPolicyCarriesTheSchemaItWasMadeFrom(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	g := bound(t, 1)
	a, b2 := f.Cases[0], f.Cases[1]
	if a.Schema == b2.Schema {
		t.Skip("the two cases share a schema")
	}
	ca, _ := compileDefaults(a.Schema, f.GrammarPin, "config_surface")
	cb, _ := compileDefaults(b2.Schema, f.GrammarPin, "config_surface")
	da, _ := forms.Parse("type: t\n")
	ra, err := forms.NormalizeWithPolicy(da, ca, f.AccessKeys, g, forms.Context{}, nil)
	if err != nil {
		t.Fatal(err)
	}
	rb, err := forms.NormalizeWithPolicy(da, cb, f.AccessKeys, g, forms.Context{}, nil)
	if err == nil && ra.SchemaDigest == rb.SchemaDigest {
		t.Fatal("two different schemas gave results with the same digest")
	}
}
