package forms_test

// Dumps the outcome of MaterializeFlat for the generated cases of tools/parity/gen_materialize.py, for the Rust/Go differential of the flat materialization (P5-2).
// PARITY_MATERIALIZE_IN is a JSON-lines file ({"id", "tag", "schema", "doc", "caps", "keys", "want"}), PARITY_MATERIALIZE_OUT receives one JSON line per input. Without
// the two variables this test does nothing. The Rust peer is rust/tests/parity_materialize_dump.rs; tools/parity/compare_materialize.py compares the two and both
// against the reference, and the engine harnesses (tools/p5/engine-harness-*) take the same file as the third reader. r is ["ok", expected_keys, projection, digest]
// or ["err", reason, field].

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func TestDumpMaterializeOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_MATERIALIZE_IN"), os.Getenv("PARITY_MATERIALIZE_OUT")
	if in == "" || out == "" {
		return
	}
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	raw, err := os.ReadFile(filepath.Join(root, "testdata", "p1", "defaults.json"))
	if err != nil {
		t.Fatal(err)
	}
	var defaults struct {
		Grammar    string   `json:"grammar"`
		AccessKeys []string `json:"access_keys"`
	}
	if err := json.Unmarshal(raw, &defaults); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var buf bytes.Buffer
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<28)
	for sc.Scan() {
		var c struct {
			ID     json.Number       `json:"id"`
			Schema string            `json:"schema"`
			Doc    string            `json:"doc"`
			Caps   map[string]string `json:"caps"`
		}
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		compiled, err := forms.CompileSchema(forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: defaults.Grammar, Surface: "config_surface"})
		if err != nil {
			t.Fatalf("#%s: the schema does not compile: %v", c.ID, err)
		}
		doc, err := forms.Parse(c.Doc)
		if err != nil {
			t.Fatalf("#%s: the document does not parse: %v", c.ID, err)
		}
		var r []any
		m, err := forms.MaterializeFlat(doc, compiled.Root, defaults.AccessKeys, c.Caps)
		var me *forms.MaterializeError
		switch {
		case err == nil:
			r = []any{"ok", m.ExpectedKeys, m.ProjectionJSON, m.OutputDigest}
		case errors.As(err, &me):
			r = []any{"err", me.Reason, me.Field}
		default:
			t.Fatalf("an error that is not a MaterializeError: %v", err)
		}
		line, err := json.Marshal(map[string]any{"id": c.ID, "r": r})
		if err != nil {
			t.Fatal(err)
		}
		buf.Write(line)
		buf.WriteByte('\n')
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}
