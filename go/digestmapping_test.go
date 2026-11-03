package forms

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The release record to receipt digest mapping (P5-3, condition 8; docs/P5-DIGEST-MAPPING-CONTRACT.md): the shared cases of testdata/p5/digest-mapping.json, written by
// tools/p5/digest_mapping_cases.py (expected values computed outside this code: raw bytes, openssl, the pin). The Rust peer is rust/tests/digest_mapping.rs and the
// Python reference tools/p5/digest_mapping.py.

type mappingCase struct {
	ID     string            `json:"id"`
	Kind   string            `json:"kind"`
	Input  json.RawMessage   `json:"input"`
	Record json.RawMessage   `json:"record"`
	Pin    map[string]string `json:"pin"`
	Expect json.RawMessage   `json:"expect"`
	Refuse *string           `json:"refuse"`
	Field  *string           `json:"field"`
}

type mappingFile struct {
	Reasons []string      `json:"reasons"`
	Cases   []mappingCase `json:"cases"`
}

// mappingNodeOf reads a JSON text into a Node. A number is an integer only when its spelling is one; every other number (3.0, 1e2) is KindUnsupported, as the
// document model has it, so that the float 3.0 is not the integer 3.
func mappingNodeOf(t *testing.T, raw []byte) *Node {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	return nodeOfJSON(t, v)
}

func nodeOfJSON(t *testing.T, v any) *Node {
	t.Helper()
	switch x := v.(type) {
	case nil:
		return &Node{Kind: KindNull}
	case bool:
		return &Node{Kind: KindBool, Bool: x}
	case string:
		return &Node{Kind: KindStr, Str: x}
	case json.Number:
		if i, err := strconv.ParseInt(x.String(), 10, 64); err == nil {
			return &Node{Kind: KindInt, Int: i}
		}
		return &Node{Kind: KindUnsupported, Str: x.String()}
	case []any:
		n := &Node{Kind: KindSeq}
		for _, item := range x {
			n.Items = append(n.Items, nodeOfJSON(t, item))
		}
		return n
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		n := &Node{Kind: KindMap}
		for _, k := range keys {
			n.Entries = append(n.Entries, Entry{Key: k, Value: nodeOfJSON(t, x[k])})
		}
		return n
	}
	t.Fatalf("unexpected JSON value %T", v)
	return nil
}

func readMappingCases(t *testing.T) mappingFile {
	t.Helper()
	text, err := os.ReadFile("../testdata/p5/digest-mapping.json")
	if err != nil {
		t.Fatal(err)
	}
	var f mappingFile
	if err := json.Unmarshal(text, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func TestDigestMappingSharedCases(t *testing.T) {
	f := readMappingCases(t)
	if len(f.Cases) < 80 {
		t.Fatalf("the shared file has %d cases: not the file this test was written for", len(f.Cases))
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			var got map[string]string
			var err error
			switch c.Kind {
			case "convert":
				var s string
				s, err = ReceiptDigestOf(mappingNodeOf(t, c.Input), "")
				got = map[string]string{"digest": s}
			case "map":
				var m *DigestMapping
				m, err = MapRelease(mappingNodeOf(t, c.Record), c.Pin)
				if m != nil {
					got = map[string]string{"primitive_build_hash": m.PrimitiveBuildHash, "grammar_sha256": m.GrammarSHA256, "grammar_schema_sha256": m.GrammarSchemaSHA256}
				}
			default:
				t.Fatalf("unknown kind %q", c.Kind)
			}

			if c.Refuse == nil {
				if err != nil {
					t.Fatalf("refused %v, expected a value", err)
				}
				if c.Kind == "convert" {
					var want string
					if e := json.Unmarshal(c.Expect, &want); e != nil {
						t.Fatal(e)
					}
					if got["digest"] != want {
						t.Fatalf("got %q, want %q", got["digest"], want)
					}
					return
				}
				var want map[string]string
				if e := json.Unmarshal(c.Expect, &want); e != nil {
					t.Fatal(e)
				}
				for k, v := range want {
					if got[k] != v {
						t.Fatalf("%s: got %q, want %q", k, got[k], v)
					}
				}
				return
			}

			var me *MappingError
			if !errors.As(err, &me) {
				t.Fatalf("expected the refusal %q, got %v", *c.Refuse, err)
			}
			if me.Reason != *c.Refuse {
				t.Fatalf("reason %q, want %q (field %q)", me.Reason, *c.Refuse, me.Field)
			}
			if c.Field != nil && me.Field != *c.Field {
				t.Fatalf("field %q, want %q (reason %q)", me.Field, *c.Field, me.Reason)
			}
		})
	}
}

func TestDigestMappingReasonsAreTheContractsAndEachIsUsed(t *testing.T) {
	f := readMappingCases(t)
	ours := []string{MappingEnvelopeNot3, MappingFieldMissing, MappingFieldType, MappingBase64Shape, MappingBase64NotCanonical, MappingBase64Length, MappingDigestForm,
		MappingArtifactAbsent, MappingArtifactDuplicate, MappingProvenanceArtifactsDiffer, MappingRecordPinDiffer}
	a, b := append([]string(nil), ours...), append([]string(nil), f.Reasons...)
	sort.Strings(a)
	sort.Strings(b)
	if strings.Join(a, ",") != strings.Join(b, ",") {
		t.Fatalf("the reasons of this file %v are not those of the shared file %v", a, b)
	}
}

// The length guard cannot be reached through the public conversion (43 characters and a padding are 32 bytes); it is exercised here directly, with the canonical check
// that follows it.
func TestDigestMappingCheckDigestBytes(t *testing.T) {
	for _, n := range []int{0, 31, 33, 64} {
		err := checkDigestBytes(make([]byte, n), "", "f")
		if err == nil || err.Reason != MappingBase64Length || err.Field != "f" {
			t.Fatalf("%d bytes: %v", n, err)
		}
	}
	zeros := strings.Repeat("A", 43) + "="
	if err := checkDigestBytes(make([]byte, 32), zeros, "f"); err != nil {
		t.Fatalf("32 zero bytes in their one spelling: %v", err)
	}
	if err := checkDigestBytes(make([]byte, 32), strings.Repeat("A", 42)+"B=", "f"); err == nil || err.Reason != MappingBase64NotCanonical {
		t.Fatalf("another spelling of the same bytes: %v", err)
	}
}

// The message names the reason and the field; it is the text of the refusal that a caller prints.
func TestDigestMappingErrorText(t *testing.T) {
	if got := (&MappingError{MappingDigestForm, "artifacts.files[x].digest"}).Error(); got != "no digest mapping: digest_form (artifacts.files[x].digest)" {
		t.Fatalf("%q", got)
	}
	if got := (&MappingError{Reason: MappingFieldMissing}).Error(); got != "no digest mapping: field_missing" {
		t.Fatalf("%q", got)
	}
}

// The shared cases take a member away or change its value; they never put the wrong KIND of node on the way to it. The cases below do, one defect at a time, on the record
// of cic-primitives v0.2.1 (the first of the shared cases), so that the refusal has to come from the check that is meant to give it.

func mappingGoodRecord(t *testing.T) (*Node, map[string]string) {
	t.Helper()
	f := readMappingCases(t)
	for _, c := range f.Cases {
		if c.Kind == "map" && c.Refuse == nil {
			return mappingNodeOf(t, c.Record), c.Pin
		}
	}
	t.Fatal("no accepted map case in the shared file")
	return nil, nil
}

// mappingSet replaces the value at a dotted path (a number is a sequence index) of a copy of the record.
func mappingSet(t *testing.T, record *Node, path string, value *Node) *Node {
	t.Helper()
	out := mappingCopy(record)
	cur := out
	parts := strings.Split(path, ".")
	for i, part := range parts {
		last := i == len(parts)-1
		if cur.Kind == KindSeq {
			idx, err := strconv.Atoi(part)
			if err != nil || idx >= len(cur.Items) {
				t.Fatalf("bad path %q", path)
			}
			if last {
				cur.Items[idx] = value
				return out
			}
			cur = cur.Items[idx]
			continue
		}
		found := false
		for j := range cur.Entries {
			if cur.Entries[j].Key == part {
				found = true
				if last {
					cur.Entries[j].Value = value
					return out
				}
				cur = cur.Entries[j].Value
				break
			}
		}
		if !found {
			t.Fatalf("bad path %q", path)
		}
	}
	return out
}

func mappingCopy(n *Node) *Node {
	if n == nil {
		return nil
	}
	c := *n
	c.Items = make([]*Node, len(n.Items))
	for i, it := range n.Items {
		c.Items[i] = mappingCopy(it)
	}
	c.Entries = make([]Entry, len(n.Entries))
	for i, e := range n.Entries {
		c.Entries[i] = Entry{Key: e.Key, Value: mappingCopy(e.Value)}
	}
	return &c
}

func mappingWant(t *testing.T, record *Node, pin map[string]string, reason, field string) {
	t.Helper()
	_, err := MapRelease(record, pin)
	var me *MappingError
	if !errors.As(err, &me) {
		t.Fatalf("expected %s (%s), got %v", reason, field, err)
	}
	if me.Reason != reason || me.Field != field {
		t.Fatalf("got %s (%s), want %s (%s)", me.Reason, me.Field, reason, field)
	}
}

func TestDigestMappingTheWrongKindOfNodeOnTheWay(t *testing.T) {
	good, pin := mappingGoodRecord(t)
	str := &Node{Kind: KindStr, Str: "x"}
	one := &Node{Kind: KindInt, Int: 1}

	// a parent that is not a mapping is a path that is not there
	mappingWant(t, mappingSet(t, good, "release", one), pin, MappingEnvelopeNot3, "release.envelope")
	mappingWant(t, mappingSet(t, good, "provenance", str), pin, MappingEnvelopeNot3, "provenance.envelope")

	// an envelope that is not an integer is refused whatever its other fields hold: a node of another kind that carries the number 3
	for _, k := range []Kind{KindBool, KindStr, KindUnsupported, KindNull} {
		n := &Node{Kind: k, Int: 3, Str: "3", Bool: true}
		mappingWant(t, mappingSet(t, good, "release.envelope", n), pin, MappingEnvelopeNot3, "release.envelope")
	}

	// artifacts.files: null is missing, not a type error
	mappingWant(t, mappingSet(t, good, "artifacts.files", &Node{Kind: KindNull}), pin, MappingFieldMissing, "artifacts.files")

	// an element of the list that is not a mapping is not an entry; it is skipped and the path stays absent when it was the only one
	for _, k := range []*Node{{Kind: KindNull}, str, one, {Kind: KindSeq}} {
		rec := mappingSet(t, good, "artifacts.files.1", k)
		mappingWant(t, rec, pin, MappingArtifactAbsent, mappingCheckGrammar)
	}
	// a nil element must not panic either
	rec := mappingCopy(good)
	files := mappingGet(rec, "artifacts.files")
	files.Items[1] = nil
	mappingWant(t, rec, pin, MappingArtifactAbsent, mappingCheckGrammar)

	// an entry whose path is missing, null or not a string is not the entry of any path
	for _, p := range []*Node{{Kind: KindNull}, one, {Kind: KindSeq}, {Kind: KindMap}} {
		rec := mappingSet(t, good, "artifacts.files.1.path", p)
		mappingWant(t, rec, pin, MappingArtifactAbsent, mappingCheckGrammar)
	}
	noPath := mappingCopy(good)
	entry := mappingGet(noPath, "artifacts.files").Items[1]
	entry.Entries = entry.Entries[:0]
	mappingWant(t, noPath, pin, MappingArtifactAbsent, mappingCheckGrammar)
}

// A mapping with the same key twice is two documents to two readers, and a record that is read for its digests must not be one. The mapping does not choose between the
// two keys (it would make a choice a norm); it is only ever given a Node that Parse made, and Parse refuses a duplicate key (DUPLICATE_KEY), in every place the mapping
// reads. Both halves are shown on the record of v0.2.1 written out as YAML: it goes through Parse and MapRelease, and the same text with one key twice does not get past Parse.
func TestDigestMappingADuplicateKeyNeverReachesIt(t *testing.T) {
	good, pin := mappingGoodRecord(t)
	text := Emit(good)

	parsed, err := Parse(text)
	if err != nil {
		t.Fatalf("the record written out does not parse: %v", err)
	}
	if _, err := MapRelease(parsed, pin); err != nil {
		t.Fatalf("the record that went through Emit and Parse is refused: %v", err)
	}

	// the key lines the mapping reads, each repeated once under its own indentation with a different value
	for _, line := range []string{"  envelope: 3", "  build_hash: ", "  grammar_sha256: ", "  grammar_schema_sha256: ", "      path: ", "      digest: ", "  files:"} {
		i := strings.Index(text, line)
		if i < 0 {
			t.Fatalf("the written record has no line %q:\n%s", line, text)
		}
		end := strings.IndexByte(text[i:], '\n') + i
		repeated := text[i:end] + "x"
		if line == "  files:" {
			repeated = text[i:end]
		}
		dup := text[:end] + "\n" + repeated + text[end:]
		_, perr := Parse(dup)
		var e *Error
		if !errors.As(perr, &e) || e.Code() != "DUPLICATE_KEY" {
			t.Fatalf("a repeated %q is not refused as DUPLICATE_KEY: %v", strings.TrimSpace(line), perr)
		}
	}
}

// Every character of the text is judged, the first one too.
func TestDigestMappingEveryPositionOfTheShapeIsChecked(t *testing.T) {
	good := "HVazYxdg737P2bMy2Enkj5ej1VfF+TQ4pDub86G3p2s="
	for i := 0; i < 43; i++ {
		bad := good[:i] + "-" + good[i+1:]
		_, err := ReceiptDigestOf(&Node{Kind: KindStr, Str: bad}, "f")
		var me *MappingError
		if !errors.As(err, &me) || me.Reason != MappingBase64Shape {
			t.Fatalf("a '-' at position %d: %v", i, err)
		}
	}
	if _, err := ReceiptDigestOf(&Node{Kind: KindStr, Str: good}, "f"); err != nil {
		t.Fatalf("the unchanged value: %v", err)
	}
}

// A nil record or a nil node is the same as an absent member, never a panic.
func TestDigestMappingNilInput(t *testing.T) {
	_, err := MapRelease(nil, nil)
	var me *MappingError
	if !errors.As(err, &me) || me.Reason != MappingEnvelopeNot3 {
		t.Fatalf("nil record: %v", err)
	}
	if _, err := ReceiptDigestOf(nil, "x"); !errors.As(err, &me) || me.Reason != MappingFieldType {
		t.Fatalf("nil node: %v", err)
	}
}
