package forms_test

// The shared PolicyResolution cases (testdata/p1/resolution.json): a document is normalized, then every position of the object is
// resolved. The expectations were derived from the rules of the reference resolution (access_policy.py, in the pin), not from this
// code's output; the golden vectors themselves are run in resolve_vectors_test.go. The Rust peer is rust/tests/resolution.rs.

import (
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type resolutionFile struct {
	GrammarPin string   `json:"grammar_pin"`
	AccessKeys []string `json:"access_keys"`
	Cases      []struct {
		ID        string   `json:"id"`
		Schema    string   `json:"schema"`
		Grammar   int64    `json:"grammar"`
		YAML      string   `json:"yaml"`
		Unknown   []string `json:"unknown"`
		Expect    [][]any  `json:"expect"`
		Positions []string `json:"positions"`
	} `json:"cases"`
}

var kindNames = map[forms.RuleKind]string{forms.RuleList: "list", forms.RuleOpen: "open", forms.RuleOwner: "owner", forms.RuleNone: "none", forms.RuleUnknown: "unknown"}

func ruleRow(path, op string, r forms.EffectiveRule) []any {
	var pats any
	if r.Kind == forms.RuleList {
		p := make([]any, 0, len(r.Patterns))
		for _, x := range r.Patterns {
			p = append(p, x)
		}
		pats = p
	}
	return []any{path, op, kindNames[r.Kind], r.Layer, r.SourcePath, pats, r.Reason}
}

func resolveCase(t *testing.T, f resolutionFile, schema string, g int64, y string, unknown []string) (forms.PolicyResolution, *forms.NormalizationResult, *forms.Node) {
	t.Helper()
	c, err := compileDefaults(schema, f.GrammarPin, "config_surface")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := forms.Parse(y)
	if err != nil {
		t.Fatal(err)
	}
	gr := bound(t, g)
	res, err := forms.Normalize(doc, c.Root, f.AccessKeys, gr, forms.Context{})
	if err != nil {
		t.Fatal(err)
	}
	pr, err := forms.ResolvePolicy(res.Object, c.Root, c.Policy, "config_surface", f.AccessKeys, gr, unknown)
	if err != nil {
		t.Fatal(err)
	}
	return pr, res, doc
}

func TestSharedPolicyResolutionCases(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/resolution.json")
	if err != nil {
		t.Fatal(err)
	}
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			pr, res, doc := resolveCase(t, f, c.Schema, c.Grammar, c.YAML, c.Unknown)
			var positions []string
			for p := range pr {
				positions = append(positions, p)
			}
			slices.Sort(positions)
			if !reflect.DeepEqual(positions, c.Positions) {
				t.Fatalf("positions\n got %v\nwant %v", positions, c.Positions)
			}
			for _, row := range c.Expect {
				path, op := row[0].(string), row[1].(string)
				e := pr[path]
				got := ruleRow(path, op, e.Access)
				if op == "modify" {
					got = ruleRow(path, op, e.Modify)
				}
				if !reflect.DeepEqual(got, row) {
					t.Errorf("%s %s\n got %v\nwant %v", path, op, got, row)
				}
			}
			// the resolution leaves the normalization alone: the authored document still comes back
			c2, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
			back, err := forms.Denormalize(res, c2.Root, f.AccessKeys, bound(t, c.Grammar), forms.Context{})
			if err != nil || !back.Same(doc) {
				t.Fatalf("denormalize after the resolution: %v", err)
			}
		})
	}
}

func TestPolicyResolutionDoesNotDependOnTheOrderOfTheListElements(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	for _, c := range f.Cases {
		if !strings.HasPrefix(c.ID, "list-elements") {
			continue
		}
		pr1, _, _ := resolveCase(t, f, c.Schema, c.Grammar, c.YAML, nil)
		// the same elements in the other order
		first := "    - to: b\n      metric:\n"
		i := strings.Index(c.YAML, first)
		j := strings.Index(c.YAML, "    - to: a\n")
		k := strings.Index(c.YAML, "  access: [O=r]")
		if i < 0 || j < i || k < j {
			t.Fatal("the case text changed: the reorder found nothing")
		}
		swapped := c.YAML[:i] + c.YAML[j:k] + c.YAML[i:j] + c.YAML[k:]
		pr2, _, _ := resolveCase(t, f, c.Schema, c.Grammar, swapped, nil)
		if !reflect.DeepEqual(pr1, pr2) {
			t.Fatalf("a different order of the elements gave another resolution")
		}
	}
}

func TestPolicyResolutionRefusesWhatItCannotDescribe(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	c := f.Cases[0]
	cs, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
	g := bound(t, 1)
	short, _ := forms.Parse("type: t\n") // a short position is not an expanded one
	if r, err := forms.ResolvePolicy(short, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, nil); r != nil || err == nil {
		t.Fatalf("a document that was not normalized was resolved: %v / %v", r, err)
	}
	doc, _ := forms.Parse("type: t\n")
	res, _ := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{})
	if r, err := forms.ResolvePolicy(res.Object, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, []string{"$.nowhere"}); r != nil || err == nil {
		t.Fatalf("an unknown object that does not exist was accepted: %v / %v", r, err)
	}
	bad, _ := forms.Parse("bogus:\n  value: 1\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n")
	if r, err := forms.ResolvePolicy(bad, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, nil); r != nil || err == nil {
		t.Fatalf("an undeclared field was resolved: %v / %v", r, err)
	}
}
