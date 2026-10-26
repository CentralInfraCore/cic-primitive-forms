package forms_test

// Dumps the outcome of Patch for the generated inputs of tools/parity/gen_patch.py, for the Rust/Go/model differential of ADR 0013 (P4 step P4-3).
// PATCH_DUMP_IN is a JSON-lines file (the first line is the schema in the tree notation of the corpus), PARITY_PATCH_OUT receives one JSON line per input. Without the
// two variables this test does nothing. The Rust peer is rust/tests/parity_patch_dump.rs; tools/parity/compare_patch.py compares and checks the promises of
// ADR 0013 on its own.
// For each input: the diff of A to B (diff), the patch of A by it (patch: the text, the replaced scalars, their spans before and after, or the refusal), the patch by
// the empty diff (empty), the patch of the result by the same diff (replay), the diff of the result to B in the scope of the first (rt), and the patch by the
// diff with the first previous value altered (tamper). An answer is {"ok": ...}, {"err": [code, phase, location, path, operand]} or {"other": kind}.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func patchErr(err error) map[string]any {
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		return map[string]any{"other": strconv.Itoa(int(fe.Kind))}
	}
	w := wireOf(fe.Violations[0])
	return map[string]any{"err": []string{w.code, w.phase, w.loc, w.path, fe.Violations[0].Operand}}
}

func patchOK(text string) map[string]any { return map[string]any{"ok": text} }

func spanStyle(s forms.Span) []any { return []any{s.Start, s.End, string(s.Style)} }

func TestDumpPatchOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_PATCH_IN"), os.Getenv("PARITY_PATCH_OUT")
	if in == "" || out == "" {
		t.Skip("PARITY_PATCH_IN and PARITY_PATCH_OUT are not set")
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
	ctx := forms.DiffContext{}
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
		text := c["yaml"].(string)
		var scope []string
		if s, ok := c["scope"].([]any); ok {
			scope = []string{}
			for _, p := range s {
				scope = append(scope, p.(string))
			}
		}
		b := dumpDoc(c["b"])
		row := map[string]any{"id": c["id"]}
		doc, perr := forms.Parse(text)
		if perr != nil {
			row["diff"], row["patch"] = patchErr(perr), patchErr(perr)
		} else {
			row["diff"] = dumpDiff(doc, b, "", root, g, scope)
			res, derr := forms.Diff(forms.DiffView{Doc: doc}, forms.DiffView{Doc: b}, root, accessKeys, g, scope)
			if derr != nil {
				// no events to apply: patch a fixed one, which an invalid A must refuse by its own earlier phase
				one := forms.PatchDiff{Version: 1, Changes: []forms.DiffEvent{{Kind: forms.EventValueChange, Path: "$.name", Previous: &forms.Node{Kind: forms.KindStr, Str: "x"}, Next: &forms.Node{Kind: forms.KindStr, Str: "y"}}}}
				if _, e := forms.Patch(text, root, accessKeys, g, ctx, ctx, one); e != nil {
					row["patch"] = patchErr(e)
				} else {
					row["patch"] = map[string]any{"skipped": true}
				}
			} else {
				pd := forms.PatchDiff{Version: 1, Changes: res.Changes}
				if len(res.Changes) > 0 { // the first event with another kind and the same two values: the patch must still refuse the whole diff
					swapped := append([]forms.DiffEvent{}, res.Changes...)
					swapped[0].Kind = forms.EventPolicyDeclarationChange
					if _, e := forms.Patch(text, root, accessKeys, g, ctx, ctx, forms.PatchDiff{Version: 1, Changes: swapped}); e != nil {
						row["kindswap"] = patchErr(e)
					} else {
						row["kindswap"] = patchOK("")
					}
				}
				p, perr := forms.Patch(text, root, accessKeys, g, ctx, ctx, pd)
				if perr != nil {
					row["patch"] = patchErr(perr)
				} else {
					spans := []any{}
					for _, s := range p.Spans {
						spans = append(spans, []any{s.Path, spanStyle(s.Before), spanStyle(s.After)})
					}
					row["patch"] = map[string]any{"ok": map[string]any{"text": p.Text, "replaced": p.Replaced, "spans": spans}}
					if r2, e2 := forms.Patch(p.Text, root, accessKeys, g, ctx, ctx, pd); e2 != nil {
						row["replay"] = patchErr(e2)
					} else {
						row["replay"] = patchOK(r2.Text)
					}
					if doc2, e3 := forms.Parse(p.Text); e3 == nil {
						row["rt"] = dumpDiff(doc2, b, "", root, g, scope)
					}
					if len(res.Changes) > 0 {
						tampered := append([]forms.DiffEvent{}, res.Changes...)
						e0 := tampered[0]
						e0.Previous = &forms.Node{Kind: forms.KindStr, Str: "tampered"}
						tampered[0] = e0
						if r3, e4 := forms.Patch(text, root, accessKeys, g, ctx, ctx, forms.PatchDiff{Version: 1, Changes: tampered}); e4 != nil {
							row["tamper"] = patchErr(e4)
						} else {
							row["tamper"] = patchOK(r3.Text)
						}
					}
				}
				if r4, e5 := forms.Patch(text, root, accessKeys, g, ctx, ctx, forms.PatchDiff{Version: 1}); e5 != nil {
					row["empty"] = patchErr(e5)
				} else {
					row["empty"] = patchOK(r4.Text)
				}
			}
		}
		line, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		w.Write(line)
		w.WriteByte('\n')
	}
}
