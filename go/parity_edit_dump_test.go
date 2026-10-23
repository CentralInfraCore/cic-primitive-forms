package forms_test

// Dumps the outcome of EditScalar for the generated inputs of tools/parity/gen_edit.py, for the Rust/Go differential of the six guarantees of
// ADR 0012 (P3b step E4). PARITY_EDIT_IN is a JSON-lines file, PARITY_EDIT_OUT receives one JSON line per input. Without the two variables this
// test does nothing. The Rust peer is rust/tests/parity_edit_dump.rs; tools/parity/compare_edit.py compares and checks the guarantees.
// For each input: the span of the scalar before the edit (before), the edit, the edit to the value the scalar already holds (noop), the edit back to it (back), and the two orders of the edit and
// the edit of an independent position (ab, ba).

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func dumpScalar(t forms.ScalarType) forms.Shape {
	return forms.Shape{Kind: forms.ShapeScalar, Scalar: t, Presence: forms.Optional}
}

func dumpRoot() forms.Shape {
	str, integer := dumpScalar(forms.ScalarString), dumpScalar(forms.ScalarInteger)
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{
		{Name: "name", Shape: str},
		{Name: "count", Shape: integer},
		{Name: "enabled", Shape: dumpScalar(forms.ScalarBoolean)},
		{Name: "ip", Shape: forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: []forms.NamedShape{{Name: "gateway", Shape: str}, {Name: "prefix", Shape: integer}}}},
		{Name: "routes", Shape: forms.Shape{Kind: forms.ShapeDeclaredList, Presence: forms.Optional, Elements: &forms.Elements{Cases: []forms.Case{{
			Name: "route", Key: []string{"to"}, Fields: []forms.NamedShape{{Name: "to", Shape: str}, {Name: "metric", Shape: integer}}}}}}},
		{Name: "tags", Shape: forms.Shape{Kind: forms.ShapeSet, Scalar: forms.ScalarString, Presence: forms.Optional}},
	}}
}

func dumpValue(v map[string]any) *forms.Node {
	switch {
	case v["string"] != nil:
		return &forms.Node{Kind: forms.KindStr, Str: v["string"].(string)}
	case v["integer"] != nil:
		n, _ := v["integer"].(json.Number).Int64()
		return &forms.Node{Kind: forms.KindInt, Int: n}
	case v["boolean"] != nil:
		return &forms.Node{Kind: forms.KindBool, Bool: v["boolean"].(bool)}
	case v["map"] != nil:
		return &forms.Node{Kind: forms.KindMap}
	case v["seq"] != nil:
		return &forms.Node{Kind: forms.KindSeq}
	}
	return &forms.Node{Kind: forms.KindNull}
}

// dumpTree is a parsed tree as JSON: the kind and the value of a scalar, a mapping with its entries in source order, a sequence. Positions are not part of it.
func dumpTree(n *forms.Node) any {
	switch n.Kind {
	case forms.KindBool:
		return map[string]any{"boolean": n.Bool}
	case forms.KindInt:
		return map[string]any{"integer": n.Int}
	case forms.KindStr:
		return map[string]any{"string": n.Str}
	case forms.KindSeq:
		items := []any{}
		for _, it := range n.Items {
			items = append(items, dumpTree(it))
		}
		return map[string]any{"seq": items}
	case forms.KindMap:
		entries := []any{}
		for _, e := range n.Entries {
			entries = append(entries, []any{e.Key, dumpTree(e.Value)})
		}
		return map[string]any{"map": entries}
	}
	return map[string]any{"null": true}
}

func dumpAnswer(r forms.EditResult, err error) map[string]any {
	if err == nil {
		return map[string]any{"ok": true, "text": r.Text, "span": []any{r.Span.Start, r.Span.End, string(r.Span.Style)}, "changed": r.Changed}
	}
	var fe *forms.Error
	if errors.As(err, &fe) && fe.Kind == forms.ErrContract {
		errs := []any{}
		for _, v := range fe.Violations {
			w := wireOf(v)
			errs = append(errs, []any{w.code, w.phase, w.loc, w.path})
		}
		return map[string]any{"ok": false, "err": errs}
	}
	kind := map[forms.ErrorKind]string{forms.ErrUnsupported: "unsupported", forms.ErrSyntax: "syntax", forms.ErrLimit: "limit"}[fe.Kind]
	if fe == nil || kind == "" {
		kind = "other"
	}
	return map[string]any{"ok": false, "other": kind}
}

func dumpEdit(text, path string, v *forms.Node) map[string]any {
	r, err := forms.EditScalar(text, dumpRoot(), path, accessKeys, v)
	return dumpAnswer(r, err)
}

func dumpOrder(text, p1 string, v1 *forms.Node, p2 string, v2 *forms.Node) map[string]any {
	r1, err := forms.EditScalar(text, dumpRoot(), p1, accessKeys, v1)
	if err != nil {
		return dumpAnswer(r1, err)
	}
	r2, err := forms.EditScalar(r1.Text, dumpRoot(), p2, accessKeys, v2)
	return dumpAnswer(r2, err)
}

func TestDumpEditOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_EDIT_IN"), os.Getenv("PARITY_EDIT_OUT")
	if in == "" || out == "" {
		t.Skip("PARITY_EDIT_IN and PARITY_EDIT_OUT are not set")
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w, err := os.Create(out)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		if len(sc.Bytes()) == 0 {
			continue
		}
		var c map[string]any
		dec := json.NewDecoder(bytes.NewReader(sc.Bytes()))
		dec.UseNumber()
		if err := dec.Decode(&c); err != nil {
			t.Fatal(err)
		}
		text, path := c["yaml"].(string), c["path"].(string)
		v := dumpValue(c["value"].(map[string]any))
		edit, eerr := forms.EditScalar(text, dumpRoot(), path, accessKeys, v)
		res := map[string]any{"id": c["id"], "before": nil, "edit": dumpAnswer(edit, eerr), "tree_before": nil, "tree_after": nil, "noop": nil, "back": nil, "ab": nil, "ba": nil}
		if eerr == nil {
			before, berr := forms.Parse(text)
			after, aerr := forms.Parse(edit.Text)
			if berr == nil && aerr == nil {
				res["tree_before"], res["tree_after"] = dumpTree(before), dumpTree(after)
			}
		}
		if sp, err := forms.SourceSpan(text, dumpRoot(), path, accessKeys); err == nil {
			res["before"] = []any{sp.Start, sp.End, string(sp.Style)}
		}
		if o, ok := c["orig"].(map[string]any); ok {
			ov := dumpValue(o)
			res["noop"] = dumpEdit(text, path, ov)
			if bp, present := c["back_path"]; eerr == nil && !(present && bp == nil) { // a nil back_path: the new key needs the escapes of the path text, not edited back
				backPath := path
				if s, ok := bp.(string); ok { // the key of a list element: its address changes with it
					backPath = s
				}
				res["back"] = dumpEdit(edit.Text, backPath, ov)
			}
		}
		if o, ok := c["other"].(map[string]any); ok {
			p2, v2 := o["path"].(string), dumpValue(o["value"].(map[string]any))
			res["ab"] = dumpOrder(text, path, v, p2, v2)
			res["ba"] = dumpOrder(text, p2, v2, path, v)
		}
		line, err := json.Marshal(res)
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprintf(w, "%s\n", line)
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
}
