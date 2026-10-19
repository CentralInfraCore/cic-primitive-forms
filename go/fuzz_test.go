package forms_test

// Parser fuzzing (PHASES.md: required from P1). `go test` runs the seeds below and anything
// under testdata/fuzz/FuzzParse as ordinary tests; `go test -fuzz FuzzParse` explores.
//
// The properties checked on every input: Parse does not panic, a document that parses
// survives Emit then Parse with the same value, and its positions lie inside the text.
//
// CIC_FUZZ_CANARY is the proof that the fuzz job can fail: with it set, an input that starts
// with "CANARY" is reported as a failure, and CI runs the committed canary seed with it set and
// requires the run to be red. It is test code; the product has no such switch.

import (
	"os"
	"strings"
	"testing"
	"unicode/utf8"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func FuzzParse(f *testing.F) {
	s := vectors(f) // seeds: the shared subset vectors, accepted and refused alike
	for _, v := range s.ParseOK {
		f.Add(v.YAML)
	}
	for _, v := range append(append([]refusal{}, s.Unsupported...), s.Syntax...) {
		f.Add(v.YAML)
	}
	for _, seed := range []string{strings.Repeat("k", 1100) + ": 1\n", "", "a: 1\na: 2\n", "[[[[", "{a: [1, {b: c}]}", "\xef\xbb\xbfa: 1", "a: |\n  x\n"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(text, "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		if !utf8.ValidString(text) {
			return
		}
		n, err := forms.Parse(text)
		if err != nil {
			return
		}
		back, err := forms.Parse(forms.Emit(n))
		if err != nil || !back.Same(n) {
			t.Fatalf("%q: emit/parse changed the value (%v)", text, err)
		}
		var walk func(*forms.Node)
		walk = func(n *forms.Node) {
			if n.Pos != nil && (n.Pos.Line < 1 || n.Pos.Column < 1 || n.Pos.Offset > len(text)) {
				t.Fatalf("%q: position %+v", text, *n.Pos)
			}
			for _, it := range n.Items {
				walk(it)
			}
			for _, e := range n.Entries {
				walk(e.Value)
			}
		}
		walk(n)
	})
}
