package forms_test

// Properties of expand and collapse (ADR 0001 "Round trip"): the law holds for every document that conforms to every
// composite shape, and an explicit policy setting is never lost.

import (
	"reflect"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"pgregory.net/rapid"
)

func rootShape(t *rapid.T) forms.Shape {
	s := shapeGen(2).Draw(t, "shape")
	if s.Kind != forms.ShapeComposite {
		t.Skip("the root of a document is a composite")
	}
	s.Nullable = false // the root is the surface itself, not a position: it is a mapping
	return s
}

func TestPropertyCollapseOfExpandGivesBackTheAuthoredDocument(t *testing.T) {
	g := boundGrammar(&testing.T{})
	rapid.Check(t, func(t *rapid.T) {
		s := rootShape(t)
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		doc := conforming(s, &seed, nil, nil)
		before := forms.Emit(doc)
		expanded, prov, err := forms.Expand(doc, s, keySet, g, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		if forms.Emit(doc) != before {
			t.Fatal("Expand changed its input")
		}
		for p, o := range prov {
			if o.AuthoredExpanded {
				t.Fatalf("%s was authored short", p)
			}
		}
		back, err := forms.Collapse(expanded, s, keySet, g, prov, forms.Context{})
		if err != nil || !back.Same(doc) {
			t.Fatalf("collapse(expand(x)) != x: %v\n%s\n%s", err, forms.Emit(back), before)
		}
		again, prov2, err := forms.Expand(expanded, s, keySet, g, forms.Context{})
		if err != nil || !again.Same(expanded) {
			t.Fatalf("expand is not idempotent: %v", err)
		}
		if len(prov2) != len(prov) {
			t.Fatalf("the same positions: %d != %d", len(prov2), len(prov))
		}
		// every position written expanded by Expand is authored expanded the second time
		for p, o := range prov2 {
			if !o.AuthoredExpanded {
				t.Fatalf("%s: an expanded node is authored expanded", p)
			}
		}
	})
}

// withDefaultValues gives every optional field that says it has a default a real one (a scalar, a set or an opaque value);
// the other fields declare none. The generator of shapes only sets the flag.
func withDefaultValues(s forms.Shape) forms.Shape {
	if s.Kind == forms.ShapeComposite {
		fs := make([]forms.NamedShape, len(s.Fields))
		for i, f := range s.Fields {
			fs[i] = forms.NamedShape{Name: f.Name, Shape: withDefaultValues(f.Shape)}
		}
		s.Fields, s.HasDefault, s.Default = fs, false, nil // a composite takes no default here
		return s
	}
	s.Default = nil
	if !s.HasDefault || s.Presence != forms.Optional || (s.HasMinItems && s.MinItems > 0) {
		s.HasDefault = false
		return s
	}
	switch s.Kind {
	case forms.ShapeScalar:
		switch s.Scalar {
		case forms.ScalarString:
			s.Default = &forms.Node{Kind: forms.KindStr, Str: "dflt"}
		case forms.ScalarInteger:
			s.Default = &forms.Node{Kind: forms.KindInt, Int: 7}
		default:
			s.Default = &forms.Node{Kind: forms.KindBool, Bool: true}
		}
	case forms.ShapeList, forms.ShapeSet:
		s.Default = &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{}}
	default:
		s.Default = &forms.Node{Kind: forms.KindStr, Str: "any"}
	}
	return s
}

// The composition of P2 and the defaults (docs/P3-UNIT3-COMPOSITION.md): the authored document comes back from the normalized one,
// nothing the author wrote is changed, the origin travels with the object, and the result does not depend on how the author
// spelled the policy of a position.
func TestPropertyDenormalizeOfNormalizeGivesBackTheAuthoredDocument(t *testing.T) {
	g := boundGrammar(&testing.T{})
	rapid.Check(t, func(t *rapid.T) {
		s := withDefaultValues(rootShape(t))
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		doc := conforming(s, &seed, nil, nil)
		if rapid.Bool().Draw(t, "explicit") {
			ex, _, err := forms.Expand(doc, s, keySet, g, forms.Context{})
			if err != nil {
				t.Fatal(err)
			}
			explicit(ex, &seed)
			doc = ex // mixed forms: some positions written out with an explicit policy
		}
		before := forms.Emit(doc)
		res, err := forms.Normalize(doc, s, keySet, g, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		if forms.Emit(doc) != before {
			t.Fatal("Normalize changed its input")
		}
		back, err := forms.Denormalize(res, s, keySet, g, forms.Context{})
		if err != nil || !back.Same(doc) {
			t.Fatalf("denormalize(normalize(x)) != x: %v\n%s\n%s", err, forms.Emit(back), before)
		}
		// nothing the author wrote is changed: the object minus the inserted values is the plain expansion
		_, prov, err := forms.Expand(doc, s, keySet, g, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(res.Provenance, prov) {
			t.Fatalf("the provenance of the author's positions changed:\n%v\n%v", res.Provenance, prov)
		}
		for _, o := range res.ValueOrigins {
			if _, has := res.Provenance[o.Path]; has == (o.Origin == forms.ValueOriginDefault) {
				t.Fatalf("%s: origin %s, provenance %v", o.Path, o.Origin, has)
			}
		}
		// the origin travels with the object: normalizing the result again changes neither
		again, err := forms.Renormalize(res, s, keySet, g, forms.Context{})
		if err != nil || forms.Emit(again.Object) != forms.Emit(res.Object) ||
			!reflect.DeepEqual(again.ValueOrigins, res.ValueOrigins) || !reflect.DeepEqual(again.Provenance, res.Provenance) {
			t.Fatalf("not idempotent on the pair: %v", err)
		}
	})
}

// explicit makes the policy of every top-level wrapper non-default, one setting each.
func explicit(n *forms.Node, rng *xorshift) {
	for _, e := range n.Entries {
		if e.Value.Kind != forms.KindMap {
			continue
		}
		for i, f := range e.Value.Entries {
			switch f.Key {
			case "access", "modify":
				if rng.coin() {
					e.Value.Entries[i].Value = &forms.Node{Kind: forms.KindSeq, Items: []*forms.Node{{Kind: forms.KindStr, Str: "OU=admins"}}}
				}
			case "inherit":
				if rng.coin() {
					e.Value.Entries[i].Value = &forms.Node{Kind: forms.KindInt, Int: 0}
				} else {
					e.Value.Entries[i].Value = &forms.Node{Kind: forms.KindBool}
				}
			case "default_injection":
				if rng.coin() {
					e.Value.Entries[i].Value = &forms.Node{Kind: forms.KindInt, Int: 0}
				}
			case "conformance":
				if rng.coin() {
					e.Value.Entries[i].Value = &forms.Node{Kind: forms.KindStr, Str: "deprecated"}
				}
			}
		}
	}
}

func TestPropertyAnExplicitPolicySettingIsNeverLost(t *testing.T) {
	g := boundGrammar(&testing.T{})
	rapid.Check(t, func(t *rapid.T) {
		s := rootShape(t)
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		doc := conforming(s, &seed, nil, nil)
		expanded, _, err := forms.Expand(doc, s, keySet, g, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		explicit(expanded, &seed) // the author wrote some policy: a position with any non-default field
		again, prov, err := forms.Expand(expanded, s, keySet, g, forms.Context{})
		if err != nil || !again.Same(expanded) {
			t.Fatalf("expand changed an authored expanded node: %v", err)
		}
		withProv, err := forms.Collapse(again, s, keySet, g, prov, forms.Context{})
		if err != nil || !withProv.Same(expanded) {
			t.Fatalf("with provenance an authored expanded document stays as authored: %v", err)
		}
		// without the provenance the law alone decides: a non-default field keeps its position expanded
		plain, err := forms.Collapse(again, s, keySet, g, nil, forms.Context{})
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range plain.Entries {
			var orig *forms.Node
			for _, o := range expanded.Entries {
				if o.Key == e.Key {
					orig = o.Value
				}
			}
			if orig == nil {
				t.Fatalf("a position disappeared: %s", e.Key)
			}
			if isWrapper(orig) && !defaultsOnly(orig) && !isWrapper(e.Value) {
				t.Fatalf("a position with an explicit setting was shortened: %s", e.Key)
			}
		}
	})
}

// defaultsOnly says whether an expanded node holds nothing but the symbolic defaults of access grammar 1.
func defaultsOnly(n *forms.Node) bool {
	for _, e := range n.Entries {
		switch e.Key {
		case "access", "modify":
			if e.Value.Kind != forms.KindStr || e.Value.Str != "inherit" {
				return false
			}
		case "inherit":
			if e.Value.Kind != forms.KindBool || !e.Value.Bool {
				return false
			}
		case "default_injection":
			if e.Value.Kind != forms.KindNull {
				return false
			}
		case "conformance":
			if e.Value.Kind != forms.KindStr || e.Value.Str != "implemented" {
				return false
			}
		}
	}
	return true
}

// isWrapper says whether n is an expanded node: a mapping with exactly the keys of A.
func isWrapper(n *forms.Node) bool {
	if n.Kind != forms.KindMap || len(n.Entries) != len(keySet) {
		return false
	}
	for _, k := range keySet {
		found := false
		for _, e := range n.Entries {
			found = found || e.Key == k
		}
		if !found {
			return false
		}
	}
	return true
}
