package forms

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

// The shared flat-materialization cases (testdata/p5/materialize.json, written by tools/p5/materialize_cases.py, whose expected projections and digests are
// computed by Python and not by this package). The Rust peer is rust/tests/materialize_corpus.rs.

type materializeFile struct {
	Grammar    string            `json:"grammar"`
	AccessKeys []string          `json:"access_keys"`
	Schemas    map[string]string `json:"schemas"`
	Cases      []struct {
		ID           string            `json:"id"`
		Schema       string            `json:"schema"`
		YAML         string            `json:"yaml"`
		Capabilities map[string]string `json:"capabilities"`
		Expect       struct {
			ExpectedKeys []string `json:"expected_keys"`
			Projection   string   `json:"projection"`
			OutputDigest string   `json:"output_digest"`
			Refused      []string `json:"refused"`
		} `json:"expect"`
	} `json:"cases"`
}

func loadMaterialize(t *testing.T) materializeFile {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	b, err := os.ReadFile(filepath.Join(root, "testdata", "p5", "materialize.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f materializeFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func compileMaterialize(t *testing.T, f materializeFile, schema string) *CompiledSchema {
	t.Helper()
	text := []byte(f.Schemas[schema])
	c, err := CompileSchema(SchemaInput{Bytes: text, SHA256: SHA256Hex(text), Grammar: f.Grammar, Surface: "config_surface"})
	if err != nil {
		t.Fatalf("%s: %v", schema, err)
	}
	return c
}

func TestSharedMaterializeCases(t *testing.T) {
	f := loadMaterialize(t)
	accepted, refused := 0, 0
	for _, c := range f.Cases {
		compiled := compileMaterialize(t, f, c.Schema)
		doc, err := Parse(c.YAML)
		if err != nil {
			t.Fatalf("%s: %v", c.ID, err)
		}
		got, err := MaterializeFlat(doc, compiled.Root, f.AccessKeys, c.Capabilities)
		if c.Expect.Refused != nil {
			refused++
			me, ok := err.(*MaterializeError)
			if !ok {
				t.Fatalf("%s: want a refusal, got %v", c.ID, err)
			}
			if !slices.Equal([]string{me.Reason, me.Field}, c.Expect.Refused) {
				t.Errorf("%s: got %q %q, want %v", c.ID, me.Reason, me.Field, c.Expect.Refused)
			}
			continue
		}
		accepted++
		if err != nil {
			t.Fatalf("%s: %v", c.ID, err)
		}
		if !slices.Equal(got.ExpectedKeys, c.Expect.ExpectedKeys) {
			t.Errorf("%s: expected keys %v, want %v", c.ID, got.ExpectedKeys, c.Expect.ExpectedKeys)
		}
		if got.ProjectionJSON != c.Expect.Projection {
			t.Errorf("%s: projection\n got %s\nwant %s", c.ID, got.ProjectionJSON, c.Expect.Projection)
		}
		if got.OutputDigest != c.Expect.OutputDigest {
			t.Errorf("%s: digest %s, want %s", c.ID, got.OutputDigest, c.Expect.OutputDigest)
		}
	}
	if accepted != 6 || refused != 13 {
		t.Fatalf("accepted %d refused %d, want 6 and 13", accepted, refused)
	}
}

func TestMaterializeNoDocumentAndErrorText(t *testing.T) {
	f := loadMaterialize(t)
	compiled := compileMaterialize(t, f, "flat")
	caps := map[string]string{}
	for _, k := range []string{"type", "enabled", "mtu", "label", "maybe"} {
		caps[k] = "implemented"
	}
	_, err := MaterializeFlat(nil, compiled.Root, nil, caps)
	me, ok := err.(*MaterializeError)
	if !ok || me.Reason != MaterializeNoDocument || me.Field != "" {
		t.Fatalf("got %v", err)
	}
	if me.Error() != "not materialized: no_document ()" {
		t.Fatalf("text %q", me.Error())
	}
}

func handBuilt(kind ShapeKind) Shape {
	return Shape{Kind: ShapeComposite, Fields: []NamedShape{{Name: "v", Shape: Shape{Kind: kind}}}}
}

func handDoc(v *Node) *Node {
	return &Node{Kind: KindMap, Entries: []Entry{{Key: "v", Value: v}}}
}

func materializeReason(t *testing.T, root Shape, doc *Node) (string, string) {
	t.Helper()
	_, err := MaterializeFlat(doc, root, []string{"__a"}, map[string]string{"v": "implemented"})
	me, ok := err.(*MaterializeError)
	if !ok {
		t.Fatalf("want a refusal, got %v", err)
	}
	return me.Reason, me.Field
}

func TestMaterializeRootThatIsNotAMappingIsNotFlat(t *testing.T) {
	if r, f := materializeReason(t, Shape{Kind: ShapeScalar}, nil); r != MaterializeNotFlat || f != "" {
		t.Fatalf("got %q %q", r, f)
	}
}

func TestMaterializeOpaqueValues(t *testing.T) {
	root := handBuilt(ShapeOpaque)
	for _, v := range []*Node{
		{Kind: KindMap, Entries: []Entry{{Key: "k", Value: &Node{Kind: KindInt, Int: 1}}}},
		{Kind: KindSeq, Items: []*Node{{Kind: KindInt, Int: 1}}},
	} {
		if r, f := materializeReason(t, root, handDoc(v)); r != MaterializeNotFlat || f != "v" {
			t.Fatalf("got %q %q", r, f)
		}
	}
	m, err := MaterializeFlat(handDoc(&Node{Kind: KindInt, Int: 7}), root, []string{"__a"}, map[string]string{"v": "implemented"})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"fields":[{"capability":"implemented","key":"v","provenance":"authored","value":7}]}`
	if m.ProjectionJSON != want {
		t.Fatalf("got %s", m.ProjectionJSON)
	}
	if r, f := materializeReason(t, root, handDoc(&Node{Kind: KindUnsupported, Str: "1.5"})); r != CanonicalUnsupportedValue || f != "" {
		t.Fatalf("got %q %q", r, f)
	}
}

// A Go map is read in a random order. The first undeclared key reported must be the smallest in byte order every time, so the call is repeated with several keys:
// a function that reads the map unsorted would answer a larger key in all but one run in a million.
func TestMaterializeFirstUndeclaredCapabilityIsTheSmallest(t *testing.T) {
	f := loadMaterialize(t)
	compiled := compileMaterialize(t, f, "flat")
	doc, err := Parse("type: t\n")
	if err != nil {
		t.Fatal(err)
	}
	caps := map[string]string{}
	for _, k := range []string{"type", "enabled", "mtu", "label", "maybe"} {
		caps[k] = "implemented"
	}
	for _, k := range []string{"u7", "u3", "u9", "u1", "u5", "u2", "u8", "u4", "u6"} {
		caps[k] = "implemented"
	}
	for i := 0; i < 200; i++ {
		_, err := MaterializeFlat(doc, compiled.Root, f.AccessKeys, caps)
		me, ok := err.(*MaterializeError)
		if !ok || me.Reason != MaterializeCapabilityUndeclared || me.Field != "u1" {
			t.Fatalf("run %d: got %v", i, err)
		}
	}
}

// A field whose name cannot be written as a path (here the empty name) has no origin of its own: two such fields would share the key "" and the provenance of the
// second would be read from the first. It is refused, with the field named.
func TestMaterializeFieldNameThatCannotBeAPath(t *testing.T) {
	root := Shape{Kind: ShapeComposite, Fields: []NamedShape{{Name: "", Shape: Shape{Kind: ShapeScalar, Scalar: ScalarString}}}}
	doc := &Node{Kind: KindMap, Entries: []Entry{{Key: "", Value: &Node{Kind: KindStr, Str: "v"}}}}
	_, err := MaterializeFlat(doc, root, []string{"__a"}, map[string]string{"": "implemented"})
	me, ok := err.(*MaterializeError)
	if !ok || me.Reason != MaterializeUnsupported || me.Field != "" {
		t.Fatalf("got %v", err)
	}
}
