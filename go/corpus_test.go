package forms_test

// Runs the shared conformance corpus (corpus/*/vectors.yaml, ADR 0005) against the product
// code, the Go peer of rust/tests/corpus.rs. The corpus is decoded straight into Go values
// with the YAML library, not with the code under test.
//
// Implemented here: every case of kind recognize. Every other kind is counted and logged
// as not run, so a green run cannot be read as covering it; the supported subset is
// docs/P1-SUPPORTED-SUBSET.md.

import (
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"testing"
	"unicode/utf8"

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
	if v.Location.NotApplicable {
		loc = "not_applicable"
	}
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
		if state != "unavailable" && state != "not_applicable" {
			t.Fatalf("state %v", state)
		}
		loc = state.(string)
	} else {
		loc = fmt.Sprintf("%d:%d:%d", sl["line"], sl["column"], sl["offset"])
	}
	path, _ := m["logical_path"].(string)
	return wire{m["code"].(string), m["phase"].(string), loc, path}
}

// hasRunner says whether a validate case is run by a runner below: a presence case
// (schema.synthetic.shape) or an inheritance case (slot inherit_key, inherit_policy or access_modify).
func hasRunner(c obj) bool {
	synthetic, _ := c["schema"].(obj)["synthetic"].(obj)
	if _, ok := synthetic["shape"]; ok {
		return true
	}
	slot, _ := synthetic["slot"].(string)
	return slot == "inherit_key" || slot == "inherit_policy" || slot == "access_modify"
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
				// The validate cases that have a runner of their own are counted there.
				if !(kind == "validate" && hasRunner(c)) && !isPathRun(kind) && !isP2Run(kind) {
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
		if el, ok := m["elements"].(obj); ok {
			s.Kind, s.Elements = forms.ShapeDeclaredList, elementsOf(t, el)
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

// elementsOf reads the fixture notation of the element structure of a list: one case (key, fields) or cases.
func elementsOf(t *testing.T, el obj) *forms.Elements {
	t.Helper()
	read := func(name string, c obj) forms.Case {
		var key []string
		for _, k := range c["key"].([]any) {
			key = append(key, k.(string))
		}
		fields := c["fields"].(obj)
		names := []string{}
		for n := range fields {
			names = append(names, n)
		}
		sort.Strings(names)
		out := forms.Case{Name: name, Key: key}
		for _, n := range names {
			out.Fields = append(out.Fields, forms.NamedShape{Name: n, Shape: shapeOf(t, fields[n])})
		}
		return out
	}
	if cases, ok := el["cases"].([]any); ok {
		els := &forms.Elements{Choice: true}
		for _, raw := range cases {
			c := raw.(obj)
			els.Cases = append(els.Cases, read(c["name"].(string), c))
		}
		return els
	}
	return &forms.Elements{Cases: []forms.Case{read("", el)}}
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
	t.Logf("NOT RUN here: validate, not a presence case (see the accounting test): %d cases", notRun)
	if ran != 37 {
		t.Fatalf("the presence and lists suites have 37 shape-based validate cases, ran %d", ran)
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

// ---------------------------------------------------------------------------
// kind validate against schema.synthetic.slot (the inherit suite, ADR 0004, cic-primitives D-018).
//
// The Access grammar version is derived from the VERIFIED pinned schema: the bytes of
// schemas/atomic/access.yaml come from a checkout of cic-primitives at the pinned commit
// (CIC_PRIMITIVES_ROOT), their SHA-256 must equal the one in corpus/pins.yaml, and only then is
// spec.access_grammar.version read. The processing context declares the grammar the corpus is written
// in. A missing checkout is a failure, not a skip: nothing silently falls back to a default version.
// ---------------------------------------------------------------------------

// corpusGrammar is the grammar the inherit vectors are written in (true | false | 0).
const corpusGrammar = 1

const accessSchema = "schemas/atomic/access.yaml"

func pinnedAccessSHA256(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "corpus", "pins.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var pins obj
	if err := yaml.Unmarshal(b, &pins); err != nil {
		t.Fatal(err)
	}
	grammar := pins["grammar"].(obj)
	if len(grammar) != 1 {
		t.Fatalf("exactly one grammar pin, found %d", len(grammar))
	}
	for _, pin := range grammar {
		for _, f := range pin.(obj)["files"].([]any) {
			if f.(obj)["path"] == accessSchema {
				return f.(obj)["sha256"].(string)
			}
		}
	}
	t.Fatal("the pin does not list access.yaml")
	return ""
}

func boundGrammar(t *testing.T) forms.Grammar {
	t.Helper()
	root := os.Getenv("CIC_PRIMITIVES_ROOT")
	if root == "" {
		t.Fatal("CIC_PRIMITIVES_ROOT must point at a checkout of CentralInfraCore/cic-primitives at the pinned commit " +
			"(python3 tools/grammar_identity.py verify --pins corpus/pins.yaml --repo DIR checks it)")
	}
	b, err := os.ReadFile(filepath.Join(root, accessSchema))
	if err != nil {
		t.Fatal(err)
	}
	pinned, err := forms.PinnedGrammarVersion(b, pinnedAccessSHA256(t))
	if err != nil {
		t.Fatalf("the pinned schema does not verify: %v", err)
	}
	g, err := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: corpusGrammar}, pinned)
	if err != nil {
		t.Fatalf("the context does not agree with the pin: %v", err)
	}
	return g
}

func typedOf(n *forms.Node) (string, string) {
	switch n.Kind {
	case forms.KindNull:
		return "null", "null"
	case forms.KindBool:
		return "boolean", fmt.Sprint(n.Bool)
	case forms.KindInt:
		return "integer", fmt.Sprint(n.Int)
	case forms.KindStr:
		return "string", n.Str
	case forms.KindSeq:
		return "list", "[]"
	}
	return "map", "{}"
}

func TestInheritValidateCasesMatchTheCorpus(t *testing.T) {
	grammar := boundGrammar(t)
	if grammar.Version() != corpusGrammar {
		t.Fatalf("bound %d", grammar.Version())
	}
	ran := 0
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["kind"] != "validate" {
				continue
			}
			id := c["id"].(string)
			synthetic, _ := c["schema"].(obj)["synthetic"].(obj)
			slot, _ := synthetic["slot"].(string)
			if slot != "inherit_key" && slot != "inherit_policy" && slot != "access_modify" {
				continue // a conformance slot is ADR 0010, not this slice
			}
			ran++
			node := nodeOf(t, c["input"])
			before := cloneNode(node)
			expect := c["expect"].(obj)
			var violation *forms.Violation
			var preservedType, preservedValue, kind string
			isInherit := false
			if slot == "access_modify" {
				rule, v := forms.ReadRule(node)
				violation = v
				if v == nil {
					kind, isInherit = "list", rule.Symbol
					if rule.Symbol {
						kind = "symbol"
					}
				}
			} else {
				mode, v := forms.ReadMode(node, grammar)
				violation = v
				if v == nil {
					written, err := forms.WriteMode(mode, grammar)
					if err != nil {
						t.Fatalf("%s: %v", id, err)
					}
					preservedType, preservedValue = typedOf(written)
				}
			}
			if !reflect.DeepEqual(node, before) {
				t.Errorf("%s: reading changed its input", id)
			}
			have := []wire{}
			if violation != nil {
				have = append(have, wireOf(*violation))
			}
			want := []wire{}
			if es, ok := expect["errors"]; ok {
				for _, e := range es.([]any) {
					want = append(want, expectedWire(t, e))
				}
			}
			if !reflect.DeepEqual(want, have) {
				t.Errorf("%s: errors: got %v, want %v", id, have, want)
			}
			if v, ok := expect["valid"].(bool); ok && v != (violation == nil) {
				t.Errorf("%s: valid: got %v, want %v", id, violation == nil, v)
			}
			if p, ok := expect["preserved"].(obj); ok {
				if preservedType != p["type"] || preservedValue != fmt.Sprint(p["value"]) {
					t.Errorf("%s: preserved: got %s/%s, want %v/%v", id, preservedType, preservedValue, p["type"], p["value"])
				}
			}
			if k, ok := expect["kind"].(string); ok && k != kind {
				t.Errorf("%s: kind: got %q, want %q", id, kind, k)
			}
			if i, ok := expect["is_inherit"].(bool); ok && (kind == "" || i != isInherit) {
				t.Errorf("%s: is_inherit: got %v, want %v", id, isInherit, i)
			}
			if _, ok := expect["upstream_conflict"]; ok {
				t.Errorf("%s: D-018 removed the upstream conflict; the expectation must not return", id)
			}
		}
	}
	t.Logf("inherit validate cases run: %d", ran)
	if ran != 21 {
		t.Fatalf("the inherit suite has 21 slot-based validate cases of the access-policy kind, ran %d", ran)
	}
}

// The accounting is exact: every case of the corpus is either run by one of the runners of this file or
// counted as NOT RUN, and the numbers are the ones in corpus/accounting.yaml (written in advance, never computed from a run).
func TestTheRunAndNotRunAccountingIsExact(t *testing.T) {
	total, ran, skipped := 0, 0, 0
	run, notRun := map[string]int{}, map[string]int{}
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			total++
			kind := c["kind"].(string)
			key := suite["suite"].(string) + "/" + kind
			if kind == "recognize" || (kind == "validate" && hasRunner(c)) || isPathRun(kind) || isP2Run(kind) || kind == "source_span" || kind == "edit" {
				run[key]++
				ran++
			} else {
				notRun[key]++
				skipped++
			}
		}
	}
	for k, n := range run {
		t.Logf("RUN     %s: %d", k, n)
	}
	for k, n := range notRun {
		t.Logf("NOT RUN %s: %d", k, n)
	}
	want := readAccounting(t)
	wantRun, wantNotRun := 0, 0
	for _, k := range want.Kinds {
		wantRun, wantNotRun = wantRun+k.Run, wantNotRun+k.NotRun
	}
	if ran+skipped != total || wantRun != ran || wantNotRun != skipped {
		t.Fatalf("accounting: %d run + %d not run of %d, corpus/accounting.yaml says %d + %d", ran, skipped, total, wantRun, wantNotRun)
	}
	keys := map[string]bool{}
	for k := range run {
		keys[k] = true
	}
	for k := range notRun {
		keys[k] = true
	}
	for k := range want.Kinds {
		keys[k] = true
	}
	for k := range keys {
		if got, exp := (counts{run[k], notRun[k]}), (counts{want.Kinds[k].Run, want.Kinds[k].NotRun}); got != exp {
			t.Errorf("%s: %d run and %d not run, corpus/accounting.yaml says %d and %d", k, got.run, got.notRun, exp.run, exp.notRun)
		}
	}
}

type counts struct{ run, notRun int }

// accounting is corpus/accounting.yaml: the numbers the runners are expected to reach. It is written by hand, in advance, and never computed from a run.
type accounting struct {
	Format string `yaml:"format"`
	Kinds  map[string]struct {
		Run    int `yaml:"run"`
		NotRun int `yaml:"not_run"`
	} `yaml:"kinds"`
}

func readAccounting(t *testing.T) accounting {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "corpus", "accounting.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var a accounting
	if err := yaml.Unmarshal(b, &a); err != nil {
		t.Fatal(err)
	}
	if a.Format != "cic-accounting/1" {
		t.Fatalf("corpus/accounting.yaml: format %q", a.Format)
	}
	return a
}

// ---------------------------------------------------------------------------
// path_of and path_stability (ADR 0006): the identity of a list element in a path.

func isPathRun(kind string) bool {
	return kind == "path_of" || kind == "path_stability" || kind == "path_parse" || kind == "path_format" || kind == "path_resolve"
}

// isP2Run says whether a case is run by the expand/collapse runner: the four P2 kinds.
func isP2Run(kind string) bool {
	return kind == "expand" || kind == "collapse" || kind == "roundtrip" || kind == "equality"
}

// treeShape reads the synthetic tree notation of corpus/paths/vectors.yaml into a Shape. In the path fixtures
// the key fields are the only mandatory fields (ADR 0006), so the fields of a list element are optional
// except the key; the fields of a composite are optional.
func treeShape(t *testing.T, v any, mandatory bool) forms.Shape {
	t.Helper()
	pres := forms.Optional
	if mandatory {
		pres = forms.Mandatory
	}
	if s, ok := v.(string); ok && s == "opaque" {
		return forms.Shape{Kind: forms.ShapeOpaque, Presence: pres}
	}
	m := v.(obj)
	switch {
	case m["scalar"] != nil:
		sc := forms.ScalarString
		switch m["scalar"] {
		case "integer":
			sc = forms.ScalarInteger
		case "boolean":
			sc = forms.ScalarBoolean
		}
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: sc, Presence: pres}
	case m["set"] != nil:
		return forms.Shape{Kind: forms.ShapeSet, Scalar: forms.ScalarString, Presence: pres}
	case m["composite"] != nil:
		return forms.Shape{Kind: forms.ShapeComposite, Fields: treeFields(t, m["composite"].(obj), nil), Presence: pres}
	}
	list := m["list"].(obj)
	els := &forms.Elements{}
	if choice, ok := list["choice"]; ok {
		els.Choice = true
		for _, raw := range choice.([]any) {
			cs := raw.(obj)
			var key []string
			for _, k := range cs["key"].([]any) {
				key = append(key, k.(string))
			}
			els.Cases = append(els.Cases, forms.Case{Name: cs["name"].(string), Fields: treeFields(t, cs["fields"].(obj), key), Key: key})
		}
	} else {
		var key []string
		for _, k := range list["key"].([]any) {
			key = append(key, k.(string))
		}
		els.Cases = []forms.Case{{Fields: treeFields(t, list["fields"].(obj), key), Key: key}}
	}
	return forms.Shape{Kind: forms.ShapeDeclaredList, Elements: els, Presence: pres}
}

func treeFields(t *testing.T, fields obj, key []string) []forms.NamedShape {
	t.Helper()
	names := make([]string, 0, len(fields))
	for k := range fields {
		names = append(names, k)
	}
	sort.Strings(names)
	var out []forms.NamedShape
	for _, name := range names {
		out = append(out, forms.NamedShape{Name: name, Shape: treeShape(t, fields[name], slices.Contains(key, name))})
	}
	return out
}

func rootOf(t *testing.T, suite obj, name string) forms.Shape {
	t.Helper()
	return forms.Shape{Kind: forms.ShapeComposite, Fields: treeFields(t, suite["trees"].(obj)[name].(obj), nil), Presence: forms.Mandatory}
}

func stepsOf(at any) []forms.Step {
	var out []forms.Step
	for _, s := range at.([]any) {
		if n, ok := s.(int); ok {
			out = append(out, forms.Step{Index: n, IsIndex: true})
		} else {
			out = append(out, forms.Step{Field: s.(string)})
		}
	}
	return out
}

// pathOutcome is what PathOf says: a path, or the wire form of its one error.
func pathOutcome(t *testing.T, id string, doc *forms.Node, root forms.Shape, at any) (string, []wire) {
	t.Helper()
	p, err := forms.PathOf(doc, root, accessKeys, stepsOf(at))
	if err == nil {
		return p, nil
	}
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		t.Fatalf("%s: outside the contract: %v", id, err)
	}
	var ws []wire
	for _, v := range fe.Violations {
		ws = append(ws, wireOf(v))
	}
	return "", ws
}

func documentOf(t *testing.T, c obj) *forms.Node {
	t.Helper()
	if src, ok := c["document_yaml"]; ok {
		n, err := forms.Parse(src.(string))
		if err != nil {
			t.Fatal(err)
		}
		return n
	}
	return nodeOf(t, c["document"])
}

func expectedPathWires(t *testing.T, expect obj) (string, []wire) {
	t.Helper()
	if p, ok := expect["path"]; ok {
		return p.(string), nil
	}
	var ws []wire
	for _, e := range expect["errors"].([]any) {
		w := e.(obj)
		ws = append(ws, wire{code: w["code"].(string), phase: w["phase"].(string), loc: "unavailable", path: w["logical_path"].(string)})
	}
	return "", ws
}

func TestPathCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	for _, suite := range suites(t) {
		if suite["suite"] != "paths" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id, kind := c["id"].(string), c["kind"].(string)
			if kind != "path_of" && kind != "path_stability" {
				continue
			}
			ran++
			root := rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string))
			wantPath, wantErr := expectedPathWires(t, c["expect"].(obj))
			if kind == "path_of" {
				path, errs := pathOutcome(t, id, documentOf(t, c), root, c["at"])
				if path != wantPath || !reflect.DeepEqual(errs, wantErr) {
					t.Errorf("%s: got %q %v, want %q %v", id, path, errs, wantPath, wantErr)
				}
				continue
			}
			for i, v := range c["variants"].([]any) {
				vc := v.(obj)
				path, errs := pathOutcome(t, id, documentOf(t, vc), root, vc["at"])
				if path != wantPath || !reflect.DeepEqual(errs, wantErr) {
					t.Errorf("%s variant %d: got %q %v, want %q %v", id, i, path, errs, wantPath, wantErr)
				}
			}
		}
	}
	if ran != 25 {
		t.Fatalf("the corpus has 18 path_of and 7 path_stability cases, ran %d", ran)
	}
}

// ---------------------------------------------------------------------------
// expand, collapse, roundtrip, equality (ADR 0001 "Round trip", ADR 0003, ADR 0010): one primitive position.

// typedMatches says whether n is the typed value a fixture spells ({type, value}).
func typedMatches(t *testing.T, n *forms.Node, want any) bool {
	t.Helper()
	w := want.(obj)
	if w["type"] == "absent" {
		return n == nil
	}
	if n == nil {
		return false
	}
	return n.Same(nodeOf(t, w["value"]))
}

func fieldsMatch(t *testing.T, id string, n *forms.Node, fields any) {
	t.Helper()
	for name, want := range fields.(obj) {
		var got *forms.Node
		for _, e := range n.Entries {
			if e.Key == name {
				got = e.Value
			}
		}
		if !typedMatches(t, got, want) {
			t.Errorf("%s: field %s does not match %v", id, name, want)
		}
	}
}

func TestExpandCollapseCasesMatchTheCorpus(t *testing.T) {
	g := boundGrammar(t)
	ran := 0
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id, kind := c["id"].(string), c["kind"].(string)
			if !isP2Run(kind) {
				continue
			}
			ran++
			expect := c["expect"].(obj)
			switch kind {
			case "equality":
				a, b := nodeOf(t, c["a"]), nodeOf(t, c["b"])
				if a.Same(b) != expect["equal"].(bool) {
					t.Errorf("%s: equal: got %v", id, !expect["equal"].(bool))
				}
			case "expand":
				out, authored, err := forms.ExpandPosition(nodeOf(t, c["input"]), accessKeys, g)
				if err != nil || authored {
					t.Fatalf("%s: %v authored=%v", id, err, authored)
				}
				fieldsMatch(t, id, out, expect["fields"])
			case "collapse":
				in := nodeOf(t, c["input"])
				basis, _ := c["basis"].(string)
				out, err := forms.CollapsePosition(in, c["authored_expanded"].(bool), basis, accessKeys, g)
				if err != nil {
					t.Fatalf("%s: %v", id, err)
				}
				got := "short"
				if out.Kind == forms.KindMap && len(out.Entries) == len(accessKeys) && out.Same(in) {
					got = "expanded"
				}
				if got != expect["collapse"] {
					t.Errorf("%s: collapse: got %s, want %v", id, got, expect["collapse"])
				}
				if f, ok := expect["fields"]; ok {
					fieldsMatch(t, id, out, f)
				}
			case "roundtrip":
				var in *forms.Node
				if _, absent := c["absent"]; !absent {
					in = nodeOf(t, c["input"])
				}
				if in == nil {
					// absent stays absent: there is no node to expand or collapse
					if !typedMatches(t, nil, expect["collapsed"]) || !typedMatches(t, nil, expect["expanded"]) {
						t.Errorf("%s: absent must stay absent", id)
					}
					continue
				}
				expanded, authored, err := forms.ExpandPosition(in, accessKeys, g)
				if err != nil {
					t.Fatalf("%s: %v", id, err)
				}
				back, err := forms.CollapsePosition(expanded, authored, "", accessKeys, g)
				if err != nil {
					t.Fatalf("%s: %v", id, err)
				}
				if f, ok := expect["fields"]; ok {
					fieldsMatch(t, id, back, f)
					continue
				}
				value := expanded
				for _, e := range expanded.Entries {
					if e.Key == "value" {
						value = e.Value
					}
				}
				if !typedMatches(t, value, expect["expanded"]) || !typedMatches(t, back, expect["collapsed"]) {
					t.Errorf("%s: the round trip does not give back %v / %v", id, expect["expanded"], expect["collapsed"])
				}
			}
		}
	}
	if ran != 26 {
		t.Fatalf("the corpus has 26 expand, collapse, roundtrip and equality cases, ran %d", ran)
	}
}

// ---------------------------------------------------------------------------
// path_parse, path_format, path_resolve (ADR 0006): the text of logical paths.

func segmentsOf(t *testing.T, raw any) []forms.PathSeg {
	t.Helper()
	out := []forms.PathSeg{}
	for _, r := range raw.([]any) {
		m := r.(obj)
		if f, ok := m["field"]; ok {
			out = append(out, forms.PathSeg{Field: f.(string)})
			continue
		}
		item := []forms.KeyComp{}
		for _, c := range m["item"].([]any) {
			cm := c.(obj)
			key := cm["key"].(obj)
			comp := forms.KeyComp{Name: cm["name"].(string)}
			switch key["type"] {
			case "string":
				comp.Str = key["value"].(string)
			case "integer":
				comp.IsInt = true
				switch n := key["value"].(type) {
				case int:
					comp.Int = int64(n)
				default:
					comp.Bad = "range" // beyond signed 64 bits
				}
			default:
				comp.Bad = "boolean"
			}
			item = append(item, comp)
		}
		out = append(out, forms.PathSeg{Item: item, IsItem: true})
	}
	return out
}

func segmentsWire(segs []forms.PathSeg) []any {
	out := []any{}
	for _, s := range segs {
		if !s.IsItem {
			out = append(out, obj{"field": s.Field})
			continue
		}
		item := []any{}
		for _, c := range s.Item {
			if c.IsInt {
				item = append(item, obj{"name": c.Name, "key": obj{"type": "integer", "value": int(c.Int)}})
			} else {
				item = append(item, obj{"name": c.Name, "key": obj{"type": "string", "value": c.Str}})
			}
		}
		out = append(out, obj{"item": item})
	}
	return out
}

func TestPathTextCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	for _, suite := range suites(t) {
		if suite["suite"] != "paths" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id, kind := c["id"].(string), c["kind"].(string)
			if kind != "path_parse" && kind != "path_format" && kind != "path_resolve" {
				continue
			}
			ran++
			expect := c["expect"].(obj)
			var perr *forms.PathError
			switch kind {
			case "path_parse":
				segs, err := forms.ParsePath(c["path"].(string))
				perr = err
				if err == nil && !reflect.DeepEqual(segmentsWire(segs), expect["segments"]) {
					t.Errorf("%s: segments\n got %v\nwant %v", id, segmentsWire(segs), expect["segments"])
				}
				if err == nil {
					// every accepted text is the canonical writing of its segments
					if text, ferr := forms.FormatPath(segs); ferr != nil || text != c["path"].(string) {
						t.Errorf("%s: format(parse(t)) = %q (%v), want %q", id, text, ferr, c["path"])
					}
				}
			case "path_format":
				text, err := forms.FormatPath(segmentsOf(t, c["segments"]))
				perr = err
				if err == nil && text != expect["text"] {
					t.Errorf("%s: text %q, want %q", id, text, expect["text"])
				}
			default:
				segs, err := forms.ParsePath(c["path"].(string))
				if err != nil {
					t.Fatalf("%s: the path to resolve does not parse: %v", id, err)
				}
				res, err := forms.ResolvePath(rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string)), segs)
				perr = err
				if err == nil && (res.Target != expect["target"] || res.Primitive != expect["primitive_position"]) {
					t.Errorf("%s: resolved %+v, want %v/%v", id, res, expect["target"], expect["primitive_position"])
				}
			}
			errs, wantErr := expect["errors"].([]any)
			switch {
			case wantErr && perr == nil:
				t.Errorf("%s: want an error, got none", id)
			case !wantErr && perr != nil:
				t.Errorf("%s: unexpected %+v", id, perr)
			case wantErr:
				e := errs[0].(obj)
				if perr.Code != e["code"] || string(perr.Phase) != e["phase"] || perr.LogicalPath != func() string { s, _ := e["logical_path"].(string); return s }() {
					t.Errorf("%s: got %+v, want %v", id, perr, e)
				}
				if off, ok := e["path_offset"]; ok && perr.Offset != off {
					t.Errorf("%s: offset %d, want %v", id, perr.Offset, off)
				}
				if sl := e["source_location"].(obj); sl["state"] != "not_applicable" {
					t.Errorf("%s: a path error has no source location", id)
				}
			}
		}
	}
	if ran != 100 {
		t.Fatalf("the corpus has 57 path_parse, 9 path_format and 34 path_resolve cases, ran %d", ran)
	}
}

// ---------------------------------------------------------------------------
// source_span (ADR 0012, step E2): where the scalar of a position is, in the original text.

// sourceOf is the source text of a case: input_yaml, or the bytes of input_hex (what a text field cannot spell: a lone CR, a BOM).
func sourceOf(t *testing.T, c obj) string {
	t.Helper()
	if s, ok := c["input_yaml"]; ok {
		return s.(string)
	}
	b, err := hex.DecodeString(c["input_hex"].(string))
	if err != nil || !utf8.Valid(b) {
		t.Fatalf("input_hex: %v", err)
	}
	return string(b)
}

func expectedEditWires(t *testing.T, expect obj) []wire {
	t.Helper()
	var ws []wire
	for _, e := range expect["errors"].([]any) {
		ws = append(ws, expectedWire(t, e))
	}
	return ws
}

func TestSourceSpanCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	for _, suite := range suites(t) {
		if suite["suite"] != "edit" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["kind"] != "source_span" {
				continue
			}
			ran++
			id := c["id"].(string)
			root := rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string))
			got, err := forms.SourceSpan(sourceOf(t, c), root, c["path"].(string), accessKeys)
			expect := c["expect"].(obj)
			if want, ok := expect["span"].(obj); ok {
				if err != nil {
					t.Errorf("%s: a span was expected, got %v", id, err)
					continue
				}
				if got.Start != want["start"] || got.End != want["end"] || string(got.Style) != want["style"] {
					t.Errorf("%s: got %v, want %v", id, got, want)
				}
				continue
			}
			var fe *forms.Error
			if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
				t.Errorf("%s: an error of the contract was expected, got %v", id, err)
				continue
			}
			var ws []wire
			for _, v := range fe.Violations {
				ws = append(ws, wireOf(v))
			}
			if !reflect.DeepEqual(ws, expectedEditWires(t, expect)) {
				t.Errorf("%s: got %v, want %v", id, ws, expectedEditWires(t, expect))
			}
		}
	}
	if ran != 28 {
		t.Fatalf("the 28 source_span cases of corpus/edit, ran %d", ran)
	}
}

func editRoot(t *testing.T) forms.Shape {
	t.Helper()
	for _, suite := range suites(t) {
		if suite["suite"] == "edit" {
			return rootOf(t, suite, "doc")
		}
	}
	t.Fatal("the edit suite")
	return forms.Shape{}
}

// spanOf is the span of a path, or the wires of the refusal.
func spanOf(t *testing.T, text, path string) (forms.Span, []wire) {
	t.Helper()
	sp, err := forms.SourceSpan(text, editRoot(t), path, accessKeys)
	if err == nil {
		return sp, nil
	}
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		t.Fatalf("%s: outside the contract: %v", path, err)
	}
	var ws []wire
	for _, v := range fe.Violations {
		ws = append(ws, wireOf(v))
	}
	return forms.Span{}, ws
}

// The places a vector cannot reach (the trees of ADR 0005 have no nullable): a position the document does not spell, a node of the wrong kind,
// a wrapper in a flow collection, the path errors, and the scanner at its edges. The Rust peer is source_span_edge_cases.
func TestSourceSpanEdgeCases(t *testing.T) {
	refused := func(text, path string, code, phase, loc, lp string) {
		t.Helper()
		if _, ws := spanOf(t, text, path); !reflect.DeepEqual(ws, []wire{{code, phase, loc, lp}}) {
			t.Errorf("%q %s: got %v, want %s %s %s %s", text, path, ws, code, phase, loc, lp)
		}
	}
	span := func(text, path string, start, end int, style forms.SpanStyle) {
		t.Helper()
		if sp, ws := spanOf(t, text, path); ws != nil || sp != (forms.Span{Start: start, End: end, Style: style}) {
			t.Errorf("%q %s: got %v %v, want %d-%d %s", text, path, sp, ws, start, end, style)
		}
	}
	refused("name:\ncount: 3\n", "$.name", "EDIT_NO_SOURCE_VALUE", "edit", "1:1:0", "$.name")
	refused("count: 3\n", "$.ip.gateway", "EDIT_NO_SOURCE_VALUE", "edit", "1:1:0", "$.ip.gateway")
	refused("count: 3\n", "$.ip", "EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$.ip")
	refused("name: {a: 1}\n", "$.name", "EDIT_NOT_A_SCALAR", "edit", "1:7:6", "$.name")
	refused("name: [a]\n", "$.name", "EDIT_NOT_A_SCALAR", "edit", "1:7:6", "$.name")
	refused("name: a\n", "$", "EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$")
	refused("routes:\n  - to: a\n    metric: 1\n", `$.routes[{to="z"}].metric`, "EDIT_NO_SOURCE_VALUE", "edit", "2:3:10", `$.routes[{to="z"}].metric`)
	wrapper := "name: {value: alpha, access: inherit, modify: inherit, inherit: true, default_injection: ~, conformance: implemented}\n"
	refused(wrapper, "$.name", "EDIT_FLOW_CONTEXT", "edit", "1:15:14", "$.name")
	refused("name: a\n", "name", "PATH_SYNTAX", "path_parse", "not_applicable", "")
	refused("name: a\n", "$.nope", "UNKNOWN_FIELD", "path_resolve", "not_applicable", "$.nope")
	if _, err := forms.SourceSpan("name: [\n", editRoot(t), "$.name", accessKeys); err == nil || err.(*forms.Error).Kind != forms.ErrSyntax {
		t.Errorf("a syntax error stays a syntax error: %v", err)
	}
	span("name: \"a\\\\\"\n", "$.name", 6, 11, forms.SpanDoubleQuoted) // an escaped backslash does not end the scalar early
	span("name: \"a\" # c\n", "$.name", 6, 9, forms.SpanDoubleQuoted)
	span("name: 'a' # c\n", "$.name", 6, 9, forms.SpanSingleQuoted)
	span("name: a#b\n", "$.name", 6, 9, forms.SpanPlain) // a # that does not follow a space is part of the scalar
	span("name: alpha  \ncount: 3\n", "$.name", 6, 11, forms.SpanPlain)
	// a value that alone would be a document marker is a plain scalar like any other (#87; the fuzz input of #86)
	span("name: --- ](e-+.(-f\n", "$.name", 6, 19, forms.SpanPlain)
	span("name: ---\n", "$.name", 6, 9, forms.SpanPlain)
	span("name: ... x\n", "$.name", 6, 11, forms.SpanPlain)
}

// The offset of a path_parse error is present there and nowhere else; and a list below a composite is found by its identity.
func TestSourceSpanPathErrorsAndAListBelowAComposite(t *testing.T) {
	offset := func(path string) *int {
		_, err := forms.SourceSpan("name: a\n", editRoot(t), path, accessKeys)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
			t.Fatalf("%s: %v", path, err)
		}
		return fe.Violations[0].PathOffset
	}
	if o := offset("name"); o == nil || *o != 0 {
		t.Errorf("a path_parse error carries the offset in the path text: %v", o)
	}
	if o := offset("$.nope"); o != nil {
		t.Errorf("a path_resolve error carries none: %v", *o)
	}
	tree := obj{"box": obj{"composite": obj{"routes": obj{"list": obj{"key": []any{"to"},
		"fields": obj{"to": obj{"scalar": "string"}, "metric": obj{"scalar": "integer"}}}}}}}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: treeFields(t, tree, nil)}
	text := "box:\n  routes:\n    - to: a\n      metric: 1\n    - to: b\n      metric: 2\n"
	sp, err := forms.SourceSpan(text, root, `$.box.routes[{to="b"}].metric`, accessKeys)
	if err != nil || sp != (forms.Span{Start: 69, End: 70, Style: forms.SpanPlain}) || text[sp.Start:sp.End] != "2" {
		t.Errorf("a list below a composite: %v %v", sp, err)
	}
}

// An empty value (`ip:`) has no position of its own: a composite target there is refused at the enclosing structure. The Rust peer is
// source_span_an_empty_value_is_not_a_scalar_target.
func TestSourceSpanAnEmptyValueIsNotAScalarTarget(t *testing.T) {
	_, ws := spanOf(t, "ip:\ncount: 3\n", "$.ip")
	if want := []wire{{"EDIT_NOT_A_SCALAR", "edit", "1:1:0", "$.ip"}}; !reflect.DeepEqual(ws, want) {
		t.Errorf("got %v, want %v", ws, want)
	}
}

// typedValue is the new value of an edit case: {string: s}, {integer: n}, {boolean: b} or {null: true}.
func typedValue(t *testing.T, v obj) *forms.Node {
	t.Helper()
	switch {
	case v["string"] != nil:
		return &forms.Node{Kind: forms.KindStr, Str: v["string"].(string)}
	case v["integer"] != nil:
		switch n := v["integer"].(type) {
		case int:
			return &forms.Node{Kind: forms.KindInt, Int: int64(n)}
		case int64:
			return &forms.Node{Kind: forms.KindInt, Int: n}
		}
		t.Fatalf("integer %T", v["integer"])
	case v["boolean"] != nil:
		return &forms.Node{Kind: forms.KindBool, Bool: v["boolean"].(bool)}
	case v["null"] != nil:
		return &forms.Node{Kind: forms.KindNull}
	}
	t.Fatalf("a typed value: %v", v)
	return nil
}

// parsedInput is the document of a case that was given as a parsed value (a map of scalars), not as text.
func parsedInput(t *testing.T, c obj) *forms.Node {
	t.Helper()
	in := c["input"].(obj)
	if in["type"] != "map" {
		t.Fatalf("a parsed input: %v", in)
	}
	doc := &forms.Node{Kind: forms.KindMap}
	vals := in["value"].(obj)
	keys := make([]string, 0, len(vals))
	for k := range vals {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	for _, k := range keys {
		n := &forms.Node{}
		switch v := vals[k].(type) {
		case string:
			n.Kind, n.Str = forms.KindStr, v
		case int:
			n.Kind, n.Int = forms.KindInt, int64(v)
		case int64:
			n.Kind, n.Int = forms.KindInt, v
		case bool:
			n.Kind, n.Bool = forms.KindBool, v
		default:
			t.Fatalf("a parsed input value: %T", v)
		}
		doc.Entries = append(doc.Entries, forms.Entry{Key: k, Value: n})
	}
	return doc
}

func TestEditCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	for _, suite := range suites(t) {
		if suite["suite"] != "edit" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			if c["kind"] != "edit" {
				continue
			}
			ran++
			id := c["id"].(string)
			root := rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string))
			value := typedValue(t, c["value"].(obj))
			path := c["path"].(string)
			expect := c["expect"].(obj)
			var res forms.EditResult
			var err error
			var text string
			if _, parsed := c["input"]; parsed {
				err = forms.EditParsed(parsedInput(t, c), root, path, accessKeys, value)
			} else {
				text = sourceOf(t, c)
				res, err = forms.EditScalar(text, root, path, accessKeys, value)
			}
			if _, ok := expect["errors"]; ok {
				var fe *forms.Error
				if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
					t.Errorf("%s: an error of the contract was expected, got %v", id, err)
					continue
				}
				if res.Text != "" {
					t.Errorf("%s: no text with an error", id)
				}
				var ws []wire
				for _, v := range fe.Violations {
					ws = append(ws, wireOf(v))
				}
				if !reflect.DeepEqual(ws, expectedEditWires(t, expect)) {
					t.Errorf("%s: got %v, want %v", id, ws, expectedEditWires(t, expect))
				}
				continue
			}
			if err != nil {
				t.Errorf("%s: an edit was expected, got %v", id, err)
				continue
			}
			var want string
			if y, ok := expect["output_yaml"]; ok {
				want = y.(string)
			} else {
				b, derr := hex.DecodeString(expect["output_hex"].(string))
				if derr != nil {
					t.Fatalf("%s: output_hex: %v", id, derr)
				}
				want = string(b)
			}
			if res.Text != want { // compared as bytes
				t.Errorf("%s: got %q, want %q", id, res.Text, want)
				continue
			}
			before, after := expect["span_before"].(obj), expect["span_after"].(obj)
			if sp, serr := forms.SourceSpan(text, root, path, accessKeys); serr != nil || sp.Start != before["start"] || sp.End != before["end"] {
				t.Errorf("%s: the span before: got %v %v, want %v", id, sp, serr, before)
			}
			pathAfter := path
			if pa, ok := c["path_after"].(string); ok { // an edit of the key of a list element changes the address of the scalar
				pathAfter = pa
			}
			if sp, serr := forms.SourceSpan(res.Text, root, pathAfter, accessKeys); serr != nil || sp.Start != after["start"] || sp.End != after["end"] || sp != res.Span {
				t.Errorf("%s: the span after: got %v %v (result %v), want %v", id, sp, serr, res.Span, after)
			}
		}
	}
	if ran != 44 {
		t.Fatalf("the 44 edit cases of corpus/edit, ran %d", ran)
	}
}
