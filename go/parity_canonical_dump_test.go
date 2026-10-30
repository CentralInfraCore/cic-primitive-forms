package forms_test

// Dumps the outcome of CanonicalBytes for the generated values of tools/parity/gen_canonical.py, for the Rust/Go differential of the canonical bytes (P5-1).
// PARITY_CANONICAL_IN is a JSON-lines file ({"id", "tag", "spec", "want"}), PARITY_CANONICAL_OUT receives one JSON line per input. Without the two variables this
// test does nothing. The Rust peer is rust/tests/parity_canonical_dump.rs; tools/parity/compare_canonical.py compares the two and both against the reference.
// The spec form is the generator's: null, a bool and a string are themselves; {"i": "123"} an integer; {"u": "1.5"} a value the library does not hold; a list a
// sequence; {"m": [[key, value], ...]} a map with its entries in the given order. r is ["ok", the bytes as text, the digest] or ["err", reason].

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func buildSpec(t *testing.T, spec any) *forms.Node {
	t.Helper()
	switch s := spec.(type) {
	case nil:
		return &forms.Node{Kind: forms.KindNull}
	case bool:
		return &forms.Node{Kind: forms.KindBool, Bool: s}
	case string:
		return &forms.Node{Kind: forms.KindStr, Str: s}
	case []any:
		n := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
		for _, x := range s {
			n.Items = append(n.Items, buildSpec(t, x))
		}
		return n
	case map[string]any:
		if i, ok := s["i"]; ok {
			v, err := strconv.ParseInt(i.(string), 10, 64)
			if err != nil {
				t.Fatal(err)
			}
			return &forms.Node{Kind: forms.KindInt, Int: v}
		}
		if u, ok := s["u"]; ok {
			return &forms.Node{Kind: forms.KindUnsupported, Str: u.(string)}
		}
		n := &forms.Node{Kind: forms.KindMap}
		for _, e := range s["m"].([]any) {
			pair := e.([]any)
			n.Entries = append(n.Entries, forms.Entry{Key: pair[0].(string), Value: buildSpec(t, pair[1])})
		}
		return n
	}
	t.Fatalf("a spec of an unknown form: %v", spec)
	return nil
}

func TestDumpCanonicalOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_CANONICAL_IN"), os.Getenv("PARITY_CANONICAL_OUT")
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
			ID   json.Number `json:"id"`
			Spec any         `json:"spec"`
		}
		if err := json.Unmarshal(sc.Bytes(), &c); err != nil {
			t.Fatal(err)
		}
		var r []any
		b, err := forms.CanonicalBytes(buildSpec(t, c.Spec))
		var ce *forms.CanonicalError
		switch {
		case err == nil:
			r = []any{"ok", string(b), forms.CanonicalDigest(b)}
		case errors.As(err, &ce):
			r = []any{"err", ce.Reason}
		default:
			t.Fatalf("an error that is not a CanonicalError: %v", err)
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
