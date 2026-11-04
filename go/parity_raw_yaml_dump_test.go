package forms

// Dumps what Parse and MapRelease answer for the RAW BYTES of the cases of tools/parity/gen_raw_yaml.py (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b).
// PARITY_RAW_YAML_IN is a JSON-lines file of cases ({"id", "bytes_hex", "pin", ...}); PARITY_RAW_YAML_OUT receives exactly one JSON line per input line, in order. Without the two
// variables this test does nothing. The Rust peer is rust/tests/parity_raw_yaml_dump.rs; tools/parity/compare_raw_yaml.py compares the readers.
//
// A line is {"id", "ok": true, "tree", "mapping"} or {"id", "ok": false, "class", "code"}. The bytes are decoded as UTF-8 here, because Parse takes text: a byte sequence that is not
// UTF-8 is answered invalid_utf8 before the parser sees it, and the line says so. Every case runs in a goroutine with a wall-clock limit (PARITY_RAW_YAML_TIMEOUT, seconds, default 10):
// a case that does not answer in time is written as TIMEOUT and the run goes on. The memory limit is set by the runner on the whole process.

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"
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

// rawYAMLRefusal is the class of a refusal: duplicate_key when every violation is one, syntax for the syntax, unsupported-syntax and limit errors, else other with the code.
func rawYAMLRefusal(err error) (string, string) {
	var fe *Error
	if !errors.As(err, &fe) {
		return "other", "NOT_AN_ERROR_TYPE"
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
		}
		if allDup {
			return "duplicate_key", strings.Join(codes, ",")
		}
		return "other", strings.Join(codes, ",")
	case ErrUnsupported, ErrSyntax, ErrLimit:
		return "syntax", fe.Code()
	}
	return "other", fe.Code()
}

func rawYAMLAnswer(id string, b []byte, pin map[string]string) map[string]any {
	if !utf8.Valid(b) {
		return map[string]any{"id": id, "ok": false, "class": "invalid_utf8", "code": "INVALID_UTF8"}
	}
	n, err := Parse(string(b))
	if err != nil {
		class, code := rawYAMLRefusal(err)
		return map[string]any{"id": id, "ok": false, "class": class, "code": code}
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
	return map[string]any{"id": id, "ok": true, "tree": rawYAMLTree(n), "mapping": mapping}
}

func TestDumpRawYAMLOutcomes(t *testing.T) {
	in, out := os.Getenv("PARITY_RAW_YAML_IN"), os.Getenv("PARITY_RAW_YAML_OUT")
	if in == "" || out == "" {
		return
	}
	limit := 10 * time.Second
	if s := os.Getenv("PARITY_RAW_YAML_TIMEOUT"); s != "" {
		if n, err := strconv.Atoi(s); err == nil {
			limit = time.Duration(n) * time.Second
		}
	}
	f, err := os.Open(in)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var sb strings.Builder
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
		done := make(chan map[string]any, 1)
		go func() { done <- rawYAMLAnswer(c.ID, b, c.Pin) }()
		var line map[string]any
		select {
		case line = <-done:
		case <-time.After(limit):
			line = map[string]any{"id": c.ID, "ok": false, "class": "TIMEOUT", "code": "TIMEOUT"}
		}
		enc, err := json.Marshal(line)
		if err != nil {
			t.Fatal(err)
		}
		sb.Write(enc)
		sb.WriteByte('\n')
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, []byte(sb.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}
