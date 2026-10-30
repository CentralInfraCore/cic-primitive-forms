package forms_test

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// The canonical bytes (P5-1) against the shared table testdata/p5/canonical-vectors.tsv, which tools/p5/engine_vectors.py checks against the pinned
// cic-primitive-engine and against a reference written apart. The Rust peer is rust/tests/canonical_corpus.rs.
func TestEveryInRangeVectorGivesItsBytesAndDigestAndEveryOtherIsOutsideTheValue(t *testing.T) {
	text, err := os.ReadFile("../testdata/p5/canonical-vectors.tsv")
	if err != nil {
		t.Fatal(err)
	}
	ranged, outside := 0, 0
	for _, line := range strings.Split(string(text), "\n") {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		f := strings.Split(line, "\t")
		if len(f) != 7 {
			t.Fatalf("a line of the table has %d fields: %q", len(f), line)
		}
		var input string
		if err := json.Unmarshal([]byte(f[3]), &input); err != nil {
			t.Fatal(err)
		}
		node, perr := forms.Parse(input)
		if f[2] != "in_range" {
			if perr == nil {
				t.Errorf("%s: the value holds it after all", f[1])
			}
			outside++
			continue
		}
		var want string
		if err := json.Unmarshal([]byte(f[4]), &want); err != nil {
			t.Fatal(err)
		}
		if perr != nil {
			t.Fatalf("%s: %v", f[1], perr)
		}
		got, cerr := forms.CanonicalBytes(node)
		if cerr != nil {
			t.Fatalf("%s: %v", f[1], cerr)
		}
		if string(got) != want {
			t.Errorf("%s: got %q want %q", f[1], got, want)
		}
		if d := forms.CanonicalDigest(got); d != f[5] {
			t.Errorf("%s: digest %s want %s", f[1], d, f[5])
		}
		ranged++
	}
	if ranged != 20 || outside != 6 {
		t.Errorf("table has %d in range and %d outside, want 20 and 6", ranged, outside)
	}
}

func TestTheDigestOfTheEmptyMappingIsTheOneTheEnginePins(t *testing.T) {
	b, err := forms.CanonicalBytes(&forms.Node{Kind: forms.KindMap})
	if err != nil || string(b) != "{}" {
		t.Fatalf("%q %v", b, err)
	}
	if d := forms.CanonicalDigest(b); d != "sha256:44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a" {
		t.Errorf("digest %s", d)
	}
}

func reasonOf(t *testing.T, n *forms.Node) string {
	t.Helper()
	_, err := forms.CanonicalBytes(n)
	var ce *forms.CanonicalError
	if !errors.As(err, &ce) {
		t.Fatalf("want a CanonicalError, got %v", err)
	}
	return ce.Reason
}

func TestAFloatOrALargeIntegerIsRefusedAndSoIsARepeatedKeyOrANonUTF8String(t *testing.T) {
	unsupported := &forms.Node{Kind: forms.KindUnsupported, Str: "1.5"}
	if r := reasonOf(t, unsupported); r != forms.CanonicalUnsupportedValue {
		t.Errorf("reason %s", r)
	}
	nested := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "a", Value: unsupported}}}}}
	if r := reasonOf(t, nested); r != forms.CanonicalUnsupportedValue {
		t.Errorf("reason %s", r)
	}
	one := func(k string, v int64) forms.Entry { return forms.Entry{Key: k, Value: &forms.Node{Kind: forms.KindInt, Int: v}} }
	dup := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{one("a", 1), one("b", 2), one("a", 3)}}
	if r := reasonOf(t, dup); r != forms.CanonicalDuplicateKey {
		t.Errorf("reason %s", r)
	}
	if _, err := forms.CanonicalBytes(dup); err.Error() != "no canonical bytes: duplicate_key" {
		t.Errorf("message %q", err.Error())
	}
	if r := reasonOf(t, &forms.Node{Kind: forms.KindStr, Str: "\xff"}); r != forms.CanonicalNotUTF8 {
		t.Errorf("reason %s", r)
	}
	if r := reasonOf(t, &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{one("\xff", 1)}}); r != forms.CanonicalNotUTF8 {
		t.Errorf("reason %s", r)
	}
}

func TestTheOrderOfTheEntriesGivenDoesNotChangeTheBytesButTheOrderOfASequenceDoes(t *testing.T) {
	one := func(k string, v int64) forms.Entry { return forms.Entry{Key: k, Value: &forms.Node{Kind: forms.KindInt, Int: v}} }
	a, _ := forms.CanonicalBytes(&forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{one("y", 1), one("x", 2)}})
	b, _ := forms.CanonicalBytes(&forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{one("x", 2), one("y", 1)}})
	if string(a) != string(b) {
		t.Errorf("%q != %q", a, b)
	}
	seq := func(x, y int64) *forms.Node {
		return &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{{Kind: forms.KindInt, Int: x}, {Kind: forms.KindInt, Int: y}}}
	}
	s1, _ := forms.CanonicalBytes(seq(1, 2))
	s2, _ := forms.CanonicalBytes(seq(2, 1))
	if string(s1) == string(s2) {
		t.Errorf("a sequence was reordered")
	}
}
