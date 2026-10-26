package forms_test

// Dumps the outcome of Diff for the generated inputs of tools/parity/gen_diff.py, for the Rust/Go/model differential of ADR 0008 (P4 step D3b).
// PARITY_DIFF_IN is a JSON-lines file (the first line is the schema in the tree notation of the corpus), PARITY_DIFF_OUT receives one JSON line per
// input. Without the two variables this test does nothing. The Rust peer is rust/tests/parity_diff_dump.rs; tools/parity/compare_diff.py compares.
// For each input four diffs: A to B (ab), B to A (ba), A to A (aa) and the same pair with every list and set in another order (abs), all in the
// scope of the input; ctx_b makes the context of B a different one. An answer is {"ok": canonical bytes}, {"err": [code, phase, location, path,
// operand]} or {"other": kind} for an error outside the contract.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"slices"
	"strconv"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func dumpScalarType(name string) forms.ScalarType {
	switch name {
	case "integer":
		return forms.ScalarInteger
	case "boolean":
		return forms.ScalarBoolean
	}
	return forms.ScalarString
}

// dumpShape reads the tree notation of the corpus. Only key fields are mandatory; opaque positions are nullable, as in the runners of corpus/diff.
func dumpShape(spec any, mandatory bool) forms.Shape {
	pres := forms.Optional
	if mandatory {
		pres = forms.Mandatory
	}
	if s, ok := spec.(string); ok && s == "opaque" {
		return forms.Shape{Kind: forms.ShapeOpaque, Presence: pres, Nullable: true}
	}
	m := spec.(map[string]any)
	switch {
	case m["scalar"] != nil:
		return forms.Shape{Kind: forms.ShapeScalar, Scalar: dumpScalarType(m["scalar"].(string)), Presence: pres}
	case m["set"] != nil:
		return forms.Shape{Kind: forms.ShapeSet, Scalar: dumpScalarType(m["set"].(string)), Presence: pres}
	case m["composite"] != nil:
		return forms.Shape{Kind: forms.ShapeComposite, Fields: dumpFields(m["composite"].(map[string]any), nil), Presence: pres}
	}
	list := m["list"].(map[string]any)
	keyOf := func(raw any) []string {
		var key []string
		for _, k := range raw.([]any) {
			key = append(key, k.(string))
		}
		return key
	}
	els := &forms.Elements{}
	if choice, ok := list["choice"]; ok {
		els.Choice = true
		for _, raw := range choice.([]any) {
			cs := raw.(map[string]any)
			key := keyOf(cs["key"])
			els.Cases = append(els.Cases, forms.Case{Name: cs["name"].(string), Fields: dumpFields(cs["fields"].(map[string]any), key), Key: key})
		}
	} else {
		key := keyOf(list["key"])
		els.Cases = []forms.Case{{Fields: dumpFields(list["fields"].(map[string]any), key), Key: key}}
	}
	return forms.Shape{Kind: forms.ShapeDeclaredList, Elements: els, Presence: pres}
}

func dumpFields(fields map[string]any, key []string) []forms.NamedShape {
	names := make([]string, 0, len(fields))
	for k := range fields {
		names = append(names, k)
	}
	slices.Sort(names)
	var out []forms.NamedShape
	for _, name := range names {
		out = append(out, forms.NamedShape{Name: name, Shape: dumpShape(fields[name], slices.Contains(key, name))})
	}
	return out
}

// dumpDoc turns a plain JSON tree into a position-less Node; {"__u": text} is an unsupported number.
func dumpDoc(v any) *forms.Node {
	switch x := v.(type) {
	case nil:
		return &forms.Node{Kind: forms.KindNull}
	case bool:
		return &forms.Node{Kind: forms.KindBool, Bool: x}
	case json.Number:
		n, err := x.Int64()
		if err != nil {
			return &forms.Node{Kind: forms.KindUnsupported, Str: x.String()}
		}
		return &forms.Node{Kind: forms.KindInt, Int: n}
	case string:
		return &forms.Node{Kind: forms.KindStr, Str: x}
	case []any:
		n := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for _, it := range x {
			n.Items = append(n.Items, dumpDoc(it))
		}
		return n
	case map[string]any:
		if u, ok := x["__u"]; ok && len(x) == 1 {
			return &forms.Node{Kind: forms.KindUnsupported, Str: u.(string)}
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		slices.Sort(keys)
		n := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{}}
		for _, k := range keys {
			n.Entries = append(n.Entries, forms.Entry{Key: k, Value: dumpDoc(x[k])})
		}
		return n
	}
	panic(fmt.Sprintf("a JSON value: %T", v))
}

func dumpDiff(a, b *forms.Node, ctxB string, root forms.Shape, g forms.Grammar, scope []string) map[string]any {
	res, err := forms.Diff(forms.DiffView{Doc: a}, forms.DiffView{Doc: b, Context: forms.DiffContext{Schema: ctxB}}, root, accessKeys, g, scope)
	if err == nil {
		return map[string]any{"ok": res.Canonical()}
	}
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		return map[string]any{"other": strconv.Itoa(int(fe.Kind))}
	}
	w := wireOf(fe.Violations[0])
	return map[string]any{"err": []string{w.code, w.phase, w.loc, w.path, fe.Violations[0].Operand}}
}

func TestDumpDiffOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_DIFF_IN"), os.Getenv("PARITY_DIFF_OUT")
	if in == "" || out == "" {
		t.Skip("PARITY_DIFF_IN and PARITY_DIFF_OUT are not set")
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
	w := bufio.NewWriter(o)
	defer w.Flush()
	g := boundGrammar(t)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	var root forms.Shape
	for n := 0; sc.Scan(); n++ {
		dec := json.NewDecoder(bytes.NewReader(sc.Bytes()))
		dec.UseNumber()
		var c map[string]any
		if err := dec.Decode(&c); err != nil {
			t.Fatal(err)
		}
		if n == 0 {
			root = forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: dumpFields(c["tree"].(map[string]any), nil)}
			continue
		}
		var scope []string
		if s, ok := c["scope"].([]any); ok {
			scope = []string{}
			for _, p := range s {
				scope = append(scope, p.(string))
			}
		}
		ctxB := ""
		if c["ctx_b"].(json.Number).String() == "1" {
			ctxB = "other"
		}
		a, b, a2, b2 := dumpDoc(c["a"]), dumpDoc(c["b"]), dumpDoc(c["a2"]), dumpDoc(c["b2"])
		row := map[string]any{
			"id":  c["id"],
			"ab":  dumpDiff(a, b, ctxB, root, g, scope),
			"ba":  dumpDiff(b, a, ctxB, root, g, scope),
			"aa":  dumpDiff(a, a, "", root, g, scope),
			"abs": dumpDiff(a2, b2, ctxB, root, g, scope),
		}
		line, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		w.Write(line)
		w.WriteByte('\n')
	}
}
