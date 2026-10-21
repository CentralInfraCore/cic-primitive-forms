package forms_test

// The shared document-level expand/collapse cases (testdata/p1/expand.json) against forms.Expand and forms.Collapse, the
// Go peer of rust/tests/expand.rs. The schema is compile.small-config of testdata/p1/schema.json; the expected texts
// spell inherit the way access grammar 1 does (the grammar the corpus pins).

import (
	"encoding/json"
	"errors"
	"os"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type expandCase struct {
	ID                           string          `json:"id"`
	YAML                         string          `json:"yaml"`
	Expanded                     string          `json:"expanded_yaml"`
	Provenance                   map[string]bool `json:"provenance"`
	CollapseWithoutProvenanceYML string          `json:"collapse_without_provenance_yaml"`
	Error                        *struct {
		Code string `json:"code"`
		Path string `json:"path"`
	} `json:"error"`
}

type contextJSON struct {
	Origin      string    `json:"origin"`
	Attestation string    `json:"attestation"`
	Positions   *[]string `json:"attested_positions"`
}

func (c contextJSON) context() forms.Context {
	ctx := forms.Context{Origin: c.Origin, Attestation: c.Attestation}
	if c.Positions != nil {
		ctx.AttestedPositions = append([]string{}, *c.Positions...)
	}
	return ctx
}

func smallConfig(t *testing.T) forms.Shape {
	t.Helper()
	f := loadSchemaCases(t)
	for _, c := range f.Cases {
		if c.ID == "compile.small-config" {
			in := forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: f.Grammar, Surface: c.Surface}
			s, err := forms.CompileSchema(in)
			if err != nil {
				t.Fatal(err)
			}
			return s.Root
		}
	}
	t.Fatal("compile.small-config not found")
	return forms.Shape{}
}

func parseCase(t *testing.T, id, text string) *forms.Node {
	t.Helper()
	n, err := forms.Parse(text)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	return n
}

func TestSharedExpandCollapseCases(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/expand.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases    []expandCase `json:"cases"`
		Contexts []struct {
			ID      string      `json:"id"`
			Context contextJSON `json:"context"`
			OK      bool        `json:"ok"`
			Error   string      `json:"error"`
		} `json:"contexts"`
		Materialized []struct {
			ID        string      `json:"id"`
			YAML      string      `json:"yaml"`
			Context   contextJSON `json:"context"`
			Collapsed string      `json:"collapsed_yaml"`
		} `json:"materialized"`
	}
	if err := json.Unmarshal(b, &file); err != nil {
		t.Fatal(err)
	}
	shape, g := smallConfig(t), boundGrammar(t)
	for _, c := range file.Cases {
		t.Run(c.ID, func(t *testing.T) {
			doc := parseCase(t, c.ID, c.YAML)
			before := cloneNode(doc)
			expanded, prov, err := forms.Expand(doc, shape, accessKeys, g, forms.Context{})
			if c.Error != nil {
				var fe *forms.Error
				if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || fe.Violations[0].Code != c.Error.Code || fe.Violations[0].LogicalPath != c.Error.Path {
					t.Fatalf("want %+v, got %v", c.Error, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !doc.Same(before) {
				t.Error("Expand changed its input")
			}
			if want := parseCase(t, c.ID, c.Expanded); !expanded.Same(want) {
				t.Errorf("expanded\n got %s\nwant %s", forms.Emit(expanded), forms.Emit(want))
			}
			if len(prov) != len(c.Provenance) {
				t.Errorf("provenance %v, want %v", prov, c.Provenance)
			}
			for path, authored := range c.Provenance {
				if o, ok := prov[path]; !ok || o.AuthoredExpanded != authored {
					t.Errorf("provenance of %s: %v, want authored=%v", path, prov[path], authored)
				}
			}
			// the round trip: what was authored comes back, with the provenance
			back, err := forms.Collapse(expanded, shape, accessKeys, g, prov, forms.Context{})
			if err != nil {
				t.Fatal(err)
			}
			if !back.Same(doc) {
				t.Errorf("collapse(expand(x)) != x\n got %s\nwant %s", forms.Emit(back), forms.Emit(doc))
			}
			// without the provenance only the law decides
			if c.CollapseWithoutProvenanceYML != "" {
				plain, err := forms.Collapse(expanded, shape, accessKeys, g, nil, forms.Context{})
				if err != nil {
					t.Fatal(err)
				}
				if want := parseCase(t, c.ID, c.CollapseWithoutProvenanceYML); !plain.Same(want) {
					t.Errorf("collapse without provenance\n got %s\nwant %s", forms.Emit(plain), forms.Emit(want))
				}
			}
			// every position attested by the binding is never shortened
			var all []string
			for p := range prov {
				all = append(all, p)
			}
			attested, err := forms.Collapse(expanded, shape, accessKeys, g, prov,
				forms.Context{Origin: forms.OriginMaterialized, Attestation: "att", AttestedPositions: all})
			if err != nil {
				t.Fatal(err)
			}
			if !attested.Same(expanded) {
				t.Errorf("a document attested everywhere must stay as it is\n got %s", forms.Emit(attested))
			}
		})
	}
}

func TestSharedTrustedContext(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/expand.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Contexts []struct {
			ID      string      `json:"id"`
			Context contextJSON `json:"context"`
			OK      bool        `json:"ok"`
			Error   string      `json:"error"`
			Phase   string      `json:"phase"`
			Path    string      `json:"path"`
		} `json:"contexts"`
		Materialized []struct {
			ID        string      `json:"id"`
			YAML      string      `json:"yaml"`
			Context   contextJSON `json:"context"`
			Collapsed string      `json:"collapsed_yaml"`
		} `json:"materialized"`
	}
	if err := json.Unmarshal(b, &file); err != nil {
		t.Fatal(err)
	}
	shape, g := smallConfig(t), boundGrammar(t)
	doc := parseCase(t, "context", "type: physical\nmtu: 1500\n")
	for _, c := range file.Contexts {
		t.Run("context/"+c.ID, func(t *testing.T) {
			ctx := c.Context.context()
			_, _, e1 := forms.Expand(doc, shape, accessKeys, g, ctx)
			_, e2 := forms.Collapse(doc, shape, accessKeys, g, nil, ctx)
			for _, err := range []error{e1, e2} {
				var fe *forms.Error
				switch {
				case c.OK && err != nil:
					t.Errorf("%v", err)
				case !c.OK && (!errors.As(err, &fe) || fe.Kind != forms.ErrContract || fe.Violations[0].Code != c.Error ||
					string(fe.Violations[0].Phase) != orDefault(c.Phase, "validate") || fe.Violations[0].LogicalPath != c.Path ||
					fe.Violations[0].Location != (forms.Location{NotApplicable: true})):
					t.Errorf("want %s/%s at %q, not_applicable; got %v", c.Error, orDefault(c.Phase, "validate"), c.Path, err)
				}
			}
		})
	}
	for _, c := range file.Materialized {
		t.Run("materialized/"+c.ID, func(t *testing.T) {
			ctx := c.Context.context()
			d := parseCase(t, c.ID, c.YAML)
			expanded, prov, err := forms.Expand(d, shape, accessKeys, g, ctx)
			if err != nil {
				t.Fatal(err)
			}
			got, err := forms.Collapse(expanded, shape, accessKeys, g, prov, ctx)
			if err != nil {
				t.Fatal(err)
			}
			if want := parseCase(t, c.ID, c.Collapsed); !got.Same(want) {
				t.Errorf("got %s\nwant %s", forms.Emit(got), forms.Emit(want))
			}
		})
	}
}

func orDefault(s, d string) string {
	if s == "" {
		return d
	}
	return s
}
