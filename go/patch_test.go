package forms_test

// Patch (ADR 0013) at the places a vector of corpus/patch cannot reach: its trees have no integer key, no item_choice, no nullable list; and the order of the
// checks is tested here with several events. The Rust peer is in rust/tests/diff.rs (the tests named patch_*).

import (
	"errors"
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

func vc(path string, previous, next *forms.Node) forms.DiffEvent {
	return forms.DiffEvent{Kind: forms.EventValueChange, Path: path, Previous: previous, Next: next}
}

func ival(i int64) *forms.Node  { return &forms.Node{Kind: forms.KindInt, Int: i} }
func sval(s string) *forms.Node { return &forms.Node{Kind: forms.KindStr, Str: s} }

func runPatch(t *testing.T, text string, changes ...forms.DiffEvent) (*forms.PatchResult, error) {
	t.Helper()
	return forms.Patch(text, diffRoot(), accessKeys, boundGrammar(t), forms.DiffContext{}, forms.DiffContext{}, forms.PatchDiff{Version: 1, Changes: changes})
}

func patchRefusal(t *testing.T, text string, changes ...forms.DiffEvent) wire {
	t.Helper()
	res, err := runPatch(t, text, changes...)
	var fe *forms.Error
	if res != nil || !errors.As(err, &fe) || fe.Kind != forms.ErrContract || len(fe.Violations) != 1 {
		t.Fatalf("expected one contract error and no result, got %v %v", res, err)
	}
	return wireOf(fe.Violations[0])
}

const patchDoc = "mtu: 1\nname: a\nip:\n  gw: g\nports:\n  - port: 1\n    label: x\n  - port: 9\n    label: y\nvlans:\n  - ref: r\n  - vlan_id: 7\n    mode: m\nnics:\n  - name: n\n    zone: z\n    subnet: s\n"

func TestPatchApplyingAndTheSpans(t *testing.T) {
	res, err := runPatch(t, patchDoc,
		vc("$.mtu", ival(1), ival(12345)),
		vc("$.name", sval("a"), sval("bcd")),
		vc("$.ports[{port=9}].label", sval("y"), sval("yy")),
		vc(`$.nics[{name="n",zone="z"}].subnet`, sval("s"), sval("t")),
		vc("$.vlans[{vlan_id=7}].mode", sval("m"), sval("n")))
	if err != nil {
		t.Fatal(err)
	}
	want := "mtu: 12345\nname: bcd\nip:\n  gw: g\nports:\n  - port: 1\n    label: x\n  - port: 9\n    label: yy\nvlans:\n  - ref: r\n  - vlan_id: 7\n    mode: n\nnics:\n  - name: n\n    zone: z\n    subnet: t\n"
	if res.Text != want {
		t.Errorf("got %q", res.Text)
	}
	if want := []string{"$.mtu", "$.name", "$.ports[{port=9}].label", `$.nics[{name="n",zone="z"}].subnet`, "$.vlans[{vlan_id=7}].mode"}; !reflect.DeepEqual(res.Replaced, want) {
		t.Errorf("replaced %v", res.Replaced)
	}
	// each span reads the scalar in its own text: the one before in A, the one after in the result
	for _, sp := range res.Spans {
		b, _ := forms.SourceSpan(patchDoc, diffRoot(), sp.Path, accessKeys)
		a, _ := forms.SourceSpan(res.Text, diffRoot(), sp.Path, accessKeys)
		if sp.Before != b || sp.After != a {
			t.Errorf("%s: %+v, want %+v and %+v", sp.Path, sp, b, a)
		}
	}
	if res.Spans[0].After.End-res.Spans[0].After.Start != 5 || res.Spans[1].Before.Start >= res.Spans[1].After.End+10 {
		t.Errorf("spans %+v", res.Spans)
	}
	// the same diff on its own result is a conflict, nothing is applied; the empty diff is the identity
	if w := patchRefusal(t, res.Text, vc("$.mtu", ival(1), ival(12345))); w.code != "PATCH_CONFLICT" || w.path != "$.mtu" {
		t.Errorf("replay: %v", w)
	}
	same, err := runPatch(t, patchDoc)
	if err != nil || same.Text != patchDoc || len(same.Replaced) != 0 || len(same.Spans) != 0 {
		t.Errorf("empty diff: %v %v", same, err)
	}
	// an event whose next is its previous is accepted and moves nothing
	noop, err := runPatch(t, patchDoc, vc("$.mtu", ival(1), ival(1)))
	if err != nil || noop.Text != patchDoc || !reflect.DeepEqual(noop.Replaced, []string{"$.mtu"}) {
		t.Errorf("no-op event: %v %v", noop, err)
	}
}

func TestPatchTheKeyComponentsAndTheTargetsItRefuses(t *testing.T) {
	cases := []struct{ path string }{
		{"$.ports[{port=1}].port"}, {`$.vlans[{ref="r"}].ref`}, {"$.vlans[{vlan_id=7}].vlan_id"}, {`$.nics[{name="n",zone="z"}].name`}, {`$.nics[{name="n",zone="z"}].zone`},
		{"$.ip"}, {"$.ip.addrs"}, {"$.ports"}, {"$.note"}, {"$.ports[{port=1}]"},
	}
	for _, c := range cases {
		if w := patchRefusal(t, patchDoc, vc(c.path, sval("x"), sval("y"))); w.code != "PATCH_UNSUPPORTED_EVENT" || w.path != c.path || w.loc != "not_applicable" || w.phase != "patch" {
			t.Errorf("%s: %v", c.path, w)
		}
	}
	for _, path := range []string{"$.ip.gw", "$.ports[{port=1}].label", `$.nics[{name="n",zone="z"}].subnet`, "$.vlans[{vlan_id=7}].mode"} {
		var prev *forms.Node
		switch path {
		case "$.ip.gw":
			prev = sval("g")
		case "$.ports[{port=1}].label":
			prev = sval("x")
		case `$.nics[{name="n",zone="z"}].subnet`:
			prev = sval("s")
		default:
			prev = sval("m")
		}
		if _, err := runPatch(t, patchDoc, vc(path, prev, sval("q"))); err != nil {
			t.Errorf("%s: %v", path, err)
		}
	}
	// a value change that does not carry both values
	for _, e := range []forms.DiffEvent{{Kind: forms.EventValueChange, Path: "$.mtu", Next: ival(2)}, {Kind: forms.EventValueChange, Path: "$.mtu", Previous: ival(1)}} {
		if w := patchRefusal(t, patchDoc, e); w.code != "PATCH_UNSUPPORTED_EVENT" {
			t.Errorf("%v", w)
		}
	}
}

func TestPatchTheOrderOfTheChecksAcrossEvents(t *testing.T) {
	absent := vc(`$.nics[{name="zz",zone="z"}].subnet`, sval("s"), sval("t"))
	conflict := vc("$.mtu", ival(2), ival(3))
	badPath := vc("$.nope", ival(1), ival(2))
	unsupported := forms.DiffEvent{Kind: forms.EventSetChange, Path: "$.ip.addrs"}
	// unsupported before everything, whatever the order of the events
	if w := patchRefusal(t, patchDoc, conflict, badPath, unsupported); w.code != "PATCH_UNSUPPORTED_EVENT" || w.path != "$.ip.addrs" {
		t.Errorf("%v", w)
	}
	// then a path the schema does not resolve, before the absence or the conflict of an earlier event
	if w := patchRefusal(t, patchDoc, absent, conflict, badPath); w.code != "UNKNOWN_FIELD" || w.phase != "path_resolve" || w.path != "$.nope" {
		t.Errorf("%v", w)
	}
	// then the absence and the conflict, event by event in the order of the diff
	if w := patchRefusal(t, patchDoc, absent, conflict); w.code != "PATCH_TARGET_ABSENT" || w.path != absent.Path {
		t.Errorf("%v", w)
	}
	if w := patchRefusal(t, patchDoc, conflict, absent); w.code != "PATCH_CONFLICT" || w.path != "$.mtu" || w.loc != "1:6:5" {
		t.Errorf("%v", w)
	}
	// the absence is anchored at the enclosing structure: the list for an element, the mapping for a field
	if w := patchRefusal(t, patchDoc, absent); w.code != "PATCH_TARGET_ABSENT" || w.loc != "15:3:137" {
		t.Errorf("%v", w)
	}
	if w := patchRefusal(t, "name: a\n", vc("$.mtu", ival(1), ival(2))); w.code != "PATCH_TARGET_ABSENT" || w.loc != "1:1:0" {
		t.Errorf("%v", w)
	}
	// a type-strict conflict
	if w := patchRefusal(t, patchDoc, vc("$.mtu", sval("1"), ival(2))); w.code != "PATCH_CONFLICT" {
		t.Errorf("%v", w)
	}
	// the path text is checked before it is resolved
	if w := patchRefusal(t, patchDoc, vc("$.", ival(1), ival(2))); w.phase != "path_parse" {
		t.Errorf("%v", w)
	}
}

func TestPatchTheContextAndTheEarlierPhases(t *testing.T) {
	ctx := func(f func(*forms.DiffContext)) forms.DiffContext {
		c := forms.DiffContext{Schema: "s", SchemaDigest: "d", Grammar: "g", GrammarDigest: "gd", Surface: "x"}
		f(&c)
		return c
	}
	base := ctx(func(*forms.DiffContext) {})
	for i, other := range []forms.DiffContext{
		ctx(func(c *forms.DiffContext) { c.Schema = "t" }), ctx(func(c *forms.DiffContext) { c.SchemaDigest = "t" }), ctx(func(c *forms.DiffContext) { c.Grammar = "t" }),
		ctx(func(c *forms.DiffContext) { c.GrammarDigest = "t" }), ctx(func(c *forms.DiffContext) { c.Surface = "t" }),
	} {
		_, err := forms.Patch(patchDoc, diffRoot(), accessKeys, boundGrammar(t), base, other, forms.PatchDiff{Version: 1})
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Code() != "PATCH_CONTEXT_MISMATCH" || fe.Violations[0].LogicalPath != "" || fe.Violations[0].Phase != forms.PhasePatch {
			t.Errorf("context %d: %v", i, err)
		}
	}
	for _, v := range []int{0, 2, -1} {
		_, err := forms.Patch(patchDoc, diffRoot(), accessKeys, boundGrammar(t), base, base, forms.PatchDiff{Version: v})
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Code() != "PATCH_CONTEXT_MISMATCH" {
			t.Errorf("version %d: %v", v, err)
		}
	}
	if _, err := forms.Patch(patchDoc, diffRoot(), accessKeys, boundGrammar(t), base, base, forms.PatchDiff{Version: 1}); err != nil {
		t.Errorf("equal contexts: %v", err)
	}
	// A is read first: a document that does not parse or validate is that error, whatever the context says
	for src, code := range map[string]string{"a: 1\na: 2\n": "DUPLICATE_KEY", "bogus: 1\n": "UNKNOWN_FIELD"} {
		_, err := forms.Patch(src, diffRoot(), accessKeys, boundGrammar(t), base, ctx(func(c *forms.DiffContext) { c.Schema = "t" }), forms.PatchDiff{Version: 9})
		var fe *forms.Error
		if !errors.As(err, &fe) || fe.Code() != code {
			t.Errorf("%q: %v", src, err)
		}
	}
}

func TestPatchAnEventOfAnotherKindThatCarriesBothValuesIsStillRefused(t *testing.T) {
	for _, kind := range []string{forms.EventAdded, forms.EventRemoved, forms.EventPolicyDeclarationChange, forms.EventSetChange} {
		e := forms.DiffEvent{Kind: kind, Path: "$.mtu", Previous: ival(1), Next: ival(2)}
		if w := patchRefusal(t, patchDoc, e); w.code != "PATCH_UNSUPPORTED_EVENT" || w.path != "$.mtu" {
			t.Errorf("%s: %v", kind, w)
		}
	}
}

func TestPatchAPositionWithNoValueInTheDocumentIsAbsent(t *testing.T) {
	str := forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional, Nullable: true}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: diffFields("n", str)}
	_, err := forms.Patch("n:\n", root, accessKeys, boundGrammar(t), forms.DiffContext{}, forms.DiffContext{}, forms.PatchDiff{Version: 1, Changes: []forms.DiffEvent{{Kind: forms.EventValueChange, Path: "$.n", Previous: &forms.Node{Kind: forms.KindNull}, Next: sval("x")}}})
	var fe *forms.Error
	if !errors.As(err, &fe) || fe.Code() != "PATCH_TARGET_ABSENT" || wireOf(fe.Violations[0]).loc != "1:1:0" {
		t.Errorf("got %v", err)
	}
}

func TestPatchAKeyNameBelowAnElementIsNotAKeyComponent(t *testing.T) {
	str := forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional}
	inner := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Optional, Fields: diffFields("id", str)}
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: diffFields("l", diffList(false, forms.Case{Name: "e", Key: []string{"id"}, Fields: diffFields(
		"id", forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Mandatory}, "c", inner)}))}
	text := "l:\n  - id: a\n    c:\n      id: x\n"
	apply := func(path string) error {
		_, err := forms.Patch(text, root, accessKeys, boundGrammar(t), forms.DiffContext{}, forms.DiffContext{}, forms.PatchDiff{Version: 1, Changes: []forms.DiffEvent{vc(path, sval(map[bool]string{true: "a", false: "x"}[path == `$.l[{id="a"}].id`]), sval("y"))}})
		return err
	}
	if err := apply(`$.l[{id="a"}].c.id`); err != nil {
		t.Errorf("a field that is called like the key, one level down: %v", err)
	}
	var fe *forms.Error
	if err := apply(`$.l[{id="a"}].id`); !errors.As(err, &fe) || fe.Code() != "PATCH_UNSUPPORTED_EVENT" {
		t.Errorf("the key: %v", err)
	}
}
