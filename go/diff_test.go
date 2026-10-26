package forms_test

// Diff (ADR 0008) at the places a vector of corpus/diff cannot reach: its trees have no nullable composite, no integer or boolean set, no
// integer key, and the operands of a case are never invalid in a way the vectors do not name. The Rust peer is rust/tests/diff.rs.

import (
	"errors"
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func diffScalar(t forms.ScalarType) forms.Shape {
	return forms.Shape{Kind: forms.ShapeScalar, Scalar: t, Presence: forms.Optional}
}

func diffFields(names ...any) []forms.NamedShape {
	var out []forms.NamedShape
	for i := 0; i < len(names); i += 2 {
		out = append(out, forms.NamedShape{Name: names[i].(string), Shape: names[i+1].(forms.Shape)})
	}
	return out
}

func diffList(choice bool, cases ...forms.Case) forms.Shape {
	return forms.Shape{Kind: forms.ShapeDeclaredList, Presence: forms.Optional, Elements: &forms.Elements{Choice: choice, Cases: cases}}
}

// diffRoot has every shape a diff walks: scalars of the three types, a composite, sets of strings, integers and booleans, an opaque value, the
// three kinds of list (by a string key, by an integer key, by an item_choice), and a nullable composite, list and set.
func diffRoot() forms.Shape {
	str, num, flag := diffScalar(forms.ScalarString), diffScalar(forms.ScalarInteger), diffScalar(forms.ScalarBoolean)
	set := func(t forms.ScalarType, nullable bool) forms.Shape {
		return forms.Shape{Kind: forms.ShapeSet, Scalar: t, Presence: forms.Optional, Nullable: nullable}
	}
	nics := diffList(false, forms.Case{Name: "nic", Key: []string{"name", "zone"}, Fields: diffFields("name", str, "zone", str, "subnet", str)})
	nics.Nullable = true
	return forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: diffFields(
		"mtu", num, "name", str, "on", flag,
		"note", forms.Shape{Kind: forms.ShapeOpaque, Presence: forms.Optional, Nullable: true},
		"ip", forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: diffFields("gw", str, "addrs", set(forms.ScalarString, false))},
		"opt", forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Nullable: true, Fields: diffFields("x", str)},
		"ids", set(forms.ScalarInteger, false), "flags", set(forms.ScalarBoolean, false), "oset", set(forms.ScalarString, true),
		"nics", nics,
		"ports", diffList(false, forms.Case{Name: "port", Key: []string{"port"}, Fields: diffFields("port", num, "label", str)}),
		"vlans", diffList(true,
			forms.Case{Name: "referenced", Key: []string{"ref"}, Fields: diffFields("ref", str)},
			forms.Case{Name: "inline", Key: []string{"vlan_id"}, Fields: diffFields("vlan_id", num, "mode", str)}),
		"plain", forms.Shape{Kind: forms.ShapeList, Presence: forms.Optional},
	)}
}

func diffParse(t *testing.T, src string) *forms.Node {
	t.Helper()
	n, err := forms.Parse(src)
	if err != nil {
		t.Fatalf("%q: %v", src, err)
	}
	return n
}

func diffView(doc *forms.Node) forms.DiffView { return forms.DiffView{Doc: doc} }

func runDiff(t *testing.T, a, b *forms.Node, scope []string) (*forms.DiffResult, error) {
	t.Helper()
	return forms.Diff(diffView(a), diffView(b), diffRoot(), accessKeys, boundGrammar(t), scope)
}

// diffCanonical is the bytes of the diff of two documents written as YAML.
func diffCanonical(t *testing.T, a, b string, scope []string) string {
	t.Helper()
	r, err := runDiff(t, diffParse(t, a), diffParse(t, b), scope)
	if err != nil {
		t.Fatalf("%q against %q: %v", a, b, err)
	}
	return r.Canonical()
}

// diffRefusal is the wire form and the operand of the one error of a diff.
func diffRefusal(t *testing.T, a, b *forms.Node, scope []string) (wire, string) {
	t.Helper()
	_, err := runDiff(t, a, b, scope)
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrContract {
		t.Fatalf("expected a contract error, got %v", err)
	}
	if len(fe.Violations) != 1 {
		t.Fatalf("one error per response, got %d", len(fe.Violations))
	}
	return wireOf(fe.Violations[0]), fe.Violations[0].Operand
}

const diffDflt = `"access":"inherit","conformance":"implemented","default_injection":null,"inherit":true,"modify":"inherit"`

func TestDiffAnUnsupportedNumberIsNamedWhereverItIs(t *testing.T) {
	flt := func(s string) *forms.Node { return &forms.Node{Kind: forms.KindUnsupported, Str: s} }
	str := func(s string) *forms.Node { return &forms.Node{Kind: forms.KindStr, Str: s} }
	entries := func(kv ...any) *forms.Node {
		n := &forms.Node{Kind: forms.KindMap}
		for i := 0; i < len(kv); i += 2 {
			n.Entries = append(n.Entries, forms.Entry{Key: kv[i].(string), Value: kv[i+1].(*forms.Node)})
		}
		return n
	}
	wrapper := func(value *forms.Node, extra ...any) *forms.Node {
		members := []any{"value", value, "access", str("inherit"), "modify", str("inherit"), "inherit", &forms.Node{Kind: forms.KindBool, Bool: true},
			"default_injection", &forms.Node{Kind: forms.KindNull}, "conformance", str("implemented")}
		for i := 0; i < len(extra); i += 2 {
			for j := 0; j < len(members); j += 2 {
				if members[j] == extra[i] {
					members[j+1] = extra[i+1]
				}
			}
		}
		return entries(members...)
	}
	seq := func(items ...*forms.Node) *forms.Node { return &forms.Node{Kind: forms.KindSeq, Items: items} }
	cases := []struct {
		name string
		doc  *forms.Node
		path string
	}{
		{"inside an opaque value", entries("note", seq(flt("1.5"))), "$.note"},
		{"as a value", entries("mtu", flt("9223372036854775808")), "$.mtu"},
		{"in the value of an expanded position", entries("note", wrapper(flt("2.5"))), "$.note"},
		{"in a declaration member of an expanded position", entries("mtu", wrapper(&forms.Node{Kind: forms.KindInt, Int: 1}, "access", flt("1e3"))), "$.mtu"},
		{"in a set", entries("ip", entries("addrs", seq(flt("1.5")))), "$.ip.addrs"},
		{"below a composite", entries("ip", entries("gw", flt("1.5"))), "$.ip.gw"},
		{"in a field of a list element", entries("nics", seq(entries("name", str("n"), "zone", str("z"), "subnet", flt("1.5")))), `$.nics[{name="n",zone="z"}].subnet`},
		{"in a list element that has no identity", entries("nics", seq(entries("name", str("n"), "subnet", flt("1.5")))), "$.nics"},
		{"under a key the schema does not declare", entries("bogus", flt("1.5")), ""},
		{"in the first of two fields, by name", entries("name", flt("2.5"), "mtu", flt("1.5")), "$.mtu"},
		{"after a clean field", entries("mtu", &forms.Node{Kind: forms.KindInt, Int: 1}, "name", flt("2.5")), "$.name"},
	}
	clean := diffParse(t, "mtu: 1\n")
	for _, c := range cases {
		w, operand := diffRefusal(t, c.doc, clean, nil)
		if want := (wire{code: "DIFF_UNSUPPORTED_VALUE", phase: "diff", loc: "not_applicable", path: c.path}); w != want || operand != "a" {
			t.Errorf("%s (a): got %v %q, want %v \"a\"", c.name, w, operand, want)
		}
		w, operand = diffRefusal(t, clean, c.doc, nil)
		if w.code != "DIFF_UNSUPPORTED_VALUE" || w.path != c.path || operand != "b" {
			t.Errorf("%s (b): got %v %q", c.name, w, operand)
		}
	}
	// both operands: A is named, and the diffRefusal does not depend on what the other holds
	w, operand := diffRefusal(t, entries("mtu", flt("1.5")), entries("name", flt("2.5")), nil)
	if w.path != "$.mtu" || operand != "a" {
		t.Errorf("both: got %v %q", w, operand)
	}
}

func TestDiffRefusesBeforeItReadsAndInTheOrderOfTheContract(t *testing.T) {
	flt := &forms.Node{Kind: forms.KindUnsupported, Str: "1.5"}
	bad := diffParse(t, "bogus: 1\n")
	ok := diffParse(t, "mtu: 1\n")
	badScope := []string{"$.nope"}
	// the contexts first, then the scope, then an unsupported number (A before B), then the views (A before B)
	if _, err := forms.Diff(forms.DiffView{Doc: bad, Context: forms.DiffContext{Schema: "x"}}, diffView(bad), diffRoot(), accessKeys, boundGrammar(t), badScope); err.(*forms.Error).Code() != "DIFF_CONTEXT_MISMATCH" {
		t.Errorf("contexts first: %v", err)
	}
	if w, _ := diffRefusal(t, bad, bad, badScope); w.code != "UNKNOWN_FIELD" || w.phase != "path_resolve" {
		t.Errorf("the scope before the views: %v", w)
	}
	floatDoc := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "mtu", Value: flt}}}
	if w, operand := diffRefusal(t, bad, floatDoc, nil); w.code != "DIFF_UNSUPPORTED_VALUE" || operand != "b" {
		t.Errorf("an unsupported number before the views: %v %q", w, operand)
	}
	if w, operand := diffRefusal(t, bad, bad, nil); w.code != "UNKNOWN_FIELD" || w.phase != "validate" || operand != "a" || w.path != "$.bogus" {
		t.Errorf("A before B: %v %q", w, operand)
	}
	if w, operand := diffRefusal(t, ok, bad, nil); w.code != "UNKNOWN_FIELD" || operand != "b" {
		t.Errorf("B: %v %q", w, operand)
	}
}

func TestDiffTheContextsAreCompared(t *testing.T) {
	doc := diffParse(t, "mtu: 1\n")
	base := forms.DiffContext{Schema: "s", SchemaDigest: "sd", Grammar: "g", GrammarDigest: "gd", Surface: "x"}
	other := []forms.DiffContext{
		{Schema: "t", SchemaDigest: "sd", Grammar: "g", GrammarDigest: "gd", Surface: "x"},
		{Schema: "s", SchemaDigest: "td", Grammar: "g", GrammarDigest: "gd", Surface: "x"},
		{Schema: "s", SchemaDigest: "sd", Grammar: "h", GrammarDigest: "gd", Surface: "x"},
		{Schema: "s", SchemaDigest: "sd", Grammar: "g", GrammarDigest: "hd", Surface: "x"},
		{Schema: "s", SchemaDigest: "sd", Grammar: "g", GrammarDigest: "gd", Surface: "y"},
	}
	for i, o := range other {
		_, err := forms.Diff(forms.DiffView{Doc: doc, Context: base}, forms.DiffView{Doc: doc, Context: o}, diffRoot(), accessKeys, boundGrammar(t), nil)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Code() != "DIFF_CONTEXT_MISMATCH" || fe.Violations[0].Operand != "" || fe.Violations[0].LogicalPath != "" || fe.Violations[0].Phase != forms.PhaseDiff {
			t.Errorf("context %d: %v", i, err)
		}
	}
	if _, err := forms.Diff(forms.DiffView{Doc: doc, Context: base}, forms.DiffView{Doc: doc, Context: base}, diffRoot(), accessKeys, boundGrammar(t), nil); err != nil {
		t.Errorf("equal contexts: %v", err)
	}
}

func TestDiffTheViewsAreReadAsTheirOwnPhases(t *testing.T) {
	good := diffParse(t, "mtu: 1\n")
	w, operand := diffRefusal(t, diffParse(t, "mtu: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: 1, extra: 2}\n"), good, nil)
	if w.code != "EXPANDED_WITH_EXTRA_KEY" || w.phase != "recognize" || operand != "a" {
		t.Errorf("recognize: %v %q", w, operand)
	}
	w, operand = diffRefusal(t, good, diffParse(t, "nics:\n  - {name: a, zone: b}\n  - {name: a, zone: b}\n"), nil)
	if w.code != "PATH_DUPLICATE_IDENTITY" || w.phase != "path_of" || operand != "b" || w.path != `$.nics[{name="a",zone="b"}]` {
		t.Errorf("path_of: %v %q", w, operand)
	}
	w, operand = diffRefusal(t, diffParse(t, "nics:\n  - {name: a}\n"), good, nil)
	if w.code != "PATH_KEY_MISSING" || w.phase != "path_of" || operand != "a" {
		t.Errorf("a missing key: %v %q", w, operand)
	}
	// the trusted context is checked when a view is expanded: a materialized document without its attestation
	trust := forms.Context{Origin: forms.OriginMaterialized}
	_, err := forms.Diff(forms.DiffView{Doc: good, Trust: trust}, diffView(good), diffRoot(), accessKeys, boundGrammar(t), nil)
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Code() != forms.CodeOriginAttestationMismatch || fe.Violations[0].Operand != "a" {
		t.Errorf("trust of A: %v", err)
	}
	_, err = forms.Diff(diffView(good), forms.DiffView{Doc: good, Trust: trust}, diffRoot(), accessKeys, boundGrammar(t), nil)
	if !errors.As(err, &fe) || fe.Code() != forms.CodeOriginAttestationMismatch || fe.Violations[0].Operand != "b" {
		t.Errorf("trust of B: %v", err)
	}
	// a structure outside the subset is not a contract error
	_, err = runDiff(t, diffParse(t, "ip: [x]\n"), good, nil)
	if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
		t.Errorf("outside the subset: %v", err)
	}
}

func TestDiffTheScope(t *testing.T) {
	a := "mtu: 1\nname: x\nip: {gw: g1}\nnics:\n  - {name: n1, zone: z, subnet: a}\n"
	b := "mtu: 2\nname: y\nip: {gw: g2}\nnics:\n  - {name: n1, zone: z, subnet: b}\n  - {name: n2, zone: z}\n"
	const empty = `{"changes":[],"representation":[],"version":1}`
	if got := diffCanonical(t, a, b, []string{}); got != empty {
		t.Errorf("an empty scope compares nothing: %s", got)
	}
	whole := diffCanonical(t, a, b, nil)
	if got := diffCanonical(t, a, b, []string{"$"}); got != whole {
		t.Errorf("the root as the scope is the whole document:\n%s\n%s", got, whole)
	}
	// repeated and nested entries add nothing, whatever their order
	one := diffCanonical(t, a, b, []string{"$.ip.gw"})
	for _, scope := range [][]string{{"$.ip.gw", "$.ip.gw"}, {"$.ip", "$.ip.gw"}, {"$.ip.gw", "$.ip"}} {
		if got := diffCanonical(t, a, b, scope); got != diffCanonical(t, a, b, []string{"$.ip"}) && got != one {
			t.Errorf("%v: %s", scope, got)
		}
	}
	if got, want := diffCanonical(t, a, b, []string{"$.ip", "$.ip.gw"}), diffCanonical(t, a, b, []string{"$.ip"}); got != want {
		t.Errorf("a nested entry adds nothing:\n%s\n%s", got, want)
	}
	// a list element that only B has appears as one event at its path; one that only A has disappears
	got := diffCanonical(t, a, b, []string{`$.nics[{name="n2",zone="z"}]`})
	want := `{"changes":[{"kind":"added","next":{"name":{` + diffDflt + `,"value":"n2"},"zone":{` + diffDflt + `,"value":"z"}},"path":"$.nics[{name=\"n2\",zone=\"z\"}]"}],"representation":[],"version":1}`
	if got != want {
		t.Errorf("an added element:\n%s\n%s", got, want)
	}
	if got := diffCanonical(t, b, a, []string{`$.nics[{name="n2",zone="z"}]`}); got == empty || got == want {
		t.Errorf("a removed element: %s", got)
	}
	// an element neither side has, and an element of an empty list
	if got := diffCanonical(t, a, b, []string{`$.nics[{name="zz",zone="z"}]`}); got != empty {
		t.Errorf("an element neither has: %s", got)
	}
	// the scope is a path of the schema
	for _, bad := range []struct{ path, code, phase string }{{"$.", "PATH_EMPTY_NAME", "path_parse"}, {"$.ip.gw.x", "PATH_NO_CHILDREN", "path_resolve"}, {"$.addrs", "UNKNOWN_FIELD", "path_resolve"}} {
		doc := diffParse(t, "mtu: 1\n")
		if w, operand := diffRefusal(t, doc, doc, []string{bad.path}); w.code != bad.code || w.phase != bad.phase || operand != "" {
			t.Errorf("%s: %v %q", bad.path, w, operand)
		}
	}
	// a failure in the second entry is reported though the first is fine
	doc := diffParse(t, "mtu: 1\n")
	if w, _ := diffRefusal(t, doc, doc, []string{"$.mtu", "$.nope"}); w.code != "UNKNOWN_FIELD" {
		t.Errorf("the second entry: %v", w)
	}
}

func TestDiffAPlaceBelowAValueThatCannotHoldItIsRefused(t *testing.T) {
	cases := []struct{ a, scope string }{
		{"opt: null\n", "$.opt.x"},
		{"nics: null\n", `$.nics[{name="n",zone="z"}].subnet`},
	}
	for _, c := range cases {
		doc := diffParse(t, c.a)
		_, err := runDiff(t, doc, doc, []string{c.scope})
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Errorf("%s: %v", c.scope, err)
		}
	}
}

func TestDiffNullAgainstACollection(t *testing.T) {
	none := `{"changes":[],"representation":[],"version":1}`
	for _, src := range []string{"opt: null\n", "nics: null\n", "oset: null\n"} {
		if got := diffCanonical(t, src, src, nil); got != none {
			t.Errorf("%s null on both sides: %s", src, got)
		}
	}
	for _, c := range []struct{ a, b string }{
		{"opt: null\n", "opt: {x: a}\n"}, {"opt: {x: a}\n", "opt: null\n"},
		{"nics: null\n", "nics: []\n"}, {"nics: []\n", "nics: null\n"},
		{"oset: null\n", "oset: [a]\n"}, {"oset: [a]\n", "oset: null\n"},
	} {
		_, err := runDiff(t, diffParse(t, c.a), diffParse(t, c.b), nil)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Errorf("%q against %q: %v", c.a, c.b, err)
		}
	}
	// a list that declares no elements is not compared
	_, err := runDiff(t, diffParse(t, "plain: [a]\n"), diffParse(t, "plain: [b]\n"), nil)
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
		t.Errorf("a list without elements: %v", err)
	}
}

func TestDiffSets(t *testing.T) {
	cases := []struct{ a, b, want string }{
		{"ids: [3, 1, 2]\n", "ids: [2, 5, -4]\n", `{"added":[-4,5],"kind":"set_change","path":"$.ids","removed":[1,3]}`},
		{"ids: [10, 9]\n", "ids: []\n", `{"added":[],"kind":"set_change","path":"$.ids","removed":[9,10]}`},
		{"flags: [true]\n", "flags: [false]\n", `{"added":[false],"kind":"set_change","path":"$.flags","removed":[true]}`},
		{"flags: [true, false]\n", "flags: [false, true]\n", ``},
		{"ip: {addrs: [a, b, a]}\n", "ip: {addrs: [b, a]}\n", ``},
		{"ip: {addrs: [b, a]}\n", "ip: {addrs: [a, a, c, c]}\n", `{"added":["c"],"kind":"set_change","path":"$.ip.addrs","removed":["b"]}`},
	}
	for _, c := range cases {
		want := `{"changes":[],"representation":[],"version":1}`
		if c.want != "" {
			want = `{"changes":[` + c.want + `],"representation":[],"version":1}`
		}
		if got := diffCanonical(t, c.a, c.b, nil); got != want {
			t.Errorf("%q against %q:\n got %s\nwant %s", c.a, c.b, got, want)
		}
	}
	// an appearing set is sorted with its duplicates dropped
	got := diffCanonical(t, "mtu: 1\n", "mtu: 1\nids: [3, 1, 3, 2]\n", nil)
	if want := `{"changes":[{"kind":"added","next":{` + diffDflt + `,"value":[1,2,3]},"path":"$.ids"}],"representation":[],"version":1}`; got != want {
		t.Errorf("an appearing set:\n got %s\nwant %s", got, want)
	}
}

func TestDiffAFragmentKeepsEveryDeclarationAndOrdersWhatItHolds(t *testing.T) {
	b := `ports:
  - {port: 10, label: ten}
  - {port: 9, label: nine}
  - port: {access: [OU=ops], modify: inherit, inherit: true, default_injection: 0, conformance: deprecated, value: 2}
vlans:
  - {vlan_id: 7, mode: m}
  - {ref: x}
  - {ref: a}
`
	got := diffCanonical(t, "mtu: 1\n", "mtu: 1\n"+b, nil)
	str := func(v string) string { return `{` + diffDflt + `,"value":"` + v + `"}` }
	num := func(v string) string { return `{` + diffDflt + `,"value":` + v + `}` }
	ports := `[{"port":{"access":["OU=ops"],"conformance":"deprecated","default_injection":0,"inherit":true,"modify":"inherit","value":2}},` +
		`{"label":` + str("nine") + `,"port":` + num("9") + `},{"label":` + str("ten") + `,"port":` + num("10") + `}]`
	vlans := `[{"ref":` + str("a") + `},{"ref":` + str("x") + `},{"mode":` + str("m") + `,"vlan_id":` + num("7") + `}]`
	want := `{"changes":[` +
		`{"kind":"added","next":{` + diffDflt + `,"value":` + ports + `},"path":"$.ports"},` +
		`{"kind":"added","next":{` + diffDflt + `,"value":` + vlans + `},"path":"$.vlans"}],"representation":[],"version":1}`
	if got != want {
		t.Errorf("fragments:\n got %s\nwant %s", got, want)
	}
	// the same list disappearing is the mirror
	back := diffCanonical(t, "mtu: 1\n"+b, "mtu: 1\n", nil)
	mirror := `{"changes":[` +
		`{"kind":"removed","path":"$.ports","previous":{` + diffDflt + `,"value":` + ports + `}},` +
		`{"kind":"removed","path":"$.vlans","previous":{` + diffDflt + `,"value":` + vlans + `}}],"representation":[],"version":1}`
	if back != mirror {
		t.Errorf("removed fragments:\n got %s\nwant %s", back, mirror)
	}
}

func TestDiffEventsAreInPathOrder(t *testing.T) {
	a := "name: a\nmtu: 1\nports:\n  - {port: 2, label: x}\n  - {port: 10, label: x}\nnics:\n  - {name: b, zone: z, subnet: x}\n  - {name: a, zone: z, subnet: x}\n"
	b := "name: b\nmtu: 2\nports:\n  - {port: 2, label: y}\n  - {port: 10, label: y}\nnics:\n  - {name: b, zone: z, subnet: y}\n  - {name: a, zone: z, subnet: y}\n"
	r, err := runDiff(t, diffParse(t, a), diffParse(t, b), nil)
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	for _, e := range r.Changes {
		paths = append(paths, e.Path)
	}
	want := []string{"$.mtu", "$.name", `$.nics[{name="a",zone="z"}].subnet`, `$.nics[{name="b",zone="z"}].subnet`, "$.ports[{port=2}].label", "$.ports[{port=10}].label"}
	if !reflect.DeepEqual(paths, want) {
		t.Errorf("order: %v", paths)
	}
}

func TestDiffRepresentationBelowAListElementAndOnlyWherePresentInBoth(t *testing.T) {
	short := "nics:\n  - {name: n, zone: z, subnet: s}\nmtu: 1\n"
	expanded := "nics:\n  - name: n\n    zone: z\n    subnet: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: s}\nname: {access: inherit, modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: x}\n"
	got := diffCanonical(t, short, expanded, nil)
	want := `{"changes":[{"kind":"removed","path":"$.mtu","previous":{` + diffDflt + `,"value":1}},{"kind":"added","next":{` + diffDflt + `,"value":"x"},"path":"$.name"}],` +
		`"representation":[{"kind":"representation_change","next":"expanded","path":"$.nics[{name=\"n\",zone=\"z\"}].subnet","previous":"short"}],"version":1}`
	if got != want {
		t.Errorf("representation:\n got %s\nwant %s", got, want)
	}
	// outside the scope a writing difference is not reported either
	if got := diffCanonical(t, short, expanded, []string{"$.mtu"}); got != `{"changes":[{"kind":"removed","path":"$.mtu","previous":{`+diffDflt+`,"value":1}}],"representation":[],"version":1}` {
		t.Errorf("scope and representation: %s", got)
	}
}

func TestDiffTheRoundTripOfAnInversion(t *testing.T) {
	docs := []string{
		"mtu: 1\nname: a\nip: {gw: g, addrs: [a, b]}\nids: [1, 2]\nports:\n  - {port: 1, label: x}\n",
		"mtu: 2\nname: a\nip: {gw: h, addrs: [b, c]}\nids: [2, 3]\nports:\n  - {port: 1, label: y}\n  - {port: 2, label: z}\n",
		"mtu: {access: [OU=ops], modify: inherit, inherit: true, default_injection: null, conformance: implemented, value: 2}\nnote: {x: [1]}\n",
		"note: null\nvlans:\n  - {ref: a}\n",
		"{}\n",
	}
	mirror := map[string]string{"added": "removed", "removed": "added", "value_change": "value_change", "set_change": "set_change", "policy_declaration_change": "policy_declaration_change"}
	for i, x := range docs {
		for j, y := range docs {
			ab, err := runDiff(t, diffParse(t, x), diffParse(t, y), nil)
			if err != nil {
				t.Fatal(err)
			}
			ba, err := runDiff(t, diffParse(t, y), diffParse(t, x), nil)
			if err != nil {
				t.Fatal(err)
			}
			if i == j && (len(ab.Changes) != 0 || len(ab.Representation) != 0) {
				t.Errorf("a document against itself: %s", ab.Canonical())
			}
			if len(ab.Changes) != len(ba.Changes) || len(ab.Representation) != len(ba.Representation) {
				t.Fatalf("%d against %d: %d and %d events", i, j, len(ab.Changes), len(ba.Changes))
			}
			for k, e := range ab.Changes {
				m := ba.Changes[k]
				if m.Path != e.Path || m.Kind != mirror[e.Kind] {
					t.Errorf("%d/%d event %d: %s %s against %s %s", i, j, k, e.Kind, e.Path, m.Kind, m.Path)
				}
			}
		}
	}
}

func TestDiffTheCanonicalStrings(t *testing.T) {
	cases := map[string]string{
		"plain":         `"plain"`,
		"a\"b":          `"a\"b"`,
		"a\\b":          `"a\\b"`,
		"\b\f\n\r\t":    `"\b\f\n\r\t"`,
		"\x00\x01\x1f":  `"\u0000\u0001\u001f"`,
		"\x7f é \u2028": "\"\x7f é \u2028\"",
		"/":             `"/"`,
	}
	for s, want := range cases {
		doc := &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "name", Value: &forms.Node{Kind: forms.KindStr, Str: s}}}}
		r, err := runDiff(t, &forms.Node{Kind: forms.KindMap}, doc, nil)
		if err != nil {
			t.Fatal(err)
		}
		if got, w := r.Canonical(), `{"changes":[{"kind":"added","next":{`+diffDflt+`,"value":`+want+`},"path":"$.name"}],"representation":[],"version":1}`; got != w {
			t.Errorf("%q:\n got %s\nwant %s", s, got, w)
		}
	}
}

func TestDiffAFieldNameThatCannotBeWrittenIsRefused(t *testing.T) {
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: diffFields("a\nb", diffScalar(forms.ScalarString))}
	doc := func(v string) *forms.Node {
		return &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "a\nb", Value: &forms.Node{Kind: forms.KindStr, Str: v}}}}
	}
	empty := &forms.Node{Kind: forms.KindMap}
	for _, c := range []struct{ a, b *forms.Node }{{doc("x"), doc("y")}, {empty, doc("y")}, {doc("x"), empty}} {
		_, err := forms.Diff(diffView(c.a), diffView(c.b), root, accessKeys, boundGrammar(t), nil)
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Errorf("a position whose path cannot be written: %v", err)
		}
	}
}

func TestDiffAScopeBelowANullInOneOperandOnly(t *testing.T) {
	null, ok := diffParse(t, "opt: null\n"), diffParse(t, "opt: {x: a}\n")
	for name, pair := range map[string][2]*forms.Node{"a": {ok, null}, "b": {null, ok}} {
		_, err := runDiff(t, pair[0], pair[1], []string{"$.opt.x"})
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Kind != forms.ErrUnsupported {
			t.Errorf("null in %s: %v", name, err)
		}
	}
}

func TestDiffANullCollectionThatAppearsKeepsItsNull(t *testing.T) {
	for _, field := range []string{"opt", "oset", "nics", "note"} {
		got := diffCanonical(t, "mtu: 1\n", "mtu: 1\n"+field+": null\n", nil)
		want := `{"changes":[{"kind":"added","next":{` + diffDflt + `,"value":null},"path":"$.` + field + `"}],"representation":[],"version":1}`
		if got != want {
			t.Errorf("%s:\n got %s\nwant %s", field, got, want)
		}
	}
}

func TestDiffEveryRemovedElementIsReportedAndTheRestIsStillCompared(t *testing.T) {
	a := "ports:\n  - {port: 1, label: x}\n  - {port: 2, label: x}\n  - {port: 3, label: x}\n  - {port: 4, label: x}\n"
	b := "ports:\n  - {port: 4, label: y}\n  - {port: 5, label: x}\n  - {port: 6, label: x}\n"
	r, err := runDiff(t, diffParse(t, a), diffParse(t, b), nil)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, e := range r.Changes {
		got = append(got, e.Kind+" "+e.Path)
	}
	want := []string{"removed $.ports[{port=1}]", "removed $.ports[{port=2}]", "removed $.ports[{port=3}]", "value_change $.ports[{port=4}].label", "added $.ports[{port=5}]", "added $.ports[{port=6}]"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %v", got)
	}
}

func TestDiffASetThatOnlyLosesOrOnlyGainsOneElement(t *testing.T) {
	for _, c := range []struct{ a, b, event string }{
		{"ip: {addrs: [a, b]}\n", "ip: {addrs: [a]}\n", `{"added":[],"kind":"set_change","path":"$.ip.addrs","removed":["b"]}`},
		{"ip: {addrs: [a]}\n", "ip: {addrs: [a, b]}\n", `{"added":["b"],"kind":"set_change","path":"$.ip.addrs","removed":[]}`},
	} {
		if got, want := diffCanonical(t, c.a, c.b, nil), `{"changes":[`+c.event+`],"representation":[],"version":1}`; got != want {
			t.Errorf("got %s", got)
		}
	}
}

func TestDiffTheScopeKeepsEveryEntryThatIsNotInsideAnother(t *testing.T) {
	a, b := "mtu: 1\nname: x\nip: {gw: g1}\n", "mtu: 2\nname: y\nip: {gw: g2}\n"
	all := diffCanonical(t, a, b, nil)
	for _, scope := range [][]string{{"$.mtu", "$.name", "$.ip.gw"}, {"$.ip.gw", "$.name", "$.mtu"}, {"$.mtu", "$.ip", "$.name"}} {
		if got := diffCanonical(t, a, b, scope); got != all {
			t.Errorf("%v:\n got %s\nwant %s", scope, got, all)
		}
	}
	if got := diffCanonical(t, a, b, []string{"$.mtu", "$.ip.gw"}); got == all || got == diffCanonical(t, a, b, []string{"$.mtu"}) || got == diffCanonical(t, a, b, []string{"$.ip.gw"}) {
		t.Errorf("two entries, neither inside the other: %s", got)
	}
}

func TestDiffAnUnsupportedNumberAfterEntriesThatAreClean(t *testing.T) {
	flt := &forms.Node{Kind: forms.KindUnsupported, Str: "1.5"}
	str := &forms.Node{Kind: forms.KindStr, Str: "n"}
	clean := diffParse(t, "mtu: 1\n")
	cases := []struct {
		name string
		doc  *forms.Node
		path string
	}{
		{"as the value of a list position", &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "nics", Value: flt}}}, "$.nics"},
		{"in a later element after one without an identity", &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "nics", Value: &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{
			{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "name", Value: str}}},
			{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "name", Value: str}, {Key: "zone", Value: str}, {Key: "subnet", Value: flt}}},
		}}}}}, `$.nics[{name="n",zone="n"}].subnet`},
		{"after an undeclared key that is clean", &forms.Node{Kind: forms.KindMap, Entries: []forms.Entry{{Key: "bogus", Value: str}, {Key: "mtu", Value: flt}}}, "$.mtu"},
	}
	for _, c := range cases {
		if w, operand := diffRefusal(t, c.doc, clean, nil); w.code != "DIFF_UNSUPPORTED_VALUE" || w.path != c.path || operand != "a" {
			t.Errorf("%s: %v %q", c.name, w, operand)
		}
	}
}
