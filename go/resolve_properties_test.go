package forms_test

// Properties of ResolveRule over generated object graphs (docs/P3-UNIT4-POLICY-RESOLUTION.md). The graphs are chains of objects with
// random carriers, object policies and field rules; the properties are the ones the design promises whatever the chain looks like.
// The Rust peer is rust/tests/resolve_properties.rs.

import (
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"pgregory.net/rapid"
)

func genMode(t *rapid.T, name string) forms.Mode {
	return rapid.SampledFrom([]forms.Mode{forms.ModeInherit, forms.ModeNoInherit, forms.ModeReset}).Draw(t, name)
}

func genRule(t *rapid.T, name string) *forms.PolicyRule {
	switch rapid.IntRange(0, 3).Draw(t, name) {
	case 0:
		return nil
	case 1:
		return &forms.PolicyRule{Patterns: []string{}} // an explicit "nobody"
	default:
		return &forms.PolicyRule{Patterns: []string{"O=r"}}
	}
}

// chain builds $ <- $.o1 <- ... <- $.on and returns the graph and the id of the deepest object. `policy` decides what each object
// declares; `carrier` what each non-root object's carrier passes down.
func chain(t *rapid.T, n int, policy func(i int) forms.ObjectPolicy, carrier func(i int) *forms.Carrier) (forms.PolicyGraph, string) {
	g := forms.PolicyGraph{}
	prev, id := "", "$"
	for i := 0; i <= n; i++ {
		if i > 0 {
			id = prev + ".o" + string(rune('0'+i))
		}
		o := &forms.PolicyObject{ID: id, Policy: policy(i), CarrierPath: id}
		if i > 0 {
			o.Parent, o.HasParent, o.Carrier = prev, true, carrier(i)
		}
		g[id] = o
		prev = id
	}
	return g, id
}

func TestPropertyAnUnknownAncestorIsNeverPermissive(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.IntRange(1, 6).Draw(t, "depth")
		k := rapid.IntRange(0, n).Draw(t, "unknown at")
		modes := make([]forms.Mode, n+1)
		for i := 1; i <= n; i++ {
			modes[i] = genMode(t, "mode")
		}
		g, target := chain(t, n,
			func(i int) forms.ObjectPolicy {
				if i == k {
					return forms.ObjectPolicy{State: forms.PolicyUnknown}
				}
				return forms.ObjectPolicy{State: forms.PolicyAbsent}
			},
			func(i int) *forms.Carrier { return &forms.Carrier{Mode: modes[i]} })
		for _, op := range []string{"access", "modify"} {
			r := forms.ResolveRule(g, target, "config_surface", nil, target+".f", op)
			if r.Kind != forms.RuleUnknown && r.Kind != forms.RuleNone {
				t.Fatalf("%s: an unknown ancestor gave %v (layer %s)", op, r.Kind, r.Layer)
			}
			if r.Layer != forms.LayerNone || r.Reason == "" {
				t.Fatalf("%s: none/unknown must name the none layer and a reason, got %+v", op, r)
			}
		}
	})
}

func TestPropertyTheResultIsAlwaysWellFormed(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.IntRange(0, 6).Draw(t, "depth")
		pol := make([]forms.ObjectPolicy, n+1)
		car := make([]*forms.Carrier, n+1)
		for i := 0; i <= n; i++ {
			switch rapid.IntRange(0, 3).Draw(t, "policy kind") {
			case 0:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyAbsent}
			case 1:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyUnknown}
			default:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{
					Declared: true, DefaultAccess: genRule(t, "default access"), DefaultModify: genRule(t, "default modify"),
					InheritPolicy: genMode(t, "inherit_policy"), InheritPolicyDeclared: true,
				}}
			}
			car[i] = &forms.Carrier{Mode: genMode(t, "carrier mode"), Access: genRule(t, "carrier access"), Modify: genRule(t, "carrier modify")}
		}
		g, target := chain(t, n, func(i int) forms.ObjectPolicy { return pol[i] }, func(i int) *forms.Carrier { return car[i] })
		field := genRule(t, "field")
		for _, op := range []string{"access", "modify"} {
			r := forms.ResolveRule(g, target, "config_surface", field, target+".f", op)
			switch r.Kind {
			case forms.RuleNone, forms.RuleUnknown:
				if r.Layer != forms.LayerNone || r.Reason == "" || r.SourcePath != "" {
					t.Fatalf("%s: malformed none/unknown %+v", op, r)
				}
			case forms.RuleOpen, forms.RuleOwner:
				if r.Layer != forms.LayerSystem || r.SourcePath != "" || r.Reason != "" {
					t.Fatalf("%s: a system rule must come from the system layer, got %+v", op, r)
				}
			case forms.RuleList:
				if r.Layer == forms.LayerNone || r.Layer == forms.LayerSystem || r.SourcePath == "" || r.Reason != "" {
					t.Fatalf("%s: a list rule must name its layer and source, got %+v", op, r)
				}
			}
		}
	})
}

func TestPropertyAFieldRuleOnAnObjectWithNoPolicyWins(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.IntRange(0, 5).Draw(t, "depth")
		g, target := chain(t, n,
			func(int) forms.ObjectPolicy { return forms.ObjectPolicy{State: forms.PolicyAbsent} },
			func(int) *forms.Carrier {
				return &forms.Carrier{Mode: genMode(t, "mode"), Access: genRule(t, "a"), Modify: genRule(t, "m")}
			})
		field := &forms.PolicyRule{Patterns: rapid.SampledFrom([][]string{{}, {"O=r"}}).Draw(t, "field")}
		for _, op := range []string{"access", "modify"} {
			r := forms.ResolveRule(g, target, "config_surface", field, target+".f", op)
			if r.Kind != forms.RuleList || r.Layer != forms.LayerField || r.SourcePath != target+".f" {
				t.Fatalf("%s: a field rule did not win: %+v", op, r)
			}
		}
	})
}

// shuffled is a copy of n whose mapping entries, at every depth, are in an order drawn from rng. The values are untouched.
func shuffled(n *forms.Node, rng *xorshift) *forms.Node {
	c := *n
	if n.Kind == forms.KindMap {
		c.Entries = make([]forms.Entry, len(n.Entries))
		for i, e := range n.Entries {
			c.Entries[i] = forms.Entry{Key: e.Key, KeyPos: e.KeyPos, Value: shuffled(e.Value, rng)}
		}
		for i := len(c.Entries) - 1; i > 0; i-- {
			j := int(rng.next() % uint64(i+1))
			c.Entries[i], c.Entries[j] = c.Entries[j], c.Entries[i]
		}
	}
	if n.Kind == forms.KindSeq {
		c.Items = make([]*forms.Node, len(n.Items))
		for i, it := range n.Items {
			c.Items[i] = shuffled(it, rng) // the entries of a record, not the order of the items
		}
	}
	return &c
}

// G2 of the P3 exit-criteria check: reordering the entries of a mapping, at any depth, changes neither the normalization (as a value, with
// its origins) nor the resolution, whole (rule, layer, source path and reason of every position). The Rust peer is
// rust/tests/resolve_properties.rs, `mapping_order_changes_no_result`.
func TestPropertyMappingOrderChangesNoResult(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/resolution.json")
	if err != nil {
		t.Fatal(err)
	}
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		cs, err := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
		if err != nil {
			t.Fatal(err)
		}
		g := bound(t, c.Grammar)
		doc, _ := forms.Parse(c.YAML)
		run := func(d *forms.Node) (*forms.NormalizationResult, forms.PolicyResolution, error) {
			n, err := forms.Normalize(d, cs.Root, f.AccessKeys, g, forms.Context{})
			if err != nil {
				return nil, nil, err
			}
			r, err := forms.ResolvePolicy(n.Object, cs.Root, cs.Policy, cs.Surface, f.AccessKeys, g, c.Unknown)
			return n, r, err
		}
		baseN, baseR, err := run(doc)
		if err != nil {
			t.Fatal(err)
		}
		rapid.Check(t, func(t *rapid.T) {
			seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
			otherN, otherR, err := run(shuffled(doc, &seed))
			if err != nil {
				t.Fatalf("%s: a different order of the entries was refused: %v", c.ID, err)
			}
			if !otherN.Object.Same(baseN.Object) || !slices.Equal(otherN.ValueOrigins, baseN.ValueOrigins) {
				t.Fatalf("%s: a different order of the entries changed the normalization", c.ID)
			}
			if !reflect.DeepEqual(otherR, baseR) {
				t.Fatalf("%s: a different order of the entries changed the resolution", c.ID)
			}
		})
	}
}

// G5: the local layers are never cut. A position's own list wins over everything above it, whatever the chain looks like; and the
// containing object's own rule applies even when its carrier or its own policy resets or stops the inheritance.
func TestPropertyLocalRulesAreNeverCut(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		n := rapid.IntRange(0, 6).Draw(t, "depth")
		pol := make([]forms.ObjectPolicy, n+1)
		car := make([]*forms.Carrier, n+1)
		for i := 0; i <= n; i++ {
			switch rapid.IntRange(0, 2).Draw(t, "policy kind") {
			case 0:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyAbsent}
			case 1:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyUnknown}
			default:
				pol[i] = forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{
					Declared: true, DefaultAccess: genRule(t, "da"), DefaultModify: genRule(t, "dm"),
					InheritPolicy: genMode(t, "inherit_policy"), InheritPolicyDeclared: true,
				}}
			}
			car[i] = &forms.Carrier{Mode: genMode(t, "carrier mode"), Access: genRule(t, "ca"), Modify: genRule(t, "cm")}
		}
		// the target object always declares its own default for both operations, whatever the chain above it
		own := forms.PolicyRule{Patterns: []string{"O=own"}}
		pol[n] = forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{
			Declared: true, DefaultAccess: &own, DefaultModify: &own,
			InheritPolicy: genMode(t, "own inherit_policy"), InheritPolicyDeclared: true,
		}}
		g, target := chain(t, n, func(i int) forms.ObjectPolicy { return pol[i] }, func(i int) *forms.Carrier { return car[i] })
		field := genRule(t, "field")
		for _, op := range []string{"access", "modify"} {
			r := forms.ResolveRule(g, target, "config_surface", field, target+".f", op)
			switch {
			case field != nil:
				if r.Layer != forms.LayerField || r.SourcePath != target+".f" {
					t.Fatalf("%s: the position's own rule was cut: %+v", op, r)
				}
			default:
				if r.Kind != forms.RuleList || r.Layer != forms.LayerObjectDefault || r.SourcePath != target {
					t.Fatalf("%s: the object's own default was cut: %+v", op, r)
				}
			}
		}
	})
}
