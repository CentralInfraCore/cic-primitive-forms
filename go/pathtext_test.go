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
		Format []struct {
			Segments []struct {
				Field string `json:"field"`
				Item  *[]struct {
					Name string  `json:"name"`
					Str  *string `json:"str"`
					Int  *int64  `json:"int"`
					Bad  string  `json:"bad"`
				} `json:"item"`
			} `json:"segments"`
			Error string `json:"error"`
			Text  string `json:"text"`
		} `json:"format"`
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
	for i, c := range file.Format {
		var segs []forms.PathSeg
		for _, sg := range c.Segments {
			if sg.Item == nil {
				segs = append(segs, forms.PathSeg{Field: sg.Field})
				continue
			}
			item := []forms.KeyComp{}
			for _, k := range *sg.Item {
				comp := forms.KeyComp{Name: k.Name, Bad: k.Bad}
				if k.Str != nil {
					comp.Str = *k.Str
				}
				if k.Int != nil {
					comp.IsInt, comp.Int = true, *k.Int
				}
				item = append(item, comp)
			}
			segs = append(segs, forms.PathSeg{Item: item, IsItem: true})
		}
		text, perr := forms.FormatPath(segs)
		switch {
		case c.Error != "" && (perr == nil || perr.Code != c.Error || perr.Phase != forms.PhasePathFormat):
			t.Errorf("format case %d: got %q %+v, want %s", i, text, perr, c.Error)
		case c.Error == "" && (perr != nil || text != c.Text):
			t.Errorf("format case %d: got %q %+v, want %q", i, text, perr, c.Text)
		}
	}
}
