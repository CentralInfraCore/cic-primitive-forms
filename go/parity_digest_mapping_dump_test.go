package forms

// Dumps the outcome of ReceiptDigestOf / MapRelease for the input set of the digest mapping differential (docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md, section 3).
// PARITY_DIGEST_MAPPING_IN is a JSON-lines file of cases ({"id", "kind": "convert" | "map", "input" | "record", "pin", ...}); PARITY_DIGEST_MAPPING_OUT receives exactly
// one JSON line per input line, in order: {"id", "ok": true, "value"} or {"id", "ok": false, "reason", "field"}. Without the two variables this test does nothing.
// The Rust peer is rust/tests/parity_digest_mapping_dump.rs; tools/parity/compare_digest_mapping.py compares the three readers.

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

func TestDumpDigestMappingOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_DIGEST_MAPPING_IN"), os.Getenv("PARITY_DIGEST_MAPPING_OUT")
	if in == "" || out == "" {
		return
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var b strings.Builder
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var c mappingCase
		if err := json.Unmarshal([]byte(sc.Text()), &c); err != nil {
			t.Fatalf("input line is not a case: %v", err)
		}
		line := map[string]any{"id": c.ID}
		var value any
		var rerr error
		switch c.Kind {
		case "convert":
			var s string
			s, rerr = ReceiptDigestOf(mappingNodeOf(t, c.Input), "")
			value = s
		case "map":
			var m *DigestMapping
			m, rerr = MapRelease(mappingNodeOf(t, c.Record), c.Pin)
			if m != nil {
				value = map[string]string{"primitive_build_hash": m.PrimitiveBuildHash, "grammar_sha256": m.GrammarSHA256, "grammar_schema_sha256": m.GrammarSchemaSHA256}
			}
		default:
			t.Fatalf("unknown kind %q", c.Kind)
		}
		var me *MappingError
		switch {
		case rerr == nil:
			line["ok"], line["value"] = true, value
		case errors.As(rerr, &me):
			line["ok"], line["reason"] = false, me.Reason
			if me.Field == "" {
				line["field"] = nil
			} else {
				line["field"] = me.Field
			}
		default:
			line["ok"], line["reason"], line["detail"] = false, "CRASH", rerr.Error()
		}
		enc, err := json.Marshal(line)
		if err != nil {
			t.Fatal(err)
		}
		b.Write(enc)
		b.WriteByte('\n')
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}
