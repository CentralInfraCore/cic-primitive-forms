package forms_test

// Dumps the outcome of Parse for a list of inputs, for the Rust/Go differential (tools/parity).
//
// PARITY_IN is a file with one input per line, hex-encoded; PARITY_OUT receives one JSON line per input.
// Without the two variables this test does nothing. The Rust peer is rust/tests/parity_dump.rs; the two
// outputs are compared by tools/parity/compare.py.

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"unicode/utf8"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func parityPos(p *forms.Pos) any {
	if p == nil {
		return nil
	}
	return []int{p.Line, p.Column, p.Offset}
}

func parityTree(n *forms.Node) any {
	var v map[string]any
	switch n.Kind {
	case forms.KindNull:
		v = map[string]any{"t": "null"}
	case forms.KindBool:
		v = map[string]any{"t": "bool", "v": n.Bool}
	case forms.KindInt:
		v = map[string]any{"t": "int", "v": n.Int}
	case forms.KindStr:
		v = map[string]any{"t": "str", "v": n.Str}
	case forms.KindSeq:
		items := []any{}
		for _, it := range n.Items {
			items = append(items, parityTree(it))
		}
		v = map[string]any{"t": "seq", "v": items}
	default:
		entries := []any{}
		for _, e := range n.Entries {
			entries = append(entries, []any{e.Key, parityPos(e.KeyPos), parityTree(e.Value)})
		}
		v = map[string]any{"t": "map", "v": entries}
	}
	return map[string]any{"n": v, "p": parityPos(n.Pos)}
}

func parityOutcome(b []byte) any {
	if !utf8.Valid(b) {
		return map[string]any{"err": "INVALID_UTF8"}
	}
	n, err := forms.Parse(string(b))
	if err == nil {
		return map[string]any{"ok": parityTree(n)}
	}
	var fe *forms.Error
	if !errors.As(err, &fe) {
		return map[string]any{"err": "NOT_AN_ERROR_TYPE"}
	}
	if fe.Kind != forms.ErrContract {
		return map[string]any{"err": fe.Code()}
	}
	viol := []any{}
	for _, v := range fe.Violations {
		var loc any
		if v.Location.Available {
			loc = []int{v.Location.Pos.Line, v.Location.Pos.Column, v.Location.Pos.Offset}
		}
		viol = append(viol, map[string]any{"code": v.Code, "phase": string(v.Phase), "loc": loc})
	}
	return map[string]any{"err": "CONTRACT", "viol": viol}
}

func TestDumpParseOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_IN"), os.Getenv("PARITY_OUT")
	if in == "" || out == "" {
		return
	}
	f, err := os.Open(in)
	must(t, err)
	defer f.Close()
	var sb strings.Builder
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		b, err := hex.DecodeString(strings.TrimSpace(sc.Text()))
		must(t, err)
		j, err := json.Marshal(parityOutcome(b))
		must(t, err)
		sb.Write(j)
		sb.WriteByte('\n')
	}
	must(t, sc.Err())
	must(t, os.WriteFile(out, []byte(sb.String()), 0o644))
}

// The shared regression corpus (testdata/p1/parity.json, built by tools/parity/make_corpus.py):
// the outcome of Parse for each input, pinned. The Rust peer reads the same file and pins the same
// outcomes, so a change on one side shows up as a failing pin on that side.
type parityFile struct {
	Cases []struct {
		Input  string `json:"input"`
		Expect any    `json:"expect"`
		Go     any    `json:"go"`
	} `json:"cases"`
	Known []struct {
		Input string `json:"input"`
		Rust  any    `json:"rust"`
		Go    any    `json:"go"`
	} `json:"known_divergences"`
}

func loadParity(t *testing.T) parityFile {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "testdata", "p1", "parity.json"))
	must(t, err)
	var f parityFile
	must(t, json.Unmarshal(b, &f))
	return f
}

// normal puts an outcome through JSON, so that it compares with what the file holds.
func normal(t *testing.T, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	must(t, err)
	var out any
	must(t, json.Unmarshal(b, &out))
	return out
}

func TestTheSharedRegressionCorpusGivesThePinnedOutcomes(t *testing.T) {
	f := loadParity(t)
	if len(f.Cases) <= 300 {
		t.Fatalf("the corpus is not empty: %d", len(f.Cases))
	}
	for _, c := range f.Cases {
		want := c.Expect
		if want == nil {
			want = c.Go
		}
		if got := normal(t, parityOutcome([]byte(c.Input))); !reflect.DeepEqual(got, want) {
			t.Errorf("input %q: got %v, want %v", c.Input, got, want)
		}
	}
}

// An open difference is pinned with both outcomes and must stay a difference: when a fix makes
// the two agree the pin fails and the case moves to cases.
func TestTheKnownDivergencesAreStillDivergences(t *testing.T) {
	f := loadParity(t)
	if len(f.Known) == 0 {
		t.Fatal("no known divergences")
	}
	refusal := func(v any) bool {
		e, ok := v.(map[string]any)["err"]
		return ok && e != "CONTRACT"
	}
	for _, k := range f.Known {
		if got := normal(t, parityOutcome([]byte(k.Input))); !reflect.DeepEqual(got, k.Go) {
			t.Errorf("input %q: got %v, want %v", k.Input, got, k.Go)
		}
		if reflect.DeepEqual(k.Rust, k.Go) || (refusal(k.Rust) && refusal(k.Go)) {
			t.Errorf("input %q is no longer a divergence", k.Input)
		}
	}
}
