package forms_test

// Dumps the outcome of ReceiptFlatFixture for the generated cases of tools/parity/gen_materialize.py, for the Rust/Go differential of the flat receipt (P5-3).
// PARITY_RECEIPT_IN is the same JSON-lines file as the materialization differential ({"id", "tag", "schema", "doc", "caps", "keys", "want"}), PARITY_RECEIPT_OUT receives
// one JSON line per input. Without the two variables this test does nothing. The Rust peer is rust/tests/parity_receipt_dump.rs; tools/parity/compare_receipt.py
// compares the two and both against a reference computed in Python, and the engine harnesses take the same files: the engine's own receipt verifier is the third reader.
// r is ["ok", document, authored_input, binding, expected_keys, projection] or ["err", reason, field]. Only the release and the engine are fixture data (the file
// testdata/p5/receipt.json holds them): an authentic receipt is not what this produces.

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

func TestDumpReceiptOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_RECEIPT_IN"), os.Getenv("PARITY_RECEIPT_OUT")
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
	raw, err = os.ReadFile(filepath.Join(root, "testdata", "p5", "receipt.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		EngineVersion string `json:"engine_version"`
		Release       struct {
			PrimitiveRelease      string `json:"primitive_release"`
			PrimitiveSourceCommit string `json:"primitive_source_commit"`
			PrimitiveBuildHash    string `json:"primitive_build_hash"`
			GrammarSHA256         string `json:"grammar_sha256"`
			GrammarSchemaSHA256   string `json:"grammar_schema_sha256"`
		} `json:"release"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	release := forms.ReleaseFixture(fixture.Release)
	engine := forms.EngineIdentity{Identity: "cic-primitive-forms-go", Version: fixture.EngineVersion}
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
		doc, err := forms.Parse(c.Doc)
		if err != nil {
			t.Fatalf("#%s: the document does not parse: %v", c.ID, err)
		}
		in := forms.SchemaInput{Bytes: []byte(c.Schema), SHA256: forms.SHA256Hex([]byte(c.Schema)), Grammar: defaults.Grammar, Surface: "config_surface"}
		var r []any
		rec, err := forms.ReceiptFlatFixture(in, doc, defaults.AccessKeys, c.Caps, release, engine)
		var re *forms.ReceiptError
		var me *forms.MaterializeError
		switch {
		case err == nil:
			r = []any{"ok", rec.Document, rec.AuthoredInput, rec.Binding, rec.Materialized.ExpectedKeys, rec.Materialized.ProjectionJSON}
		case errors.As(err, &me):
			r = []any{"err", me.Reason, me.Field}
		case errors.As(err, &re) && (re.Reason == forms.ReceiptPathNotOneSegment):
			r = []any{"err", string(re.Reason), re.Field}
		default:
			t.Fatalf("#%s: an error outside the contract of the cases: %v", c.ID, err)
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
