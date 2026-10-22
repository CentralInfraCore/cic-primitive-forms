package forms_test

// Dumps the outcome of Normalize + ResolvePolicy for the generated inputs of tools/parity/gen_p3.py, for the Rust/Go differential of P3
// (G3 of the P3 exit-criteria check). PARITY_P3_IN is a JSON-lines file, PARITY_P3_OUT receives one JSON line per input. Without the two
// variables this test does nothing. The Rust peer is rust/tests/parity_p3_dump.rs; tools/parity/compare_p3.py compares.

import (
	"bufio"
	"encoding/json"
	"os"
	"sort"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func p3Rule(r forms.EffectiveRule) []any {
	kind := map[forms.RuleKind]string{forms.RuleList: "list", forms.RuleOpen: "open", forms.RuleOwner: "owner", forms.RuleNone: "none", forms.RuleUnknown: "unknown"}[r.Kind]
	var pats any
	if r.Kind == forms.RuleList {
		p := make([]any, 0, len(r.Patterns))
		for _, x := range r.Patterns {
			p = append(p, x)
		}
		pats = p
	}
	return []any{kind, r.Layer, r.SourcePath, r.Reason, pats}
}

type p3Case struct {
	ID         int      `json:"id"`
	Schema     string   `json:"schema"`
	YAML       string   `json:"yaml"`
	Grammar    int64    `json:"grammar"`
	Pin        string   `json:"pin"`
	AccessKeys []string `json:"access_keys"`
	Unknown    []string `json:"unknown"`
}

func p3Run(c p3Case) map[string]any {
	stage := func(s string) map[string]any { return map[string]any{"id": c.ID, "stage": s} }
	cs, err := forms.CompileSchema(forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: c.Pin, Surface: "config_surface"})
	if err != nil {
		return stage("compile")
	}
	doc, err := forms.Parse(c.YAML)
	if err != nil {
		return stage("parse")
	}
	n := &forms.Node{Kind: forms.KindInt, Int: c.Grammar}
	g, err := forms.BindGrammar(n, n)
	if err != nil {
		return stage("grammar")
	}
	res, err := forms.Normalize(doc, cs.Root, c.AccessKeys, g, forms.Context{})
	if err != nil {
		return stage("normalize")
	}
	pr, err := forms.ResolvePolicy(res.Object, cs.Root, cs.Policy, cs.Surface, c.AccessKeys, g, c.Unknown)
	if err != nil {
		return stage("resolve")
	}
	paths := make([]string, 0, len(pr))
	for p := range pr {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	rules := map[string]any{}
	for _, p := range paths {
		rules[p] = map[string]any{"access": p3Rule(pr[p].Access), "modify": p3Rule(pr[p].Modify)}
	}
	origins := []any{}
	for _, o := range res.ValueOrigins {
		origins = append(origins, []any{o.Path, string(o.Origin)})
	}
	return map[string]any{"id": c.ID, "stage": "ok", "object": forms.Emit(res.Object), "origins": origins, "rules": rules}
}

func TestDumpP3Outcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_P3_IN"), os.Getenv("PARITY_P3_OUT")
	if in == "" || out == "" {
		t.Skip("PARITY_P3_IN / PARITY_P3_OUT not set")
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	o, err := os.Create(out)
	if err != nil {
		t.Fatal(err)
	}
	defer o.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	enc := json.NewEncoder(o)
	for sc.Scan() {
		var c p3Case
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		if err := enc.Encode(p3Run(c)); err != nil {
			t.Fatal(err)
		}
	}
}
