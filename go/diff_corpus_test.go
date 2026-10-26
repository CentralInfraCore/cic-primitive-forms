package forms_test

// Runs the cases of kind diff and diff_stability of the corpus (corpus/diff, ADR 0008) against forms.Diff, the Go peer of
// rust/tests/diff.rs. The expectation of a case is the exact canonical bytes, or the one error with its operand.

import (
	"errors"
	"math"
	"reflect"
	"slices"
	"strconv"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// diffNode is nodeOf for the operands of a diff: it also takes a number the document model does not carry (a float, an integer beyond 64
// bits), which becomes the unsupported kind.
func diffNode(t *testing.T, v any) *forms.Node {
	t.Helper()
	switch x := v.(type) {
	case float64:
		return &forms.Node{Kind: forms.KindUnsupported, Str: strconv.FormatFloat(x, 'g', -1, 64)}
	case uint64:
		if x > math.MaxInt64 {
			return &forms.Node{Kind: forms.KindUnsupported, Str: strconv.FormatUint(x, 10)}
		}
		return &forms.Node{Kind: forms.KindInt, Int: int64(x)}
	case int64:
		return &forms.Node{Kind: forms.KindInt, Int: x}
	case []any:
		n := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for _, it := range x {
			n.Items = append(n.Items, diffNode(t, it))
		}
		return n
	case obj:
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		for _, k := range sortedKeys(x) {
			n.Entries = append(n.Entries, forms.Entry{Key: k, Value: diffNode(t, x[k])})
		}
		return n
	}
	return nodeOf(t, v)
}

// isDiffRun says whether a case is run by the diff runner: the two kinds of ADR 0008.
func isDiffRun(kind string) bool { return kind == "diff" || kind == "diff_stability" }

func sortedKeys(m obj) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// diffOperand is one operand of a case: the parsed value under key, or the text under key+"_yaml".
func diffOperand(t *testing.T, c obj, key string) *forms.Node {
	t.Helper()
	if src, ok := c[key+"_yaml"]; ok {
		n, err := forms.Parse(src.(string))
		if err != nil {
			t.Fatalf("%s: %v", c["id"], err)
		}
		return n
	}
	return diffNode(t, c[key])
}

func diffContextOf(c obj, key string) forms.DiffContext {
	s, ok := c[key].(obj)
	if !ok {
		s = c["schema"].(obj)
	}
	return forms.DiffContext{Schema: s["synthetic"].(obj)["tree"].(string)}
}

func scopeOf(c obj) []string {
	raw, ok := c["scope"].([]any)
	if !ok {
		return nil
	}
	out := make([]string, len(raw))
	for i, p := range raw {
		out[i] = p.(string)
	}
	return out
}

// nullableOpaque is the shape with every opaque position nullable: in the cases of ADR 0008 `null` is a value of an opaque position (Absent to
// null is an appearance), and the tree notation of the corpus does not spell nullability.
func nullableOpaque(s forms.Shape) forms.Shape {
	if s.Kind == forms.ShapeOpaque {
		s.Nullable = true
	}
	fields := make([]forms.NamedShape, len(s.Fields))
	for i, f := range s.Fields {
		fields[i] = forms.NamedShape{Name: f.Name, Shape: nullableOpaque(f.Shape)}
	}
	s.Fields = fields
	if s.Elements != nil {
		els := &forms.Elements{Choice: s.Elements.Choice}
		for _, c := range s.Elements.Cases {
			cf := make([]forms.NamedShape, len(c.Fields))
			for i, f := range c.Fields {
				cf[i] = forms.NamedShape{Name: f.Name, Shape: nullableOpaque(f.Shape)}
			}
			els.Cases = append(els.Cases, forms.Case{Name: c.Name, Fields: cf, Key: c.Key})
		}
		s.Elements = els
	}
	return s
}

// diffRun is what Diff says for one pair of operands: the canonical bytes, or the wire form of its one error and its operand.
func diffRun(t *testing.T, suite, c obj, a, b *forms.Node) (string, []wire, string) {
	t.Helper()
	root := nullableOpaque(rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string)))
	res, err := forms.Diff(
		forms.DiffView{Doc: a, Context: diffContextOf(c, "schema")},
		forms.DiffView{Doc: b, Context: diffContextOf(c, "schema_b")},
		root, accessKeys, boundGrammar(t), scopeOf(c))
	if err == nil {
		return res.Canonical(), nil, ""
	}
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		t.Fatalf("%s: outside the contract: %v", c["id"], err)
	}
	var ws []wire
	for _, v := range fe.Violations {
		ws = append(ws, wireOf(v))
	}
	return "", ws, fe.Violations[0].Operand
}

func expectedDiff(t *testing.T, expect obj) (string, []wire, string) {
	t.Helper()
	if s, ok := expect["canonical"]; ok {
		return s.(string), nil, ""
	}
	errs := expect["errors"].([]any)
	if len(errs) != 1 {
		t.Fatalf("one error per diff response, the case has %d", len(errs))
	}
	e := errs[0].(obj)
	operand, _ := e["operand"].(string)
	delete(e, "operand")
	return "", []wire{expectedWire(t, e)}, operand
}

func TestDiffCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	for _, suite := range suites(t) {
		if suite["suite"] != "diff" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id, kind := c["id"].(string), c["kind"].(string)
			ran++
			wantBytes, wantErr, wantOperand := expectedDiff(t, c["expect"].(obj))
			pairs := []obj{c}
			if kind == "diff_stability" {
				pairs = nil
				for _, v := range c["variants"].([]any) {
					pairs = append(pairs, v.(obj))
				}
			}
			for i, p := range pairs {
				if kind == "diff_stability" {
					p["id"], p["schema"] = id, c["schema"]
				}
				got, errs, operand := diffRun(t, suite, p, diffOperand(t, p, "a"), diffOperand(t, p, "b"))
				if got != wantBytes || !reflect.DeepEqual(errs, wantErr) || operand != wantOperand {
					t.Errorf("%s (variant %d): got %q %v %q, want %q %v %q", id, i, got, errs, operand, wantBytes, wantErr, wantOperand)
				}
			}
		}
	}
	if ran != 66 {
		t.Fatalf("the corpus has 62 diff and 4 diff_stability cases, ran %d", ran)
	}
}
