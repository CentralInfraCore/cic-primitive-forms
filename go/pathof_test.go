package forms_test

// PathOf on addresses the corpus does not spell out (testdata/p1/pathof.json): the ends of an address, an element that is not a
// record, a list field written expanded, an index past the end. The Rust peer is rust/tests/pathof.rs.

import (
	"encoding/json"
	"errors"
	"os"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func TestSharedPathOfCases(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/pathof.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases []struct {
			ID          string `json:"id"`
			YAML        string `json:"yaml"`
			At          []any  `json:"at"`
			Path        string `json:"path"`
			Unsupported bool   `json:"unsupported"`
			Error       string `json:"error"`
			ErrorPath   string `json:"error_path"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(b, &file); err != nil {
		t.Fatal(err)
	}
	shape := smallConfig(t)
	for _, c := range file.Cases {
		var at []forms.Step
		for _, s := range c.At {
			if n, ok := s.(float64); ok {
				at = append(at, forms.Step{Index: int(n), IsIndex: true})
			} else {
				at = append(at, forms.Step{Field: s.(string)})
			}
		}
		got, err := forms.PathOf(parseCase(t, c.ID, c.YAML), shape, accessKeys, at)
		var fe *forms.Error
		switch {
		case c.Unsupported:
			if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
				t.Errorf("%s: want unsupported, got %q %v", c.ID, got, err)
			}
		case c.Error != "":
			if !errors.As(err, &fe) || fe.Kind != forms.ErrContract || fe.Violations[0].Code != c.Error ||
				fe.Violations[0].Phase != forms.PhasePathOf || fe.Violations[0].LogicalPath != c.ErrorPath {
				t.Errorf("%s: want %s at %q, got %v", c.ID, c.Error, c.ErrorPath, err)
			}
		case err != nil || got != c.Path:
			t.Errorf("%s: got %q %v, want %q", c.ID, got, err, c.Path)
		}
	}
}
