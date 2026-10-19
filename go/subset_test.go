package forms_test

// The shared P1 subset vectors (testdata/p1/subset.json): what Parse reads, what it
// refuses and what Emit writes. The Rust peer reads the same file.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type typed struct {
	T string          `json:"t"`
	V json.RawMessage `json:"v"`
}

// treeOf reads {"t": "int", "v": 1} and friends, with maps as ordered [key, node] pairs.
func treeOf(t *testing.T, raw json.RawMessage) *forms.Node {
	t.Helper()
	var j typed
	if err := json.Unmarshal(raw, &j); err != nil {
		t.Fatal(err)
	}
	switch j.T {
	case "null":
		return &forms.Node{Kind: forms.KindNull}
	case "bool":
		var v bool
		must(t, json.Unmarshal(j.V, &v))
		return &forms.Node{Kind: forms.KindBool, Bool: v}
	case "int":
		var v int64
		must(t, json.Unmarshal(j.V, &v))
		return &forms.Node{Kind: forms.KindInt, Int: v}
	case "str":
		var v string
		must(t, json.Unmarshal(j.V, &v))
		return &forms.Node{Kind: forms.KindStr, Str: v}
	case "seq":
		var items []json.RawMessage
		must(t, json.Unmarshal(j.V, &items))
		n := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for _, it := range items {
			n.Items = append(n.Items, treeOf(t, it))
		}
		return n
	case "map":
		var pairs [][]json.RawMessage
		must(t, json.Unmarshal(j.V, &pairs))
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		for _, p := range pairs {
			var k string
			must(t, json.Unmarshal(p[0], &k))
			n.Entries = append(n.Entries, forms.Entry{Key: k, Value: treeOf(t, p[1])})
		}
		return n
	}
	t.Fatalf("unknown node type %q", j.T)
	return nil
}

func must(t testing.TB, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

type subset struct {
	ParseOK []struct {
		Name string          `json:"name"`
		YAML string          `json:"yaml"`
		Node json.RawMessage `json:"node"`
	} `json:"parse_ok"`
	Unsupported []refusal `json:"unsupported"`
	Syntax      []refusal `json:"syntax"`
	Emit        []struct {
		Name string          `json:"name"`
		Node json.RawMessage `json:"node"`
		YAML string          `json:"yaml"`
	} `json:"emit"`
}

type refusal struct {
	Name string `json:"name"`
	YAML string `json:"yaml"`
	Code string `json:"code"`
}

func vectors(t testing.TB) subset {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "testdata", "p1", "subset.json"))
	must(t, err)
	var s subset
	must(t, json.Unmarshal(b, &s))
	if len(s.ParseOK) == 0 || len(s.Unsupported) == 0 || len(s.Syntax) == 0 || len(s.Emit) == 0 {
		t.Fatal("a vector group is empty")
	}
	return s
}

func TestParseReadsTheSupportedSubset(t *testing.T) {
	for _, v := range vectors(t).ParseOK {
		got, err := forms.Parse(v.YAML)
		if err != nil {
			t.Fatalf("%s: %v", v.Name, err)
		}
		if !got.Same(treeOf(t, v.Node)) {
			t.Errorf("%s: got %+v", v.Name, got)
		}
	}
}

func TestParseRefusesWhatIsOutsideTheSubset(t *testing.T) {
	s := vectors(t)
	for _, v := range append(append([]refusal{}, s.Unsupported...), s.Syntax...) {
		_, err := forms.Parse(v.YAML)
		var fe *forms.Error
		if !errors.As(err, &fe) {
			t.Errorf("%s: expected a refusal, got %v", v.Name, err)
			continue
		}
		if fe.Code() != v.Code {
			t.Errorf("%s: got %s, want %s", v.Name, fe.Code(), v.Code)
		}
	}
}

func TestEmitWritesThePinnedTextAndItReadsBack(t *testing.T) {
	for _, v := range vectors(t).Emit {
		node := treeOf(t, v.Node)
		text := forms.Emit(node)
		if text != v.YAML {
			t.Errorf("%s: got %q, want %q", v.Name, text, v.YAML)
		}
		back, err := forms.Parse(text)
		if err != nil || !back.Same(node) {
			t.Errorf("%s: did not read back (%v)", v.Name, err)
		}
	}
}
