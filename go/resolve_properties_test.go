package forms_test

// Properties of ResolveRule over generated object graphs (docs/P3-UNIT4-POLICY-RESOLUTION.md). The graphs are chains of objects with
// random carriers, object policies and field rules; the properties are the ones the design promises whatever the chain looks like.
// The Rust peer is rust/tests/resolve_properties.rs.

import (
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
