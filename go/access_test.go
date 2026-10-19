package forms_test

// Reading access-policy/1 values (cic-primitives D-018): the shared vectors (testdata/p1/access.json,
// read by the Rust peer too) and the behaviour around them (rust/tests/access.rs has the same tests).

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type accessVectors struct {
	ReadMode []struct {
		Name    string          `json:"name"`
		Grammar int64           `json:"grammar"`
		Value   json.RawMessage `json:"value"`
		Expect  string          `json:"expect"`
	} `json:"read_mode"`
	ReadRule []struct {
		Name   string          `json:"name"`
		Value  json.RawMessage `json:"value"`
		Expect string          `json:"expect"`
	} `json:"read_rule"`
	CertPattern []struct {
		Name    string `json:"name"`
		Pattern string `json:"pattern"`
		Valid   bool   `json:"valid"`
	} `json:"cert_pattern"`
	Bind []struct {
		Name    string          `json:"name"`
		Context json.RawMessage `json:"context"`
		Pinned  json.RawMessage `json:"pinned"`
		Expect  string          `json:"expect"`
	} `json:"bind"`
}

func loadAccess(t testing.TB) accessVectors {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "testdata", "p1", "access.json"))
	must(t, err)
	var v accessVectors
	must(t, json.Unmarshal(b, &v))
	if len(v.ReadMode) == 0 || len(v.ReadRule) == 0 || len(v.CertPattern) == 0 || len(v.Bind) == 0 {
		t.Fatal("a vector group is empty")
	}
	return v
}

func gram(t testing.TB, v int64) forms.Grammar {
	t.Helper()
	n := &forms.Node{Kind: forms.KindInt, Int: v}
	g, err := forms.BindGrammar(n, n)
	must(t, err)
	return g
}

func optionalNode(t *testing.T, raw json.RawMessage) *forms.Node {
	t.Helper()
	if string(raw) == `"missing"` {
		return nil
	}
	return treeOf(t, raw)
}

func TestReadModeVectors(t *testing.T) {
	names := map[forms.Mode]string{forms.ModeInherit: "inherit", forms.ModeNoInherit: "no-inherit", forms.ModeReset: "reset"}
	for _, v := range loadAccess(t).ReadMode {
		node := treeOf(t, v.Value)
		g := gram(t, v.Grammar)
		mode, viol := forms.ReadMode(node, g)
		if viol != nil {
			if viol.Code != v.Expect {
				t.Errorf("%s: got %s, want %s", v.Name, viol.Code, v.Expect)
			}
			continue
		}
		if names[mode] != v.Expect {
			t.Errorf("%s: got %s, want %s", v.Name, names[mode], v.Expect)
		}
		// The round trip keeps the spelling, to the type.
		if !mustWrite(t, mode, g).Same(node) {
			t.Errorf("%s: spelling lost", v.Name)
		}
	}
}

func TestReadRuleVectors(t *testing.T) {
	for _, v := range loadAccess(t).ReadRule {
		rule, viol := forms.ReadRule(treeOf(t, v.Value))
		got := ""
		switch {
		case viol != nil:
			got = viol.Code
		case rule.Symbol:
			got = "symbol"
		default:
			got = "list:" + strings.Join(rule.Patterns, "|")
		}
		if got != v.Expect {
			t.Errorf("%s: got %s, want %s", v.Name, got, v.Expect)
		}
	}
}

func TestCertPatternVectors(t *testing.T) {
	for _, v := range loadAccess(t).CertPattern {
		if got := forms.IsCertPattern(v.Pattern); got != v.Valid {
			t.Errorf("%s: %q: got %v", v.Name, v.Pattern, got)
		}
	}
}

func TestBindVectors(t *testing.T) {
	for _, v := range loadAccess(t).Bind {
		g, err := forms.BindGrammar(optionalNode(t, v.Context), optionalNode(t, v.Pinned))
		var fe *forms.Error
		switch {
		case err == nil:
			if got := string(rune('0' + g.Version())); got != v.Expect {
				t.Errorf("%s: bound %s, want %s", v.Name, got, v.Expect)
			}
		case !errors.As(err, &fe) || fe.Kind != forms.ErrBinding || fe.Code() != v.Expect:
			t.Errorf("%s: got %v, want %s", v.Name, err, v.Expect)
		}
	}
}

func TestViolationsAreAnchoredAtTheValueAndNameThePhase(t *testing.T) {
	n := mustParse(t, "a: [x]\n")
	rule, v := forms.ReadRule(n.Entries[0].Value)
	_ = rule
	if v == nil || v.Code != "INVALID_CERT_PATTERN" || v.Phase != forms.PhaseValidate || v.LogicalPath != "" ||
		v.Location != (forms.Location{Available: true, Pos: forms.Pos{Line: 1, Column: 4, Offset: 3}}) {
		t.Errorf("%+v", v)
	}
	_, v = forms.ReadMode(&forms.Node{Kind: forms.KindInt, Int: 1}, gram(t, 1))
	if v == nil || v.Code != "INVALID_INHERIT" || v.Location.Available {
		t.Errorf("%+v", v)
	}
}

func TestFalseAndZeroAreTwoValuesInEveryDirection(t *testing.T) {
	f, zero := &forms.Node{Kind: forms.KindBool}, &forms.Node{Kind: forms.KindInt}
	if f.Same(zero) {
		t.Error("false equals 0")
	}
	for text, want := range map[string]forms.Mode{"false": forms.ModeNoInherit, "0": forms.ModeReset, "true": forms.ModeInherit} {
		if got, v := forms.ReadMode(mustParse(t, text), gram(t, 1)); v != nil || got != want {
			t.Errorf("%s: %v %v", text, got, v)
		}
	}
	// A float spelling never reaches the reading: the YAML subset refuses it.
	if _, err := forms.Parse("0.0"); kindOf(err) != forms.ErrUnsupported {
		t.Errorf("%v", err)
	}
}

func TestTheEmptyListIsNobodyAndNeverTheSymbol(t *testing.T) {
	rule, v := forms.ReadRule(mustParse(t, "[]"))
	if v != nil || rule.Symbol || rule.Patterns == nil || len(rule.Patterns) != 0 {
		t.Errorf("%+v %v", rule, v)
	}
	if rule, v := forms.ReadRule(mustParse(t, "inherit")); v != nil || !rule.Symbol {
		t.Errorf("%+v %v", rule, v)
	}
}

func TestWriteModeIsABijectionPerVersion(t *testing.T) {
	all := []forms.Mode{forms.ModeInherit, forms.ModeNoInherit, forms.ModeReset}
	for _, version := range forms.SupportedGrammars() {
		g := gram(t, version)
		for i, m := range all {
			w := mustWrite(t, m, g)
			if back, v := forms.ReadMode(w, g); v != nil || back != m {
				t.Errorf("v%d %v: %v %v", version, m, back, v)
			}
			for j, other := range all {
				if w.Same(mustWrite(t, other, g)) != (i == j) {
					t.Errorf("v%d: spellings %d and %d", version, i, j)
				}
			}
		}
	}
	// The two spellings never cross.
	if _, v := forms.ReadMode(mustWrite(t, forms.ModeReset, gram(t, 1)), gram(t, 2)); v == nil {
		t.Error("grammar 1 spelling read as grammar 2")
	}
	if _, v := forms.ReadMode(mustWrite(t, forms.ModeReset, gram(t, 2)), gram(t, 1)); v == nil {
		t.Error("grammar 2 spelling read as grammar 1")
	}
}

func TestTheDigestIsTheSHA256OfTheBytes(t *testing.T) {
	if forms.SHA256Hex([]byte("abc")) != "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" ||
		forms.SHA256Hex(nil) != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" {
		t.Error("sha256")
	}
}

func schemaWith(versionLine string) []byte {
	return []byte("metadata:\n  name: Access\nspec:\n  kind: AtomicPrimitive\n  access_grammar:\n" + versionLine + "    supported: [1, 2]\n")
}

func TestThePinnedVersionComesFromTheVerifiedBytesOnly(t *testing.T) {
	ok := schemaWith("    version: 1\n")
	pinned, err := forms.PinnedGrammarVersion(ok, forms.SHA256Hex(ok))
	if err != nil || pinned == nil || pinned.Kind != forms.KindInt || pinned.Int != 1 {
		t.Fatalf("%v %+v", err, pinned)
	}
	// A changed byte is a different pin, whatever it says.
	if _, err := forms.PinnedGrammarVersion(schemaWith("    version: 2\n"), forms.SHA256Hex(ok)); kindOf(err) != forms.ErrBinding ||
		err.(*forms.Error).Code() != "GRAMMAR_DIGEST_MISMATCH" {
		t.Errorf("%v", err)
	}
	// A verified schema with no version: the caller gets "missing", never a default.
	none := []byte("spec:\n  kind: AtomicPrimitive\n")
	v, err := forms.PinnedGrammarVersion(none, forms.SHA256Hex(none))
	if err != nil || v != nil {
		t.Fatalf("%v %v", err, v)
	}
	if _, err := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, v); err.(*forms.Error).Code() != "GRAMMAR_VERSION_MISSING" {
		t.Errorf("%v", err)
	}
	for _, tree := range []string{"spec: 5\n", "spec: {access_grammar: 5}\n", "spec: [1]\n", "metadata: {}\n"} {
		b := []byte(tree)
		if v, err := forms.PinnedGrammarVersion(b, forms.SHA256Hex(b)); err != nil || v != nil {
			t.Errorf("%q: %v %v", tree, v, err)
		}
	}
	// A version that is not an integer is carried as it is and refused by the binding.
	text := schemaWith("    version: '1'\n")
	v, err = forms.PinnedGrammarVersion(text, forms.SHA256Hex(text))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := forms.BindGrammar(&forms.Node{Kind: forms.KindInt, Int: 1}, v); err.(*forms.Error).Code() != "UNKNOWN_GRAMMAR_VERSION" {
		t.Errorf("%v", err)
	}
	// Text the YAML subset refuses is an error, not a default.
	bad := []byte("spec: &a 1\n")
	if _, err := forms.PinnedGrammarVersion(bad, forms.SHA256Hex(bad)); kindOf(err) != forms.ErrUnsupported {
		t.Errorf("%v", err)
	}
}

func TestBindingErrorsCarryTheirUpstreamCode(t *testing.T) {
	for _, code := range []string{"GRAMMAR_VERSION_MISSING", "UNKNOWN_GRAMMAR_VERSION", "GRAMMAR_VERSION_CONFLICT", "GRAMMAR_DIGEST_MISMATCH"} {
		if e := (&forms.Error{Kind: forms.ErrBinding, BindingCode: code, Detail: "d"}); e.Code() != code || !strings.HasSuffix(e.Error(), ": d") {
			t.Errorf("%v", e)
		}
	}
}

func TestReadingLeavesTheInputAlone(t *testing.T) {
	list := mustParse(t, "[a=b, c=d]")
	before := cloneNode(list)
	_, _ = forms.ReadRule(list)
	_, _ = forms.ReadMode(list, gram(t, 1))
	if !list.Same(before) || *list.Pos != *before.Pos {
		t.Error("the input changed")
	}
}

// A hand-built node can carry stray fields of another kind; the reading goes by Kind and by nothing else.
func TestReadingGoesByKindNotByStrayFields(t *testing.T) {
	g := gram(t, 1)
	stray := []*forms.Node{
		{Kind: forms.KindInt, Int: 5, Bool: true, Str: "inherit"},
		{Kind: forms.KindStr, Str: "x", Bool: true, Int: 0},
		{Kind: forms.KindNull, Bool: true, Int: 0, Str: "inherit"},
	}
	for _, n := range stray {
		if mode, v := forms.ReadMode(n, g); v == nil || mode != forms.ModeInvalid || v.Code != "INVALID_INHERIT" {
			t.Errorf("%+v read as a mode: %v %+v", n, mode, v)
		}
		if _, v := forms.ReadRule(n); v == nil || v.Code != "INVALID_ACCESS_VALUE" {
			t.Errorf("%+v read as a rule: %+v", n, v)
		}
	}
	list := &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{{Kind: forms.KindInt, Str: "a=b"}}}
	if _, v := forms.ReadRule(list); v == nil || v.Code != "INVALID_CERT_PATTERN" {
		t.Errorf("a non-string item with a pattern in Str: %+v", v)
	}
	for _, version := range []*forms.Node{
		{Kind: forms.KindStr, Int: 1}, {Kind: forms.KindBool, Int: 1, Bool: true}, {Kind: forms.KindSeq, Int: 2},
	} {
		if _, err := forms.BindGrammar(version, &forms.Node{Kind: forms.KindInt, Int: 1}); err == nil || err.(*forms.Error).Code() != "UNKNOWN_GRAMMAR_VERSION" {
			t.Errorf("%+v bound: %v", version, err)
		}
	}
}

func TestAViolationCarriesNoMode(t *testing.T) {
	for _, version := range forms.SupportedGrammars() {
		for _, n := range []*forms.Node{{Kind: forms.KindNull}, {Kind: forms.KindInt, Int: 1}, {Kind: forms.KindStr, Str: "reset"}, {Kind: forms.KindStr, Str: "x"}, {Kind: forms.KindBool}} {
			g := gram(t, version)
			mode, v := forms.ReadMode(n, g)
			if v != nil && mode != forms.ModeInvalid {
				t.Errorf("grammar %d, %+v: mode %d with a violation", version, n, mode)
			}
			if v == nil && (mode < forms.ModeInherit || mode > forms.ModeReset) {
				t.Errorf("grammar %d, %+v: %d is not a state", version, n, mode)
			}
		}
	}
}

func mustWrite(t interface{ Fatal(args ...any) }, m forms.Mode, g forms.Grammar) *forms.Node {
	n, err := forms.WriteMode(m, g)
	if err != nil {
		t.Fatal(err)
	}
	return n
}

func TestOnlyTheThreeStatesCanBeWritten(t *testing.T) {
	for _, version := range forms.SupportedGrammars() {
		g := gram(t, version)
		for _, m := range []forms.Mode{forms.ModeInvalid, forms.Mode(3), forms.Mode(-2), forms.Mode(100)} {
			n, err := forms.WriteMode(m, g)
			var fe *forms.Error
			if n != nil || !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
				t.Errorf("grammar %d, mode %d: %+v %v", version, m, n, err)
			}
		}
		for _, m := range []forms.Mode{forms.ModeInherit, forms.ModeNoInherit, forms.ModeReset} {
			if n, err := forms.WriteMode(m, g); n == nil || err != nil {
				t.Errorf("grammar %d, mode %d: %v", version, m, err)
			}
		}
	}
}

func TestAnUnboundGrammarReadsNothingAndWritesNothing(t *testing.T) {
	var zero forms.Grammar
	values := []*forms.Node{
		{Kind: forms.KindBool, Bool: true}, {Kind: forms.KindBool}, {Kind: forms.KindInt},
		{Kind: forms.KindStr, Str: "inherit"}, {Kind: forms.KindStr, Str: "reset"}, {Kind: forms.KindNull},
	}
	for _, n := range values {
		mode, v := forms.ReadMode(n, zero)
		if v == nil || v.Code != "UNKNOWN_GRAMMAR_VERSION" || mode != forms.ModeInvalid {
			t.Errorf("%+v read under Grammar{}: %v %+v", n, mode, v)
		}
	}
	for _, m := range []forms.Mode{forms.ModeInherit, forms.ModeNoInherit, forms.ModeReset} {
		n, err := forms.WriteMode(m, zero)
		var fe *forms.Error
		if n != nil || !errors.As(err, &fe) || fe.Kind != forms.ErrBinding || fe.Code() != "UNKNOWN_GRAMMAR_VERSION" {
			t.Errorf("mode %d written under Grammar{}: %+v %v", m, n, err)
		}
	}
}

func TestTheSupportedVersionsCannotBeChangedFromOutside(t *testing.T) {
	got := forms.SupportedGrammars()
	if len(got) != 2 || got[0] != 1 || got[1] != 2 {
		t.Fatalf("%v", got)
	}
	got[0], got[1] = 7, 8
	got = append(got, 9)
	again := forms.SupportedGrammars()
	if len(again) != 2 || again[0] != 1 || again[1] != 2 {
		t.Errorf("a caller changed the supported versions: %v", again)
	}
	n := &forms.Node{Kind: forms.KindInt, Int: 7}
	if _, err := forms.BindGrammar(n, n); err == nil || err.(*forms.Error).Code() != "UNKNOWN_GRAMMAR_VERSION" {
		t.Errorf("7 bound: %v", err)
	}
}
