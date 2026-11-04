package forms

// Dumps what Parse and MapRelease answer for the RAW BYTES of the cases of tools/parity/gen_raw_yaml.py (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b).
// PARITY_RAW_YAML_IN is a JSON-lines file of cases ({"id", "bytes_hex", "pin", ...}); PARITY_RAW_YAML_OUT receives exactly one JSON line per input line, in order. Without the two
// variables this test does nothing. The Rust peer is rust/tests/parity_raw_yaml_dump.rs; tools/parity/compare_raw_yaml.py compares the readers.
//
// A line is {"id", "ok": true, "tree", "positions", "mapping"} or {"id", "ok": false, "class", "code", "locations"}; positions lists the source position of every node and every mapping
// key in document order, locations the location of every violation (semantic parity and position parity are judged apart by the compare). The bytes are decoded as UTF-8 here, because Parse takes text: a byte sequence that is not
// UTF-8 is answered invalid_utf8 before the parser sees it, and the line says so. Each line is written as soon as the case is answered.
// There is NO timer in this process: a case that does not answer is killed, with its whole process, by the runner (tools/parity/run_raw_yaml.py), which also sets the memory limit; the runner
// then records TIMEOUT or CRASH for that case and restarts after it.

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"unicode/utf8"
)

func rawYAMLTree(n *Node) any {
	switch n.Kind {
	case KindNull:
		return map[string]any{"k": "null", "v": nil}
	case KindBool:
		return map[string]any{"k": "bool", "v": n.Bool}
	case KindInt:
		return map[string]any{"k": "int", "v": n.Int}
	case KindStr:
		return map[string]any{"k": "str", "v": n.Str}
	case KindSeq:
		items := []any{}
		for _, it := range n.Items {
			items = append(items, rawYAMLTree(it))
		}
		return map[string]any{"k": "seq", "v": items}
	case KindMap:
		entries := []any{}
		for _, e := range n.Entries {
			entries = append(entries, []any{e.Key, rawYAMLTree(e.Value)})
		}
		return map[string]any{"k": "map", "v": entries}
	}
	return map[string]any{"k": "unsupported", "v": n.Str}
}

func rawYAMLPos(p *Pos) any {
	if p == nil {
		return nil
	}
	return []int{p.Line, p.Column, p.Offset}
}

// rawYAMLPositions appends the position of every node, and of every mapping key before its value, in document order.
func rawYAMLPositions(n *Node, out *[]any) {
	*out = append(*out, rawYAMLPos(n.Pos))
	switch n.Kind {
	case KindSeq:
		for _, it := range n.Items {
			rawYAMLPositions(it, out)
		}
	case KindMap:
		for _, e := range n.Entries {
			*out = append(*out, rawYAMLPos(e.KeyPos))
			rawYAMLPositions(e.Value, out)
		}
	}
}

// rawYAMLRefusal is the class of a refusal: duplicate_key when every violation is one, syntax for the syntax, unsupported-syntax and limit errors, else other with the code.
func rawYAMLRefusal(err error) (string, string, []any) {
	var fe *Error
	locations := []any{}
	if !errors.As(err, &fe) {
		return "other", "NOT_AN_ERROR_TYPE", locations
	}
	switch fe.Kind {
	case ErrContract:
		allDup := true
		codes := []string{}
		for _, v := range fe.Violations {
			codes = append(codes, v.Code)
			if v.Code != "DUPLICATE_KEY" {
				allDup = false
			}
			if v.Location.Available {
				locations = append(locations, []int{v.Location.Pos.Line, v.Location.Pos.Column, v.Location.Pos.Offset})
			} else {
				locations = append(locations, nil)
			}
		}
		if allDup {
			return "duplicate_key", strings.Join(codes, ","), locations
		}
		return "other", strings.Join(codes, ","), locations
	case ErrUnsupported, ErrSyntax, ErrLimit:
		return "syntax", fe.Code(), locations
	}
	return "other", fe.Code(), locations
}

func rawYAMLAnswer(id string, b []byte, pin map[string]string) map[string]any {
	if !utf8.Valid(b) {
		return map[string]any{"id": id, "ok": false, "class": "invalid_utf8", "code": "INVALID_UTF8", "locations": []any{}}
	}
	n, err := Parse(string(b))
	if err != nil {
		class, code, locations := rawYAMLRefusal(err)
		return map[string]any{"id": id, "ok": false, "class": class, "code": code, "locations": locations}
	}
	var mapping any
	if pin != nil {
		m, merr := MapRelease(n, pin)
		var me *MappingError
		switch {
		case merr == nil:
			mapping = map[string]any{"ok": map[string]string{"primitive_build_hash": m.PrimitiveBuildHash, "grammar_sha256": m.GrammarSHA256, "grammar_schema_sha256": m.GrammarSchemaSHA256}}
		case errors.As(merr, &me):
			var field any
			if me.Field != "" {
				field = me.Field
			}
			mapping = map[string]any{"refuse": me.Reason, "field": field}
		default:
			mapping = map[string]any{"refuse": "NOT_A_MAPPING_ERROR", "field": nil}
		}
	}
	ps := []any{}
	rawYAMLPositions(n, &ps)
	return map[string]any{"id": id, "ok": true, "tree": rawYAMLTree(n), "positions": ps, "mapping": mapping}
}

func TestDumpRawYAMLOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_RAW_YAML_IN"), os.Getenv("PARITY_RAW_YAML_OUT")
	if in == "" || out == "" {
		return
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w, err := os.Create(out)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<24), 1<<27)
	for sc.Scan() {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var c struct {
			ID       string            `json:"id"`
			BytesHex string            `json:"bytes_hex"`
			Pin      map[string]string `json:"pin"`
		}
		if err := json.Unmarshal([]byte(sc.Text()), &c); err != nil {
			t.Fatalf("input line is not a case: %v", err)
		}
		b, err := hex.DecodeString(c.BytesHex)
		if err != nil {
			t.Fatal(err)
		}
		enc, err := json.Marshal(rawYAMLAnswer(c.ID, b, c.Pin))
		if err != nil {
			t.Fatal(err)
		}
		// written and synced at once; the parser runs on this goroutine, with no timer: the runner kills the process of a case that does not come back
		if _, err := w.Write(append(enc, '\n')); err != nil {
			t.Fatal(err)
		}
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
}
