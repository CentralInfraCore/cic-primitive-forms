package forms_test

// Edge cases of the text of logical paths that the corpus does not spell out (testdata/p1/pathtext.json): every escape,
// the ends of the text, the control characters. The Rust peer is rust/tests/pathtext.rs.

import (
	"encoding/json"
	"os"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func TestSharedPathTextEdges(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/pathtext.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases []struct {
			Path   string `json:"path"`
			Value  string `json:"value"`
			Error  string `json:"error"`
			Offset int    `json:"offset"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(b, &file); err != nil {
		t.Fatal(err)
	}
	for _, c := range file.Cases {
		segs, perr := forms.ParsePath(c.Path)
		if c.Error != "" {
			if perr == nil || perr.Code != c.Error || perr.Offset != c.Offset {
				t.Errorf("%q: got %v, want %s at %d", c.Path, perr, c.Error, c.Offset)
			}
			continue
		}
		if perr != nil {
			t.Errorf("%q: %+v", c.Path, perr)
			continue
		}
		if got := segs[1].Item[0].Str; got != c.Value {
			t.Errorf("%q: value %q, want %q", c.Path, got, c.Value)
		}
		if text, ferr := forms.FormatPath(segs); ferr != nil || text != c.Path {
			t.Errorf("%q: format(parse(t)) = %q (%v)", c.Path, text, ferr)
		}
	}
}
