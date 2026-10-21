package forms_test

// The shared policy cases (testdata/p1/policy.json): CompileSchema reads spec.policy_surface and
// identity.base the same way as the Rust peer (rust/tests/policy.rs). The cases are synthetic; the real
// registry schema is checked in real_schema_test.go.

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type policyFile struct {
	Grammar string `json:"grammar"`
	Cases   []struct {
		ID      string `json:"id"`
		Schema  string `json:"schema"`
		Surface string `json:"surface"`
		Compile struct {
			OK       bool           `json:"ok"`
			Error    string         `json:"error"`
			Problems [][]string     `json:"problems"`
			Details  []string       `json:"details"`
			Base     string         `json:"base"`
			Policy   map[string]any `json:"policy"`
		} `json:"compile"`
	} `json:"cases"`
}

func ruleJSON(r *forms.PolicyRule) any {
	switch {
	case r == nil:
		return nil
	case r.Symbol != "":
		return map[string]any{"symbol": r.Symbol}
	}
	pats := make([]any, 0, len(r.Patterns))
	for _, p := range r.Patterns {
		pats = append(pats, p)
	}
	return map[string]any{"patterns": pats}
}

func policyJSON(p forms.PolicySurface) map[string]any {
	rules := []any{}
	for _, r := range p.SurfaceRules {
		rules = append(rules, map[string]any{"surface": r.Surface, "access": ruleJSON(r.Access), "modify": ruleJSON(r.Modify)})
	}
	mode := map[forms.Mode]string{forms.ModeInherit: "inherit", forms.ModeNoInherit: "no-inherit", forms.ModeReset: "reset"}[p.InheritPolicy]
	return map[string]any{
		"declared": p.Declared, "default_access": ruleJSON(p.DefaultAccess), "default_modify": ruleJSON(p.DefaultModify),
		"surface_rules": rules, "inherit_policy": mode, "inherit_policy_declared": p.InheritPolicyDeclared,
	}
}

func TestSharedPolicyCases(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/policy.json")
	if err != nil {
		t.Fatal(err)
	}
	var f policyFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Cases) == 0 {
		t.Fatal("no policy cases")
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			in := forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: f.Grammar, Surface: c.Surface}
			compiled, err := forms.CompileSchema(in)
			if !c.Compile.OK {
				var e *forms.Error
				if !errors.As(err, &e) || e.Code() != c.Compile.Error {
					t.Fatalf("want error %s, got %v", c.Compile.Error, err)
				}
				var got [][]string
				for _, p := range e.Problems {
					got = append(got, []string{p.Code, p.Path})
				}
				if !reflect.DeepEqual(got, c.Compile.Problems) {
					t.Fatalf("problems\n got %v\nwant %v", got, c.Compile.Problems)
				}
				for i, d := range c.Compile.Details {
					if !strings.Contains(e.Problems[i].Detail, d) {
						t.Fatalf("problem %d detail %q does not contain %q", i, e.Problems[i].Detail, d)
					}
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if compiled.Base != c.Compile.Base {
				t.Fatalf("base %q, want %q", compiled.Base, c.Compile.Base)
			}
			// Round-trip through JSON so the numbers and slices have the shape of the file.
			raw, _ := json.Marshal(policyJSON(compiled.Policy))
			var got map[string]any
			_ = json.Unmarshal(raw, &got)
			if !reflect.DeepEqual(got, c.Compile.Policy) {
				t.Fatalf("policy\n got %v\nwant %v", got, c.Compile.Policy)
			}
		})
	}
}
