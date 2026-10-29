package forms_test

// Dumps the outcome of ReadDiff and ReadDiffVersion for the generated inputs of tools/parity/gen_diffread.py, for the Rust/Go differential of the reader of the
// canonical bytes of a diff. PARITY_DIFFREAD_IN is a JSON-lines file ({"id", "tag", "hex"}), PARITY_DIFFREAD_OUT receives one JSON line per input. Without the two
// variables this test does nothing. The Rust peer is rust/tests/parity_diffread_dump.rs; tools/parity/compare_diffread.py compares and checks the promises of the
// reader on its own.
// For each input r is ["ok", the diff written back, number of events, number of representation entries] or ["err", reason, offset], and v is ["ok", version] or
// ["err", reason, offset].

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func readErr(t *testing.T, err error) []any {
	t.Helper()
	var re *forms.DiffReadError
	if !errors.As(err, &re) {
		t.Fatalf("an error that is not a DiffReadError: %v", err)
	}
	return []any{"err", re.Reason, re.Offset}
}

func TestDumpDiffReadOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_DIFFREAD_IN"), os.Getenv("PARITY_DIFFREAD_OUT")
	if in == "" || out == "" {
		return
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
			ID  json.Number `json:"id"`
			Hex string      `json:"hex"`
		}
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		b, err := hex.DecodeString(c.Hex)
		if err != nil {
			t.Fatal(err)
		}
		var r, v []any
		if d, err := forms.ReadDiff(b); err != nil {
			r = readErr(t, err)
		} else {
			written := (&forms.DiffResult{Changes: d.Changes, Representation: d.Representation}).Canonical()
			r = []any{"ok", written, len(d.Changes), len(d.Representation)}
		}
		if n, err := forms.ReadDiffVersion(b); err != nil {
			v = readErr(t, err)
		} else {
			v = []any{"ok", n}
		}
		line, err := json.Marshal(map[string]any{"id": c.ID, "r": r, "v": v})
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
