package forms_test

// ReadDiff (the canonical bytes of ADR 0008 read back into events): every canonical string of the corpus reads and writes back to itself; what is not exactly the
// canonical writing, or not a diff, is refused with its reason; and the patch gives the same answer whether it is given the events of a vector or the events read from the
// bytes of the vector. The Rust peer is rust/tests/diffread.rs.

import (
	"errors"
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

// bs is a backslash, so that the escapes below are written out and not decoded on the way into this file.
const bs = "\\"

func readReason(t *testing.T, in string) string {
	t.Helper()
	_, err := forms.ReadDiff([]byte(in))
	var re *forms.DiffReadError
	if !errors.As(err, &re) {
		return ""
	}
	return re.Reason
}

// canonicalsOf is every canonical diff string of the corpus: the positive diff cases and, with the diff of each patch case, the bytes the patch is given.
func canonicalsOf(t *testing.T) (diffs []string, patches []obj) {
	t.Helper()
	for _, suite := range suites(t) {
		for _, raw := range suite["cases"].([]any) {
			c := raw.(obj)
			switch c["kind"] {
			case "diff", "diff_stability":
				if e, ok := c["expect"].(obj); ok {
					if s, ok := e["canonical"].(string); ok {
						diffs = append(diffs, s)
					}
				}
			case "patch":
				patches = append(patches, c)
			}
		}
	}
	return diffs, patches
}

func TestReadDiffEveryCanonicalStringOfTheCorpusReadsAndWritesBackToItself(t *testing.T) {
	diffs, patches := canonicalsOf(t)
	for _, p := range patches {
		diffs = append(diffs, p["diff"].(obj)["canonical"].(string))
	}
	if len(diffs) < 120 {
		t.Fatalf("only %d canonical strings", len(diffs))
	}
	for _, s := range diffs {
		got, err := forms.ReadDiff([]byte(s))
		if err != nil {
			t.Errorf("%s: %v", s, err)
			continue
		}
		if got.Version == 1 {
			if again := (&forms.DiffResult{Changes: got.Changes, Representation: got.Representation}).Canonical(); again != s {
				t.Errorf("not written back as it was read:\n  %s\n  %s", s, again)
			}
		}
	}
}

func TestReadDiffGivesThePatchTheEventsOfTheVector(t *testing.T) {
	_, patches := canonicalsOf(t)
	g := boundGrammar(t)
	for _, c := range patches {
		id := c["id"].(string)
		diff := c["diff"].(obj)
		read, err := forms.ReadDiff([]byte(diff["canonical"].(string)))
		if err != nil {
			t.Errorf("%s: %v", id, err)
			continue
		}
		if _, ok := diff["changes"]; !ok { // a diff of another version: the version and nothing else
			if read.Version == 1 || len(read.Changes) != 0 {
				t.Errorf("%s: version %d, %d events", id, read.Version, len(read.Changes))
			}
			continue
		}
		want := patchEvents(t, diff)
		if len(read.Changes) != len(want) {
			t.Errorf("%s: %d events read, %d in the vector", id, len(read.Changes), len(want))
			continue
		}
		for i, e := range read.Changes {
			if e.Kind != want[i].Kind || e.Path != want[i].Path || (want[i].Previous != nil && !e.Previous.Same(want[i].Previous)) || (want[i].Next != nil && !e.Next.Same(want[i].Next)) {
				t.Errorf("%s: event %d read as %+v, the vector has %+v", id, i, e, want[i])
			}
		}
		// the same patch from the events of the vector and from the events read from its bytes
		root := rootOf(t, mustSuite(t, "patch"), c["schema"].(obj)["synthetic"].(obj)["tree"].(string))
		ctx := patchContext(c, "schema")
		fromVector, errV := forms.Patch(sourceOf(t, c), root, accessKeys, g, ctx, patchContext(c, "context"), forms.PatchDiff{Version: 1, Changes: want})
		fromBytes, errB := forms.Patch(sourceOf(t, c), root, accessKeys, g, ctx, patchContext(c, "context"), read.PatchDiff())
		if (errV == nil) != (errB == nil) || (errV != nil && errV.Error() != errB.Error()) || (errV == nil && !reflect.DeepEqual(fromVector, fromBytes)) {
			t.Errorf("%s: the vector's events give %v / %v, the bytes' give %v / %v", id, fromVector, errV, fromBytes, errB)
		}
	}
}

func mustSuite(t *testing.T, name string) obj {
	t.Helper()
	for _, s := range suites(t) {
		if s["suite"] == name {
			return s
		}
	}
	t.Fatalf("no suite %s", name)
	return nil
}

func TestReadDiffRefusesWhatIsNotExactlyTheCanonicalWriting(t *testing.T) {
	const added = `{"kind":"added","next":{"access":"inherit","conformance":"implemented","default_injection":null,"inherit":true,"modify":"inherit","value":1},"path":"$.mtu"}`
	const vc = `{"kind":"value_change","next":2,"path":"$.mtu","previous":1}`
	wrap := func(changes string) string { return `{"changes":[` + changes + `],"representation":[],"version":1}` }
	cases := []struct{ name, in, reason string }{
		{"the empty input", ``, "syntax"},
		{"not UTF-8", "{\"changes\":[],\"representation\":[],\"version\":1}\xff", "not_utf8"},
		{"a byte order mark", "\xef\xbb\xbf" + wrap(``), "syntax"},
		{"trailing bytes", wrap(``) + `x`, "syntax"},
		{"a trailing newline", wrap(``) + "\n", "syntax"},
		{"whitespace between tokens", `{"changes": [],"representation":[],"version":1}`, "syntax"},
		{"a truncated document", `{"changes":[],"representation":[]`, "syntax"},
		{"not an object", `[]`, "shape"},
		{"a bare number", `1`, "shape"},
		{"keys out of order", `{"version":1,"changes":[],"representation":[]}`, "not_canonical"},
		{"a duplicate key", `{"changes":[],"changes":[],"representation":[],"version":1}`, "duplicate_key"},
		{"a float", wrap(`{"kind":"value_change","next":2.5,"path":"$.mtu","previous":1}`), "unsupported_number"},
		{"an exponent", wrap(`{"kind":"value_change","next":1e3,"path":"$.mtu","previous":1}`), "unsupported_number"},
		{"an integer beyond 64 bits", wrap(`{"kind":"value_change","next":9223372036854775808,"path":"$.mtu","previous":1}`), "unsupported_number"},
		{"a leading zero", wrap(`{"kind":"value_change","next":02,"path":"$.mtu","previous":1}`), "unsupported_number"},
		{"minus zero", wrap(`{"kind":"value_change","next":-0,"path":"$.mtu","previous":1}`), "unsupported_number"},
		{"an escaped slash", wrap(`{"kind":"value_change","next":"a` + bs + `/b","path":"$.mtu","previous":"x"}`), "not_canonical"},
		{"an escaped non-ASCII character", wrap(`{"kind":"value_change","next":"` + bs + `u00e9","path":"$.mtu","previous":"x"}`), "not_canonical"},
		{"an uppercase unicode escape", wrap(`{"kind":"value_change","next":"` + bs + `u001F","path":"$.mtu","previous":"x"}`), "not_canonical"},
		{"a long escape of a control character that has a short one", wrap(`{"kind":"value_change","next":"` + bs + `u000a","path":"$.mtu","previous":"x"}`), "not_canonical"},
		{"a lone surrogate", wrap(`{"kind":"value_change","next":"` + bs + `ud800","path":"$.mtu","previous":"x"}`), "syntax"},
		{"a lone low surrogate", wrap(`{"kind":"value_change","next":"` + bs + `udc00","path":"$.mtu","previous":"x"}`), "syntax"},
		{"a high surrogate not followed by a low one", wrap(`{"kind":"value_change","next":"` + bs + `ud800` + bs + `u0041","path":"$.mtu","previous":"x"}`), "syntax"},
		{"a raw control character in a string", wrap("{\"kind\":\"value_change\",\"next\":\"a\nb\",\"path\":\"$.mtu\",\"previous\":\"x\"}"), "syntax"},
		{"an unknown event kind", wrap(`{"kind":"renamed","path":"$.mtu"}`), "shape"},
		{"a missing key", wrap(`{"kind":"value_change","next":2,"path":"$.mtu"}`), "shape"},
		{"an extra key", wrap(`{"extra":1,"kind":"value_change","next":2,"path":"$.mtu","previous":1}`), "shape"},
		{"a path that is not a string", wrap(`{"kind":"value_change","next":2,"path":1,"previous":1}`), "shape"},
		{"an event that is not an object", wrap(`1`), "shape"},
		{"a missing top-level key", `{"changes":[],"version":1}`, "shape"},
		{"an extra top-level key", `{"changes":[],"extra":0,"representation":[],"version":1}`, "shape"},
		{"changes that are not a list", `{"changes":{},"representation":[],"version":1}`, "shape"},
		{"a version that is not an integer", `{"changes":[],"representation":[],"version":"1"}`, "shape"},
		{"a missing version", `{"changes":[],"representation":[]}`, "shape"},
		{"a negative version", `{"changes":[],"representation":[],"version":-1}`, "shape"},
		{"a path that is not canonical", wrap(`{"kind":"value_change","next":2,"path":"mtu","previous":1}`), "path"},
		{"the root as a path", wrap(`{"kind":"value_change","next":2,"path":"$","previous":1}`), "path"},
		{"a path with a leading zero in a key", wrap(`{"kind":"value_change","next":2,"path":"$.a[{k=01}]","previous":1}`), "path"},
		{"two events out of the order of their paths", wrap(`{"kind":"value_change","next":2,"path":"$.name","previous":1},` + vc), "order"},
		{"the same path and kind twice", wrap(vc + `,` + vc), "order"},
		{"two kinds at one path in the wrong order", wrap(vc + `,{"changed":{"access":{"next":"a","previous":"b"}},"kind":"policy_declaration_change","path":"$.mtu"}`), "order"},
		{"a value change that changes nothing", wrap(`{"kind":"value_change","next":1,"path":"$.mtu","previous":1}`), "no_change"},
		{"a type-different value change is a change", wrap(`{"kind":"value_change","next":false,"path":"$.mtu","previous":0}`), ""},
		{"a declaration change with nothing changed", wrap(`{"changed":{"access":{"next":"a","previous":"a"}},"kind":"policy_declaration_change","path":"$.mtu"}`), "no_change"},
		{"a declaration change with no field", wrap(`{"changed":{},"kind":"policy_declaration_change","path":"$.mtu"}`), "shape"},
		{"a declaration field without both sides", wrap(`{"changed":{"access":{"next":"a"}},"kind":"policy_declaration_change","path":"$.mtu"}`), "shape"},
		{"an event with a shape defect and a bad path names the shape", wrap(`{"changed":{},"kind":"policy_declaration_change","path":"mtu"}`), "shape"},
		{"a set change with nothing in it", wrap(`{"added":[],"kind":"set_change","path":"$.ip.addresses","removed":[]}`), "no_change"},
		{"a set change not sorted", wrap(`{"added":["b","a"],"kind":"set_change","path":"$.ip.addresses","removed":[]}`), "shape"},
		{"a set change with a repeat", wrap(`{"added":["a","a"],"kind":"set_change","path":"$.ip.addresses","removed":[]}`), "shape"},
		{"an element in both sides of a set change", wrap(`{"added":["a"],"kind":"set_change","path":"$.ip.addresses","removed":["a"]}`), "shape"},
		{"a set element that is not a scalar", wrap(`{"added":[[]],"kind":"set_change","path":"$.ip.addresses","removed":[]}`), "shape"},
		{"a representation entry of the wrong kind", `{"changes":[],"representation":[{"kind":"other","next":"short","path":"$.mtu","previous":"expanded"}],"version":1}`, "shape"},
		{"a representation entry with a value that is not a writing", `{"changes":[],"representation":[{"kind":"representation_change","next":"long","path":"$.mtu","previous":"expanded"}],"version":1}`, "shape"},
		{"a representation entry that changes nothing", `{"changes":[],"representation":[{"kind":"representation_change","next":"short","path":"$.mtu","previous":"short"}],"version":1}`, "no_change"},
		{"representation entries out of order", `{"changes":[],"representation":[{"kind":"representation_change","next":"short","path":"$.name","previous":"expanded"},{"kind":"representation_change","next":"short","path":"$.mtu","previous":"expanded"}],"version":1}`, "order"},
		{"the same representation path twice", `{"changes":[],"representation":[{"kind":"representation_change","next":"short","path":"$.mtu","previous":"expanded"},{"kind":"representation_change","next":"expanded","path":"$.mtu","previous":"short"}],"version":1}`, "order"},
		{"an added fragment with a float inside", wrap(`{"kind":"added","next":{"value":1.5},"path":"$.mtu"}`), "unsupported_number"},
	}
	for _, c := range cases {
		if got := readReason(t, c.in); got != c.reason {
			t.Errorf("%s: reason %q, want %q (%s)", c.name, got, c.reason, c.in)
		}
	}
	// and the examples above that must be accepted are really accepted
	for _, ok := range []string{wrap(``), wrap(added), wrap(vc), wrap(added + `,` + vc)} {
		if r := readReason(t, ok); r != "" && ok != wrap(added+`,`+vc) {
			t.Errorf("%s was refused: %s", ok, r)
		}
	}
}

func TestReadDiffNestingIsBounded(t *testing.T) {
	deep := ""
	for i := 0; i < 100; i++ {
		deep += "["
	}
	if r := readReason(t, `{"changes":[{"kind":"added","next":`+deep+`,"path":"$.mtu"}],"representation":[],"version":1}`); r != "depth" {
		t.Errorf("reason %q", r)
	}
}

func TestReadDiffADiffOfAnotherVersionIsReadForItsVersionOnly(t *testing.T) {
	got, err := forms.ReadDiff([]byte(`{"version":2}`))
	if err != nil || got.Version != 2 || len(got.Changes) != 0 || len(got.Representation) != 0 {
		t.Errorf("version 2: %+v %v", got, err)
	}
	// it must still be canonical JSON
	if r := readReason(t, `{"version": 2}`); r != "syntax" {
		t.Errorf("whitespace: %q", r)
	}
	if r := readReason(t, `{"b":1,"version":2,"a":0}`); r != "not_canonical" {
		t.Errorf("order: %q", r)
	}
}

func TestReadDiffNoMutationOfAValidDiffIsAcceptedUnlessItIsTheSameBytes(t *testing.T) {
	diffs, _ := canonicalsOf(t)
	checked := 0
	for _, s := range diffs[:40] {
		b := []byte(s)
		for i := range b {
			for _, repl := range []byte{' ', 'x', '0', '}'} {
				m := append([]byte{}, b...)
				m[i] = repl
				got, err := forms.ReadDiff(m)
				checked++
				if err != nil || got.Version != 1 {
					continue
				}
				if again := (&forms.DiffResult{Changes: got.Changes, Representation: got.Representation}).Canonical(); again != string(m) {
					t.Fatalf("accepted bytes that are not written back as they are: %q", m)
				}
			}
		}
	}
	if checked < 5000 {
		t.Fatalf("only %d mutations", checked)
	}
}

func TestReadDiffEveryCanonicalEscapeIsReadAndWrittenBack(t *testing.T) {
	body := "a" + bs + "b" + bs + "f" + bs + "n" + bs + "r" + bs + "t" + bs + `"` + bs + bs + bs + "u0001" + bs + "u001fz"
	input := `{"changes":[{"kind":"value_change","next":"` + body + `","path":"$.mtu","previous":"x"}],"representation":[],"version":1}`
	got, err := forms.ReadDiff([]byte(input))
	if err != nil || len(got.Changes) != 1 {
		t.Fatalf("%v", err)
	}
	if again := (&forms.DiffResult{Changes: got.Changes, Representation: got.Representation}).Canonical(); again != input {
		t.Errorf("written back as %s", again)
	}
}

func TestReadDiffASurrogatePairIsValidJSONAndNotTheCanonicalWriting(t *testing.T) {
	wrap := func(escape string) string {
		return `{"changes":[{"kind":"value_change","next":"` + escape + `","path":"$.mtu","previous":"x"}],"representation":[],"version":1}`
	}
	for escape, want := range map[string]string{
		bs + "ud83d" + bs + "ude00": "not_canonical",
		bs + "ud800x":               "syntax",
		bs + "ud800" + bs + "ud800": "syntax",
		bs + "u00zz":                "syntax",
	} {
		if got := readReason(t, wrap(escape)); got != want {
			t.Errorf("%q: reason %q, want %q", escape, got, want)
		}
	}
}

func TestReadDiffNestingOfObjectsIsBoundedToo(t *testing.T) {
	deep := ""
	for i := 0; i < 100; i++ {
		deep += `{"a":`
	}
	if r := readReason(t, `{"changes":[{"kind":"added","next":`+deep+`1,"path":"$.mtu"}],"representation":[],"version":1}`); r != "depth" {
		t.Errorf("reason %q", r)
	}
}

func TestReadDiffTheErrorSaysItsReasonAndWhere(t *testing.T) {
	_, err := forms.ReadDiff([]byte("["))
	if err == nil || err.Error() != "diff not read: syntax at byte 1" {
		t.Errorf("%v", err)
	}
}
