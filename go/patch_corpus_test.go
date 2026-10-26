package forms_test

// Runs the cases of kind patch of the corpus (corpus/patch, ADR 0013) against forms.Patch, the Go peer of rust/tests/corpus.rs. A success is compared as bytes with
// the list of replaced scalars and the spans before and after; a failure is the one error, with no text.

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// patchEvents turns the structured `changes` of the diff of a case into events; the typed values {type, value} are their plain values.
func patchEvents(t *testing.T, diff obj) []forms.DiffEvent {
	t.Helper()
	var out []forms.DiffEvent
	plain := func(v any) *forms.Node {
		if m, ok := v.(obj); ok {
			return diffNode(t, m["value"])
		}
		return nil
	}
	for _, raw := range diff["changes"].([]any) {
		c := raw.(obj)
		out = append(out, forms.DiffEvent{Kind: c["kind"].(string), Path: c["path"].(string), Previous: plain(c["previous"]), Next: plain(c["next"])})
	}
	return out
}

// patchVersion is the version the canonical bytes of the diff say.
func patchVersion(t *testing.T, diff obj) int {
	t.Helper()
	var d struct {
		Version int `json:"version"`
	}
	if err := json.Unmarshal([]byte(diff["canonical"].(string)), &d); err != nil {
		t.Fatalf("the canonical bytes of a diff: %v", err)
	}
	return d.Version
}

func patchContext(c obj, key string) forms.DiffContext {
	s, ok := c[key].(obj)
	if !ok {
		s = c["schema"].(obj)
	}
	return forms.DiffContext{Schema: s["synthetic"].(obj)["tree"].(string)}
}

func isPatchRun(kind string) bool { return kind == "patch" }

func TestPatchCasesMatchTheCorpus(t *testing.T) {
	ran := 0
	g := boundGrammar(t)
	for _, suite := range suites(t) {
		if suite["suite"] != "patch" {
			continue
		}
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			id := c["id"].(string)
			ran++
			root := rootOf(t, suite, c["schema"].(obj)["synthetic"].(obj)["tree"].(string))
			diff := c["diff"].(obj)
			d := forms.PatchDiff{Version: patchVersion(t, diff)}
			if _, ok := diff["changes"]; ok {
				d.Changes = patchEvents(t, diff)
			}
			res, err := forms.Patch(sourceOf(t, c), root, accessKeys, g, patchContext(c, "schema"), patchContext(c, "context"), d)
			expect := c["expect"].(obj)
			if _, ok := expect["errors"]; ok {
				var fe *forms.Error
				if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
					t.Errorf("%s: an error of the contract was expected, got %v", id, err)
					continue
				}
				if res != nil {
					t.Errorf("%s: no result with an error", id)
				}
				var ws []wire
				for _, v := range fe.Violations {
					ws = append(ws, wireOf(v))
				}
				if want := expectedEditWires(t, expect); !reflect.DeepEqual(ws, want) {
					t.Errorf("%s: got %v, want %v", id, ws, want)
				}
				continue
			}
			if err != nil {
				t.Errorf("%s: a patch was expected, got %v", id, err)
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
			if res.Text != want {
				t.Errorf("%s: got %q, want %q", id, res.Text, want)
				continue
			}
			var replaced []string
			for _, p := range expect["replaced"].([]any) {
				replaced = append(replaced, p.(string))
			}
			if !reflect.DeepEqual(append([]string{}, res.Replaced...), append([]string{}, replaced...)) && !(len(res.Replaced) == 0 && len(replaced) == 0) {
				t.Errorf("%s: replaced %v, want %v", id, res.Replaced, replaced)
			}
			spans := expect["spans"].([]any)
			if len(res.Spans) != len(spans) {
				t.Errorf("%s: %d spans, want %d", id, len(res.Spans), len(spans))
				continue
			}
			for i, raw := range spans {
				s := raw.(obj)
				before, after := s["before"].(obj), s["after"].(obj)
				got := res.Spans[i]
				if got.Path != s["path"] || got.Before.Start != before["start"] || got.Before.End != before["end"] || got.After.Start != after["start"] || got.After.End != after["end"] {
					t.Errorf("%s: span %d: got %+v, want %v", id, i, got, s)
				}
			}
		}
	}
	if ran != 60 {
		t.Fatalf("the 60 patch cases of corpus/patch, ran %d", ran)
	}
}
