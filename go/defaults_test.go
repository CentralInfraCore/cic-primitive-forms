package forms_test

// The shared default cases (testdata/p1/defaults.json): CompileSchema refuses what ADR 0011 says it must, and
// ApplyDefaults inserts the declared default of an absent optional field and of nothing else. The Rust peer is
// rust/tests/defaults.rs.

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type defaultsFile struct {
	Grammar    string   `json:"grammar"`
	AccessKeys []string `json:"access_keys"`
	Cases      []struct {
		ID        string `json:"id"`
		Schema    string `json:"schema"`
		Surface   string `json:"surface"`
		Documents []struct {
			ID     string `json:"id"`
			YAML   string `json:"yaml"`
			Expect struct {
				Violations [][]string `json:"violations"`
				Document   string     `json:"document"`
				Origins    [][]string `json:"origins"`
			} `json:"expect"`
		} `json:"documents"`
	} `json:"cases"`
	Accepted []struct {
		ID      string `json:"id"`
		Schema  string `json:"schema"`
		Surface string `json:"surface"`
	} `json:"accepted"`
	Refusals []struct {
		ID       string     `json:"id"`
		Schema   string     `json:"schema"`
		Surface  string     `json:"surface"`
		Problems [][]string `json:"problems"`
	} `json:"refusals"`
}

func loadDefaults(t *testing.T) defaultsFile {
	t.Helper()
	b, err := os.ReadFile("../testdata/p1/defaults.json")
	if err != nil {
		t.Fatal(err)
	}
	var f defaultsFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func compileDefaults(schema, grammar, surface string) (*forms.CompiledSchema, error) {
	return forms.CompileSchema(forms.SchemaInput{Bytes: []byte(schema), SHA256: forms.SHA256Hex([]byte(schema)), Grammar: grammar, Surface: surface})
}

func TestSharedDefaultRefusals(t *testing.T) {
	f := loadDefaults(t)
	if len(f.Refusals) == 0 {
		t.Fatal("no refusal cases")
	}
	for _, c := range f.Refusals {
		t.Run(c.ID, func(t *testing.T) {
			_, err := compileDefaults(c.Schema, f.Grammar, c.Surface)
			var e *forms.Error
			if !errors.As(err, &e) {
				t.Fatalf("want a schema refusal, got %v", err)
			}
			var got [][]string
			for _, p := range e.Problems {
				got = append(got, []string{p.Code, p.Path})
			}
			if !reflect.DeepEqual(got, c.Problems) {
				t.Fatalf("problems\n got %v\nwant %v", got, c.Problems)
			}
		})
	}
}

func TestSharedAcceptedRoles(t *testing.T) {
	f := loadDefaults(t)
	if len(f.Accepted) == 0 {
		t.Fatal("no accepted cases")
	}
	for _, c := range f.Accepted {
		t.Run(c.ID, func(t *testing.T) {
			if _, err := compileDefaults(c.Schema, f.Grammar, c.Surface); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestApplyDefaultsRefusesWhatTheValidationDoesNotSupport(t *testing.T) {
	f := loadDefaults(t)
	compiled, err := compileDefaults(f.Cases[0].Schema, f.Grammar, f.Cases[0].Surface)
	if err != nil {
		t.Fatal(err)
	}
	// An expanded Access node inside a validated document is P2's, not a document this slice defaults.
	doc, err := forms.Parse("type: t\nenabled:\n  value: true\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n")
	if err != nil {
		t.Fatal(err)
	}
	var e *forms.Error
	if res, err := forms.ApplyDefaults(doc, compiled.Root, f.AccessKeys); !errors.As(err, &e) || e.Kind != forms.ErrUnsupported || res != nil {
		t.Fatalf("want an unsupported error and no result, got %v / %v", res, err)
	}
}

func TestApplyDefaultsOfAnAbsentOptionalRootHasNoDocument(t *testing.T) {
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{{Name: "a",
		Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional}}}}
	res, err := forms.ApplyDefaults(nil, root, nil)
	if err != nil || res.Document != nil || len(res.Violations) != 0 || len(res.Origins) != 0 {
		t.Fatalf("got %+v / %v", res, err)
	}
}

func TestSharedApplyDefaults(t *testing.T) {
	f := loadDefaults(t)
	for _, c := range f.Cases {
		compiled, err := compileDefaults(c.Schema, f.Grammar, c.Surface)
		if err != nil {
			t.Fatalf("%s: %v", c.ID, err)
		}
		for _, d := range c.Documents {
			t.Run(c.ID+"/"+d.ID, func(t *testing.T) {
				doc, err := forms.Parse(d.YAML)
				if err != nil {
					t.Fatal(err)
				}
				before := forms.Emit(doc)
				res, err := forms.ApplyDefaults(doc, compiled.Root, f.AccessKeys)
				if err != nil {
					t.Fatal(err)
				}
				if forms.Emit(doc) != before {
					t.Fatal("the input was modified")
				}
				if d.Expect.Document == "" {
					var got [][]string
					for _, v := range res.Violations {
						got = append(got, []string{v.Code, v.LogicalPath})
					}
					if res.Document != nil || !reflect.DeepEqual(got, d.Expect.Violations) {
						t.Fatalf("want violations %v and no document, got %v / %v", d.Expect.Violations, got, res.Document)
					}
					return
				}
				if len(res.Violations) != 0 {
					t.Fatalf("violations %v", res.Violations)
				}
				if got := forms.Emit(res.Document); got != d.Expect.Document {
					t.Fatalf("document\n got %q\nwant %q", got, d.Expect.Document)
				}
				var origins [][]string
				for _, o := range res.Origins {
					origins = append(origins, []string{o.Path, o.Origin})
				}
				if !reflect.DeepEqual(origins, d.Expect.Origins) {
					t.Fatalf("origins\n got %v\nwant %v", origins, d.Expect.Origins)
				}
				// The defaulted positions are exactly the default candidates of the validation of the input.
				v, _ := forms.Validate(doc, compiled.Root, f.AccessKeys)
				var defaulted []string
				for _, o := range res.Origins {
					if o.Origin == forms.ValueOriginDefault {
						defaulted = append(defaulted, o.Path)
					}
				}
				if !sameSet(defaulted, v.DefaultCandidates) {
					t.Fatalf("defaulted %v, candidates %v", defaulted, v.DefaultCandidates)
				}
				// Idempotence: the result needs no further defaults, and every value of it is written in it.
				again, err := forms.ApplyDefaults(res.Document, compiled.Root, f.AccessKeys)
				if err != nil || forms.Emit(again.Document) != d.Expect.Document {
					t.Fatalf("not idempotent: %v / %q", err, forms.Emit(again.Document))
				}
				for _, o := range again.Origins {
					if o.Origin != forms.ValueOriginAuthored {
						t.Fatalf("%s is still %s after the defaults were applied", o.Path, o.Origin)
					}
				}
			})
		}
	}
}

func sameSet(a, b []string) bool {
	m := map[string]int{}
	for _, x := range a {
		m[x]++
	}
	for _, x := range b {
		m[x]--
	}
	for _, n := range m {
		if n != 0 {
			return false
		}
	}
	return true
}
