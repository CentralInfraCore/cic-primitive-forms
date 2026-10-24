package forms_test

// NormalizeWithPolicy (docs/P3-NORMALIZE-DESIGN.md, section 2): the normalization and the resolution of its object, made from one
// compiled schema. The Rust peer is rust/tests/composed.rs.

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

// Normalize, ResolvePolicy and NormalizeWithPolicy start with the validation (#88): a value of the wrong type, also inside an
// expanded wrapper, and two list elements of one identity are refused as Validate refuses them, and nothing is made; a key set
// without access, modify or inherit is an error and not a panic. The schema is that of a shared resolution case (a mandatory type,
// an integer metric, routes keyed by to). The Rust peer is `the_boundaries_validate_first` in rust/tests/composed.rs.
func TestTheBoundariesValidateFirst(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	var schema string
	for _, c := range f.Cases {
		if c.ID == "an-absent-field-gets-no-entry-and-an-inserted-default-does" {
			schema = c.Schema
		}
	}
	cs, err := compileDefaults(schema, f.GrammarPin, "config_surface")
	if err != nil {
		t.Fatal(err)
	}
	g := bound(t, 1)
	wrapper := "{value: abc, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}"
	for _, c := range []struct{ yaml, want string }{
		{"type: t\nroutes:\n  - to: a\n    metric: abc\n", "value where"},
		{"type: t\nroutes:\n  - to: a\n    metric: " + wrapper + "\n", "value where"},
		{"type: t\nroutes:\n  - to: a\n  - to: a\n", "PATH_DUPLICATE_IDENTITY"},
		{"routes: []\n", "MISSING_MANDATORY"},
	} {
		doc, err := forms.Parse(c.yaml)
		if err != nil {
			t.Fatal(err)
		}
		refused := func(step string, res any, err error) {
			t.Helper()
			var e *forms.Error
			if !errors.As(err, &e) || !strings.Contains(err.Error(), c.want) || !reflect.ValueOf(res).IsNil() {
				t.Errorf("%q, %s: want a refusal with %s and no result, got %v / %v", c.yaml, step, c.want, res, err)
			}
		}
		n, err := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{})
		refused("Normalize", n, err)
		// ResolvePolicy reads an expanded object: Expand makes one and checks no values
		obj, _, err := forms.Expand(doc, cs.Root, f.AccessKeys, g, forms.Context{})
		if err != nil {
			t.Fatalf("%q: expand: %v", c.yaml, err)
		}
		r, err := forms.ResolvePolicy(obj, cs.Root, cs.Policy, cs.Surface, f.AccessKeys, g, nil)
		refused("ResolvePolicy", r, err)
		p, err := forms.NormalizeWithPolicy(doc, cs, f.AccessKeys, g, forms.Context{}, nil)
		refused("NormalizeWithPolicy", p, err)
	}
	// a key set without access: the wrapper the normalization makes has none, and the resolution refuses it instead of panicking
	noAccess := []string{"value", "modify", "inherit", "default_injection", "conformance"}
	doc, _ := forms.Parse("type: t\n")
	p, err := forms.NormalizeWithPolicy(doc, cs, noAccess, g, forms.Context{}, nil)
	var e *forms.Error
	if p != nil || !errors.As(err, &e) || e.Kind != forms.ErrUnsupported || !strings.Contains(err.Error(), "the position has no access") {
		t.Fatalf("want an Unsupported error naming access, got %v / %v", p, err)
	}
}

// Normalize reads the rule members of an authored expanded wrapper under the bound grammar, as the resolution does (#90): a
// member it cannot read is refused with the resolution's message, and a wrapper it can read is normalized as before. The Rust
// peer is `normalize_reads_the_rule_members_of_an_authored_wrapper` in rust/tests/composed.rs.
func TestNormalizeReadsTheRuleMembersOfAnAuthoredWrapper(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	var schema string
	for _, c := range f.Cases {
		if c.ID == "an-absent-field-gets-no-entry-and-an-inserted-default-does" {
			schema = c.Schema
		}
	}
	cs, err := compileDefaults(schema, f.GrammarPin, "config_surface")
	if err != nil {
		t.Fatal(err)
	}
	g := bound(t, 1)
	wrapper := func(access, modify, inherit string) string {
		return "type: t\nlabel:\n  value: hello\n  access: " + access + "\n  modify: " + modify + "\n  inherit: " + inherit +
			"\n  default_injection: null\n  conformance: implemented\n"
	}
	for _, c := range []struct{ yaml, want string }{
		{wrapper("123", "inherit", "true"), "$.label: access of the position: INVALID_ACCESS_VALUE"},
		{wrapper("inherit", "5", "true"), "$.label: modify of the position: INVALID_ACCESS_VALUE"},
		{wrapper("inherit", "inherit", "maybe"), "$.label: inherit of the position: INVALID_INHERIT"},
	} {
		doc, _ := forms.Parse(c.yaml)
		res, err := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{})
		var e *forms.Error
		if res != nil || !errors.As(err, &e) || e.Kind != forms.ErrUnsupported || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%q: want %q and no result, got %v / %v", c.yaml, c.want, res, err)
		}
	}
	doc, _ := forms.Parse(wrapper("['O=r']", "inherit", "false"))
	if _, err := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{}); err != nil {
		t.Fatalf("a wrapper the grammar reads was refused: %v", err)
	}
}
