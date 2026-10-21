package forms_test

// The shared schema cases (testdata/p1/schema.json) against CompileSchema and Validate, the Go peer of
// rust/tests/schema.rs. A case compiles a pinned schema and validates documents against its surface; the
// expected outcome is the same file for both languages. The cases are synthetic and written here; the
// real registry schema is checked in real_schema_test.go.

import (
	"encoding/json"
	"errors"
	"os"
	"slices"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type schemaFile struct {
	Grammar    string       `json:"grammar"`
	AccessKeys []string     `json:"access_keys"`
	Cases      []schemaCase `json:"cases"`
}

type schemaCase struct {
	ID      string `json:"id"`
	Schema  string `json:"schema"`
	Surface string `json:"surface"`
	SHA256  string `json:"sha256"`
	Grammar string `json:"grammar"`
	Compile struct {
		OK                  bool       `json:"ok"`
		Error               string     `json:"error"`
		Problems            [][]string `json:"problems"`
		UsesItemChoice      bool       `json:"uses_item_choice"`
		UnevaluatedContract []string   `json:"unevaluated_contracts"`
		Kind                string     `json:"kind"`
		Namespace           string     `json:"namespace"`
		Version             string     `json:"version"`
	} `json:"compile"`
	Documents []struct {
		ID     string `json:"id"`
		YAML   string `json:"yaml"`
		Expect struct {
			Violations  []map[string]string `json:"violations"`
			Candidates  []string            `json:"candidates"`
			Unsupported bool                `json:"unsupported"`
		} `json:"expect"`
	} `json:"documents"`
}

func loadSchemaCases(t *testing.T) schemaFile {
	t.Helper()
	b, err := os.ReadFile("../testdata/p1/schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var f schemaFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func TestSharedSchemaCases(t *testing.T) {
	f := loadSchemaCases(t)
	if len(f.Cases) == 0 {
		t.Fatal("no schema cases")
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			in := forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: f.Grammar, Surface: c.Surface}
			if c.SHA256 != "" {
				in.SHA256 = c.SHA256
			}
			if c.Grammar != "" {
				in.Grammar = c.Grammar
			}
			compiled, err := forms.CompileSchema(in)
			if !c.Compile.OK {
				var e *forms.Error
				if !errors.As(err, &e) || e.Code() != c.Compile.Error {
					t.Fatalf("want error %s, got %v", c.Compile.Error, err)
				}
				if c.Compile.Problems != nil {
					var got [][]string
					for _, p := range e.Problems {
						got = append(got, []string{p.Code, p.Path})
					}
					if !slices.EqualFunc(got, c.Compile.Problems, slices.Equal) {
						t.Fatalf("problems\n got %v\nwant %v", got, c.Compile.Problems)
					}
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			want := c.Compile
			if compiled.UsesItemChoice != want.UsesItemChoice || compiled.Kind != want.Kind || compiled.Namespace != want.Namespace ||
				compiled.Version != want.Version || compiled.Digest != in.SHA256 || compiled.Grammar != f.Grammar ||
				!slices.Equal(append([]string{}, compiled.UnevaluatedContracts...), append([]string{}, want.UnevaluatedContract...)) {
				t.Fatalf("compiled %+v does not match %+v", compiled, want)
			}
			for _, d := range c.Documents {
				node, err := forms.Parse(d.YAML)
				if err != nil {
					t.Fatalf("%s: %v", d.ID, err)
				}
				v, err := forms.Validate(node, compiled.Root, f.AccessKeys)
				if d.Expect.Unsupported {
					var e *forms.Error
					if !errors.As(err, &e) || e.Kind != forms.ErrUnsupported {
						t.Fatalf("%s: want an unsupported error, got %v / %v", d.ID, v, err)
					}
					continue
				}
				if err != nil {
					t.Fatalf("%s: %v", d.ID, err)
				}
				var got []map[string]string
				for _, x := range v.Violations {
					got = append(got, map[string]string{"code": x.Code, "path": x.LogicalPath})
				}
				if len(got) != len(d.Expect.Violations) {
					t.Fatalf("%s: violations %v, want %v", d.ID, got, d.Expect.Violations)
				}
				for i := range got {
					if got[i]["code"] != d.Expect.Violations[i]["code"] || got[i]["path"] != d.Expect.Violations[i]["path"] {
						t.Fatalf("%s: violations %v, want %v", d.ID, got, d.Expect.Violations)
					}
				}
				if !slices.Equal(append([]string{}, v.DefaultCandidates...), append([]string{}, d.Expect.Candidates...)) {
					t.Fatalf("%s: candidates %v, want %v", d.ID, v.DefaultCandidates, d.Expect.Candidates)
				}
			}
		})
	}
}
