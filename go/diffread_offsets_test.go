package forms_test

import (
	"encoding/hex"
	"os"
	"strconv"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// The reader of the canonical bytes stops at the first byte it refuses, and says which reason and at which offset; the offset is never beyond the end of the
// input. The inputs, reasons and offsets are in testdata/p1/diffread-offsets.tsv, which the Rust test (rust/tests/diffread_offsets.rs) reads too: the two
// readers were written apart and have to agree on every line.
func TestReadDiffStopsAtTheReasonAndOffsetOfTheSharedTable(t *testing.T) {
	text, err := os.ReadFile("../testdata/p1/diffread-offsets.tsv")
	if err != nil {
		t.Fatal(err)
	}
	checked := 0
	for _, line := range strings.Split(string(text), "\n") {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		f := strings.Split(line, "\t")
		if len(f) != 4 {
			t.Fatalf("a line of the table has %d fields: %q", len(f), line)
		}
		in, err := hex.DecodeString(f[0])
		if err != nil {
			t.Fatalf("%s: %v", f[3], err)
		}
		_, rerr := forms.ReadDiff(in)
		checked++
		if f[1] == "ok" {
			if rerr != nil {
				t.Errorf("%s: refused (%v), want it read", f[3], rerr)
			}
			continue
		}
		de, ok := rerr.(*forms.DiffReadError)
		if !ok {
			t.Errorf("%s: %v, want a reason", f[3], rerr)
			continue
		}
		if want, _ := strconv.Atoi(f[2]); de.Reason != f[1] || de.Offset != want {
			t.Errorf("%s: %s at %d, want %s at %s", f[3], de.Reason, de.Offset, f[1], f[2])
		}
	}
	if checked < 50 {
		t.Errorf("only %d lines were checked", checked)
	}
}
