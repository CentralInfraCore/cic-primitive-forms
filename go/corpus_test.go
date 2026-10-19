package forms_test

// Runs the shared conformance corpus (corpus/*/vectors.yaml, ADR 0005) against the product
// code, the Go peer of rust/tests/corpus.rs. The corpus is decoded straight into Go values
// with the YAML library, not with the code under test.
//
// Implemented here: every case of kind recognize. Every other kind is counted and logged
// as not run, so a green run cannot be read as covering it; the supported subset is
// docs/P1-SUPPORTED-SUBSET.md.

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"gopkg.in/yaml.v3"
)

// accessKeys is the expanded key set A, read from the grammar pin by the corpus header.
// The library takes it as an argument; TestAMatchesTheCorpus ties this to the vectors.
var accessKeys = []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}

type obj = map[string]any

func suites(t *testing.T) []obj {
	t.Helper()
	files, err := filepath.Glob(filepath.Join("..", "corpus", "*", "vectors.yaml"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no corpus found: %v", err)
	}
	sort.Strings(files)
	var out []obj
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		var s obj
		if err := yaml.Unmarshal(b, &s); err != nil {
			t.Fatalf("%s: %v", f, err)
		}
		out = append(out, s)
	}
	return out
}

// nodeOf turns a corpus value into a position-less Node.
func nodeOf(t *testing.T, v any) *forms.Node {
	t.Helper()
	switch x := v.(type) {
	case nil:
		return &forms.Node{Kind: forms.KindNull}
	case bool:
		return &forms.Node{Kind: forms.KindBool, Bool: x}
	case int:
		return &forms.Node{Kind: forms.KindInt, Int: int64(x)}
	case string:
		return &forms.Node{Kind: forms.KindStr, Str: x}
	case []any:
		n := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for _, it := range x {
			n.Items = append(n.Items, nodeOf(t, it))
		}
		return n
	case obj:
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			n.Entries = append(n.Entries, forms.Entry{Key: k, Value: nodeOf(t, x[k])})
		}
		return n
	}
	t.Fatalf("unsupported corpus value %#v", v)
	return nil
}

func positionOf(t *testing.T, v any) forms.Position {
	t.Helper()
	if s, ok := v.(string); ok {
		switch s {
		case "scalar":
			return forms.Position{Kind: forms.PosScalar}
		case "opaque":
			return forms.Position{Kind: forms.PosOpaque}
		}
		t.Fatalf("unknown position %q", s)
	}
	m := v.(obj)
	if fields, ok := m["structured"]; ok {
		p := forms.Position{Kind: forms.PosStructured}
		for _, f := range fields.([]any) {
			p.Fields = append(p.Fields, f.(string))
		}
		return p
	}
	record := m["list_of"].(obj)["record"].(obj)
	names := make([]string, 0, len(record))
	for k := range record {
		names = append(names, k)
	}
	sort.Strings(names)
	p := forms.Position{Kind: forms.PosListOf}
	for _, name := range names {
		p.Record = append(p.Record, forms.RecordField{Name: name, Position: positionOf(t, record[name])})
	}
	return p
}

type wire struct{ code, phase, loc, path string }

func wireOf(v forms.Violation) wire {
	loc := "unavailable"
	if v.Location.Available {
		p := v.Location.Pos
		loc = fmt.Sprintf("%d:%d:%d", p.Line, p.Column, p.Offset)
	}
	return wire{v.Code, string(v.Phase), loc, v.LogicalPath}
}

func expectedWire(t *testing.T, e any) wire {
	t.Helper()
	m := e.(obj)
	for k := range m {
		if k != "code" && k != "phase" && k != "source_location" && k != "logical_path" {
			t.Fatalf("an error field this slice does not produce: %s", k)
		}
	}
	sl := m["source_location"].(obj)
	loc := "unavailable"
	if state, ok := sl["state"]; ok {
		if state != "unavailable" {
			t.Fatalf("state %v", state)
		}
	} else {
		loc = fmt.Sprintf("%d:%d:%d", sl["line"], sl["column"], sl["offset"])
	}
	path, _ := m["logical_path"].(string)
	return wire{m["code"].(string), m["phase"].(string), loc, path}
}

type run struct {
	class       string
	elements    []map[string]forms.Class
	opaqueValue bool
	errors      []wire
}

func runCase(t *testing.T, c obj, id string) run {
	t.Helper()
	pos := positionOf(t, c["schema"].(obj)["synthetic"].(obj)["position"])
	var node *forms.Node
	if src, ok := c["input_yaml"]; ok {
		n, err := forms.Parse(src.(string))
		if err != nil {
			var fe *forms.Error
			if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
				t.Fatalf("%s: outside the contract: %v", id, err)
			}
			r := run{errors: []wire{}}
			for _, v := range fe.Violations {
				r.errors = append(r.errors, wireOf(v))
			}
			return r
		}
		node = n
	} else {
		node = nodeOf(t, c["input"])
	}
	out, err := forms.Recognize(node, pos, accessKeys)
	if err != nil {
		t.Fatalf("%s: %v", id, err)
	}
	r := run{class: string(out.Class), elements: out.Elements, opaqueValue: out.OpaqueValue, errors: []wire{}}
	for _, v := range out.Violations {
		r.errors = append(r.errors, wireOf(v))
	}
	return r
}

func TestRecognizeCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	notRun := map[string]int{}
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id, kind := c["id"].(string), c["kind"].(string)
			if kind != "recognize" {
				// A shape-based validate case is run by TestPresenceValidateCasesMatchTheCorpus.
				synthetic, _ := c["schema"].(obj)["synthetic"].(obj)
				if _, shapeBased := synthetic["shape"]; !(kind == "validate" && shapeBased) {
					notRun[kind]++
				}
				continue
			}
			ran++
			got := runCase(t, c, id)
			expect := c["expect"].(obj)

			if want, ok := expect["class"]; ok && want != got.class {
				t.Errorf("%s: class: got %q, want %q", id, got.class, want)
			}
			if _, ok := expect["opaque_value"]; ok && !got.opaqueValue {
				t.Errorf("%s: opaque_value expected", id)
			}
			if els, ok := expect["elements"]; ok {
				want := []map[string]string{}
				for _, el := range els.([]any) {
					m := map[string]string{}
					for k, v := range el.(obj) {
						m[k] = v.(string)
					}
					want = append(want, m)
				}
				have := []map[string]string{}
				for _, el := range got.elements {
					m := map[string]string{}
					for k, v := range el {
						m[k] = string(v)
					}
					have = append(have, m)
				}
				if !reflect.DeepEqual(want, have) {
					t.Errorf("%s: elements: got %v, want %v", id, have, want)
				}
			}
			want := []wire{}
			if es, ok := expect["errors"]; ok {
				for _, e := range es.([]any) {
					want = append(want, expectedWire(t, e))
				}
			}
			if !reflect.DeepEqual(want, got.errors) {
				t.Errorf("%s: errors: got %v, want %v", id, got.errors, want)
			}
		}
	}
	t.Logf("recognize cases run: %d", ran)
	kinds := make([]string, 0, len(notRun))
	for k := range notRun {
		kinds = append(kinds, k)
	}
	sort.Strings(kinds)
	for _, k := range kinds {
		t.Logf("NOT RUN: kind %s: %d cases", k, notRun[k])
	}
	if ran < 30 {
		t.Fatalf("the corpus has at least 30 recognize cases, ran %d", ran)
	}
}

func TestAMatchesTheCorpus(t *testing.T) {
	for _, suite := range suites(t) {
		if suite["suite"] != "recognition" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["id"] != "recognition.full-key-set-expanded" {
				continue
			}
			var keys []string
			for k := range c["input"].(obj) {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			mine := append([]string{}, accessKeys...)
			sort.Strings(mine)
			if !reflect.DeepEqual(keys, mine) {
				t.Fatalf("A: corpus %v, test constant %v", keys, mine)
			}
			return
		}
	}
	t.Fatal("the vector that spells A was not found")
}

// Every document the recognize cases feed in survives Emit then Parse.
func TestCorpusDocumentsSurviveEmitAndParse(t *testing.T) {
	checked := 0
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["kind"] != "recognize" {
				continue
			}
			var node *forms.Node
			if v, ok := c["input"]; ok {
				node = nodeOf(t, v)
			} else if src, ok := c["input_yaml"]; ok {
				n, err := forms.Parse(src.(string))
				if err != nil {
					continue // a parse-phase vector has no document
				}
				node = n
			} else {
				t.Fatalf("%v: no input", c["id"])
			}
			back, err := forms.Parse(forms.Emit(node))
			if err != nil || !back.Same(node) {
				t.Errorf("%v: emit/parse changed the value (%v)", c["id"], err)
			}
			checked++
		}
	}
	if checked < 25 {
		t.Fatalf("checked only %d", checked)
	}
}

// ---------------------------------------------------------------------------
// kind validate against schema.synthetic.shape (the presence suite, ADR 0003).
//
// The fixture notation is bound here to the members of the Shape: type string/integer/boolean to
// shape_type scalar with a scalar_type, list to a collection with min_items, composite or a bare
// fields to a composite, and a child written as just mandatory/optional to an opaque field. No
// schema of its own is defined; the library takes the Shape members only.
// ---------------------------------------------------------------------------

func presenceOf(t *testing.T, v any) forms.Presence {
	t.Helper()
	switch v {
	case "mandatory":
		return forms.Mandatory
	case "optional":
		return forms.Optional
	}
	t.Fatalf("unknown presence %v", v)
	return 0
}

func shapeOf(t *testing.T, v any) forms.Shape {
	t.Helper()
	if p, ok := v.(string); ok {
		// A child written as just its presence: any value, nothing below it.
		return forms.Shape{Kind: forms.ShapeOpaque, Presence: presenceOf(t, p)}
	}
	m := v.(obj)
	s := forms.Shape{Presence: presenceOf(t, m["presence"])}
	s.Nullable, _ = m["nullable"].(bool)
	_, s.HasDefault = m["default"]
	typ, hasType := m["type"].(string)
	switch {
	case hasType && typ == "string":
		s.Kind, s.Scalar = forms.ShapeScalar, forms.ScalarString
	case hasType && typ == "integer":
		s.Kind, s.Scalar = forms.ShapeScalar, forms.ScalarInteger
	case hasType && typ == "boolean":
		s.Kind, s.Scalar = forms.ShapeScalar, forms.ScalarBoolean
	case hasType && typ == "list":
		s.Kind = forms.ShapeList
		if n, ok := m["min_items"].(int); ok {
			s.MinItems, s.HasMinItems = n, true
		}
	case (!hasType || typ == "composite") && m["fields"] != nil:
		s.Kind = forms.ShapeComposite
		names := []string{}
		for name := range m["fields"].(obj) {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			s.Fields = append(s.Fields, forms.NamedShape{Name: name, Shape: shapeOf(t, m["fields"].(obj)[name])})
		}
	case !hasType:
		s.Kind = forms.ShapeOpaque
	default:
		t.Fatalf("unsupported fixture type %q", typ)
	}
	return s
}

func TestPresenceValidateCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	notRun := 0
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["kind"] != "validate" {
				continue
			}
			id := c["id"].(string)
			synthetic, _ := c["schema"].(obj)["synthetic"].(obj)
			fixture, ok := synthetic["shape"]
			if !ok {
				notRun++
				continue
			}
			ran++
			shape := shapeOf(t, fixture)
			var node *forms.Node
			if src, ok := c["input_yaml"]; ok {
				n, err := forms.Parse(src.(string))
				if err != nil {
					t.Fatalf("%s: %v", id, err)
				}
				node = n
			} else if _, absent := c["absent"]; !absent {
				node = nodeOf(t, c["input"])
			}
			var before *forms.Node
			if node != nil {
				before = cloneNode(node)
			}
			got, err := forms.Validate(node, shape, accessKeys)
			if err != nil {
				t.Fatalf("%s: %v", id, err)
			}
			if node != nil && !reflect.DeepEqual(node, before) {
				t.Errorf("%s: validation changed its input", id)
			}
			expect := c["expect"].(obj)
			want := []wire{}
			if es, ok := expect["errors"]; ok {
				for _, e := range es.([]any) {
					want = append(want, expectedWire(t, e))
				}
			}
			have := []wire{}
			for _, v := range got.Violations {
				have = append(have, wireOf(v))
			}
			if !reflect.DeepEqual(want, have) {
				t.Errorf("%s: errors: got %v, want %v", id, have, want)
			}
			if v, ok := expect["valid"].(bool); ok && v != (len(have) == 0) {
				t.Errorf("%s: valid: got %v, want %v", id, len(have) == 0, v)
			}
			if want, ok := expect["default_candidate"].(bool); ok && want != slices.Contains(got.DefaultCandidates, "$") {
				t.Errorf("%s: default_candidate: want %v", id, want)
			}
			if _, ok := expect["stays_absent"]; ok && !(node == nil && len(have) == 0) {
				t.Errorf("%s: stays_absent expected", id)
			}
			for _, key := range []string{"kind", "is_inherit", "preserved", "upstream_conflict"} {
				if _, ok := expect[key]; ok {
					t.Errorf("%s: expectation %s is not a presence one", id, key)
				}
			}
		}
	}
	t.Logf("presence validate cases run: %d", ran)
	t.Logf("NOT RUN: validate (slot or pin): %d cases", notRun)
	if ran != 29 {
		t.Fatalf("the presence suite has 29 shape-based validate cases, ran %d", ran)
	}
}

// cloneNode is a deep copy, to prove validation leaves its input alone.
func cloneNode(n *forms.Node) *forms.Node {
	c := *n
	if n.Pos != nil {
		p := *n.Pos
		c.Pos = &p
	}
	if n.Items != nil {
		c.Items = []*forms.Node{}
	}
	for _, it := range n.Items {
		c.Items = append(c.Items, cloneNode(it))
	}
	if n.Entries != nil {
		c.Entries = []forms.Entry{}
	}
	for _, e := range n.Entries {
		ce := forms.Entry{Key: e.Key, Value: cloneNode(e.Value)}
		if e.KeyPos != nil {
			p := *e.KeyPos
			ce.KeyPos = &p
		}
		c.Entries = append(c.Entries, ce)
	}
	return &c
}
