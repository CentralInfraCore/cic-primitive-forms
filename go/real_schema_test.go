package forms_test

// The pinned real schemas of the registry (corpus/pins.yaml, schemas.*) against CompileSchema. The file is CC BY-NC-SA 4.0 and is not vendored: it is read from CIC_REGISTRY_ROOT, a
// checkout of cic-schema-registry at the pinned commit, and its SHA-256 must equal the pin before anything
// is compiled. The expectations (testdata/p1/real-schemas.json) hold codes and paths only.
//
// Two outcomes are pinned down. network-interface.yaml: config_surface does not compile, because 22 fields
// declare a default without `optional`, which shape.yaml makes invalid (a field that says neither is
// mandatory, and a mandatory field has no default); the library refuses the schema and names every field, it
// does not guess that `optional` was meant. network-space.yaml, the provider-neutral VCN/VPC/VNet space:
// config_surface compiles, and an authored VCN intent is validated against it.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"gopkg.in/yaml.v3"
)

type realDoc struct {
	ID          string     `json:"id"`
	YAML        string     `json:"yaml"`
	Violations  [][]string `json:"violations"`
	Candidates  []string   `json:"candidates"`
	Unsupported bool       `json:"unsupported"`
}

type realExpect struct {
	Refused struct {
		Pin      string     `json:"pin"`
		Surface  string     `json:"surface"`
		Error    string     `json:"error"`
		Problems [][]string `json:"problems"`
	} `json:"refused"`
	StateRefused struct {
		Pin   string `json:"pin"`
		Error string `json:"error"`
	} `json:"state_refused"`
	Accepted struct {
		Pin            string   `json:"pin"`
		Surface        string   `json:"surface"`
		Kind           string   `json:"kind"`
		Namespace      string   `json:"namespace"`
		UsesItemChoice bool     `json:"uses_item_choice"`
		Unevaluated    []string `json:"unevaluated_contracts"`
		ValidatedBy    string   `json:"declared_validated_by"`
		Base           string   `json:"base"`
		PolicySummary  struct {
			Declared      bool     `json:"declared"`
			DefaultAccess string   `json:"default_access"`
			DefaultModify string   `json:"default_modify"`
			SurfaceRules  []string `json:"surface_rules"`
			InheritPolicy string   `json:"inherit_policy"`
			InheritSet    bool     `json:"inherit_policy_declared"`
		} `json:"policy_summary"`
		Documents []realDoc `json:"documents"`
	} `json:"accepted"`
}

func realSchema(t *testing.T, pin string) (data []byte, grammar string) {
	t.Helper()
	root := os.Getenv("CIC_REGISTRY_ROOT")
	if root == "" {
		t.Fatal("CIC_REGISTRY_ROOT must point at a checkout of CentralInfraCore/cic-schema-registry at the pinned commit " +
			"(corpus/pins.yaml, schemas." + pin + ")")
	}
	b, err := os.ReadFile("../corpus/pins.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var pins struct {
		Grammar map[string]any `yaml:"grammar"`
		Schemas map[string]struct {
			Path   string `yaml:"path"`
			SHA256 string `yaml:"sha256"`
		} `yaml:"schemas"`
	}
	if err := yaml.Unmarshal(b, &pins); err != nil {
		t.Fatal(err)
	}
	s, ok := pins.Schemas[pin]
	if !ok {
		t.Fatalf("pin %s is not in corpus/pins.yaml", pin)
	}
	data, err = os.ReadFile(filepath.Join(root, s.Path))
	if err != nil {
		t.Fatal(err)
	}
	if forms.SHA256Hex(data) != s.SHA256 {
		t.Fatalf("%s is not the pinned file: sha256 %s, pinned %s", s.Path, forms.SHA256Hex(data), s.SHA256)
	}
	for name := range pins.Grammar {
		grammar = name
	}
	return data, grammar
}

func compileReal(t *testing.T, pin, surface string) (*forms.CompiledSchema, error) {
	t.Helper()
	data, grammar := realSchema(t, pin)
	return forms.CompileSchema(forms.SchemaInput{Bytes: data, SHA256: forms.SHA256Hex(data), Grammar: grammar, Surface: surface})
}

func loadRealExpect(t *testing.T) realExpect {
	t.Helper()
	var want realExpect
	b, err := os.ReadFile("../testdata/p1/real-schemas.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &want); err != nil {
		t.Fatal(err)
	}
	return want
}

func TestRealRegistrySchemas(t *testing.T) {
	want := loadRealExpect(t)

	t.Run("network-interface config_surface is refused with every defect named", func(t *testing.T) {
		_, err := compileReal(t, want.Refused.Pin, want.Refused.Surface)
		var e *forms.Error
		if !errors.As(err, &e) || e.Kind != forms.ErrSchema || e.Code() != want.Refused.Error {
			t.Fatalf("got %v", err)
		}
		var got [][]string
		for _, p := range e.Problems {
			got = append(got, []string{p.Code, p.Path})
		}
		if !slices.EqualFunc(got, want.Refused.Problems, slices.Equal) {
			t.Fatalf("problems\n got %v\nwant %v", got, want.Refused.Problems)
		}
	})

	t.Run("state_surface is not validated as authoring", func(t *testing.T) {
		_, err := compileReal(t, want.StateRefused.Pin, "state_surface")
		var e *forms.Error
		if !errors.As(err, &e) || e.Code() != want.StateRefused.Error {
			t.Fatalf("got %v", err)
		}
	})

	t.Run("network-space config_surface compiles and validates a VCN intent", func(t *testing.T) {
		a := want.Accepted
		c, err := compileReal(t, a.Pin, a.Surface)
		if err != nil {
			t.Fatal(err)
		}
		if c.Kind != a.Kind || c.Namespace != a.Namespace || c.UsesItemChoice != a.UsesItemChoice ||
			!slices.Equal(c.UnevaluatedContracts, a.Unevaluated) || c.DeclaredValidatedBy != a.ValidatedBy {
			t.Fatalf("compiled %+v", c)
		}
		// The policy and the base are read as declared, never evaluated (patterns are not asserted: the
		// file is licensed content, and only the structure is pinned down).
		pol, ps := c.Policy, a.PolicySummary
		var rules []string
		for _, r := range pol.SurfaceRules {
			rules = append(rules, r.Surface)
		}
		if c.Base != a.Base || pol.Declared != ps.Declared || pol.DefaultAccess == nil || pol.DefaultAccess.Symbol != ps.DefaultAccess ||
			pol.DefaultModify == nil || pol.DefaultModify.Symbol != ps.DefaultModify || !slices.Equal(rules, ps.SurfaceRules) ||
			pol.InheritPolicy != forms.ModeInherit || pol.InheritPolicyDeclared != ps.InheritSet || ps.InheritPolicy != "inherit" {
			t.Fatalf("base %q, policy %+v", c.Base, pol)
		}
		for _, d := range a.Documents {
			node, err := forms.Parse(d.YAML)
			if err != nil {
				t.Fatalf("%s: %v", d.ID, err)
			}
			v, err := forms.Validate(node, c.Root, accessKeys)
			if d.Unsupported {
				var e *forms.Error
				if !errors.As(err, &e) || e.Kind != forms.ErrUnsupported {
					t.Fatalf("%s: want unsupported, got %v", d.ID, err)
				}
				continue
			}
			if err != nil {
				t.Fatalf("%s: %v", d.ID, err)
			}
			var got [][]string
			for _, x := range v.Violations {
				got = append(got, []string{x.Code, x.LogicalPath})
			}
			if !slices.EqualFunc(got, d.Violations, slices.Equal) || !slices.Equal(append([]string{}, v.DefaultCandidates...), append([]string{}, d.Candidates...)) {
				t.Fatalf("%s: %v / %v, want %v / %v", d.ID, got, v.DefaultCandidates, d.Violations, d.Candidates)
			}
		}
	})

	t.Run("another byte is a digest mismatch, not a schema", func(t *testing.T) {
		data, grammar := realSchema(t, want.Accepted.Pin)
		sum := forms.SHA256Hex(data)
		_, err := forms.CompileSchema(forms.SchemaInput{Bytes: append(slices.Clone(data), '\n'), SHA256: sum, Grammar: grammar, Surface: "config_surface"})
		var e *forms.Error
		if !errors.As(err, &e) || e.Code() != forms.SchemaDigestMismatch {
			t.Fatalf("got %v", err)
		}
	})
}
