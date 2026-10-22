package forms_test

// The shared PolicyResolution cases (testdata/p1/resolution.json): a document is normalized, then every position of the object is
// resolved. The expectations were derived from the rules of the reference resolution (access_policy.py, in the pin), not from this
// code's output; the golden vectors themselves are run in resolve_vectors_test.go. The Rust peer is rust/tests/resolution.rs.

import (
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

type resolutionFile struct {
	GrammarPin string   `json:"grammar_pin"`
	AccessKeys []string `json:"access_keys"`
	Cases      []struct {
		ID        string   `json:"id"`
		Schema    string   `json:"schema"`
		Grammar   int64    `json:"grammar"`
		YAML      string   `json:"yaml"`
		Unknown   []string `json:"unknown"`
		Expect    [][]any  `json:"expect"`
		Positions []string `json:"positions"`
	} `json:"cases"`
}

var kindNames = map[forms.RuleKind]string{forms.RuleList: "list", forms.RuleOpen: "open", forms.RuleOwner: "owner", forms.RuleNone: "none", forms.RuleUnknown: "unknown"}

func ruleRow(path, op string, r forms.EffectiveRule) []any {
	var pats any
	if r.Kind == forms.RuleList {
		p := make([]any, 0, len(r.Patterns))
		for _, x := range r.Patterns {
			p = append(p, x)
		}
		pats = p
	}
	return []any{path, op, kindNames[r.Kind], r.Layer, r.SourcePath, pats, r.Reason}
}

func resolveCase(t *testing.T, f resolutionFile, schema string, g int64, y string, unknown []string) (forms.PolicyResolution, *forms.NormalizationResult, *forms.Node) {
	t.Helper()
	c, err := compileDefaults(schema, f.GrammarPin, "config_surface")
	if err != nil {
		t.Fatal(err)
	}
	doc, err := forms.Parse(y)
	if err != nil {
		t.Fatal(err)
	}
	gr := bound(t, g)
	res, err := forms.Normalize(doc, c.Root, f.AccessKeys, gr, forms.Context{})
	if err != nil {
		t.Fatal(err)
	}
	pr, err := forms.ResolvePolicy(res.Object, c.Root, c.Policy, "config_surface", f.AccessKeys, gr, unknown)
	if err != nil {
		t.Fatal(err)
	}
	return pr, res, doc
}

func TestSharedPolicyResolutionCases(t *testing.T) {
	b, err := os.ReadFile("../testdata/p1/resolution.json")
	if err != nil {
		t.Fatal(err)
	}
	var f resolutionFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.ID, func(t *testing.T) {
			pr, res, doc := resolveCase(t, f, c.Schema, c.Grammar, c.YAML, c.Unknown)
			var positions []string
			for p := range pr {
				positions = append(positions, p)
			}
			slices.Sort(positions)
			if !reflect.DeepEqual(positions, c.Positions) {
				t.Fatalf("positions\n got %v\nwant %v", positions, c.Positions)
			}
			for _, row := range c.Expect {
				path, op := row[0].(string), row[1].(string)
				e := pr[path]
				got := ruleRow(path, op, e.Access)
				if op == "modify" {
					got = ruleRow(path, op, e.Modify)
				}
				if !reflect.DeepEqual(got, row) {
					t.Errorf("%s %s\n got %v\nwant %v", path, op, got, row)
				}
			}
			// the resolution leaves the normalization alone: the authored document still comes back
			c2, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
			back, err := forms.Denormalize(res, c2.Root, f.AccessKeys, bound(t, c.Grammar), forms.Context{})
			if err != nil || !back.Same(doc) {
				t.Fatalf("denormalize after the resolution: %v", err)
			}
		})
	}
}

func TestPolicyResolutionDoesNotDependOnTheOrderOfTheListElements(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	for _, c := range f.Cases {
		if !strings.HasPrefix(c.ID, "list-elements") {
			continue
		}
		pr1, _, _ := resolveCase(t, f, c.Schema, c.Grammar, c.YAML, nil)
		// the same elements in the other order
		first := "    - to: b\n      metric:\n"
		i := strings.Index(c.YAML, first)
		j := strings.Index(c.YAML, "    - to: a\n")
		k := strings.Index(c.YAML, "  access: [O=r]")
		if i < 0 || j < i || k < j {
			t.Fatal("the case text changed: the reorder found nothing")
		}
		swapped := c.YAML[:i] + c.YAML[j:k] + c.YAML[i:j] + c.YAML[k:]
		pr2, _, _ := resolveCase(t, f, c.Schema, c.Grammar, swapped, nil)
		if !reflect.DeepEqual(pr1, pr2) {
			t.Fatalf("a different order of the elements gave another resolution")
		}
	}
}

func TestPolicyResolutionRefusesWhatItCannotDescribe(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	c := f.Cases[0]
	cs, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
	g := bound(t, 1)
	short, _ := forms.Parse("type: t\n") // a short position is not an expanded one
	if r, err := forms.ResolvePolicy(short, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, nil); r != nil || err == nil {
		t.Fatalf("a document that was not normalized was resolved: %v / %v", r, err)
	}
	doc, _ := forms.Parse("type: t\n")
	res, _ := forms.Normalize(doc, cs.Root, f.AccessKeys, g, forms.Context{})
	if r, err := forms.ResolvePolicy(res.Object, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, []string{"$.nowhere"}); r != nil || err == nil {
		t.Fatalf("an unknown object that does not exist was accepted: %v / %v", r, err)
	}
	bad, _ := forms.Parse("bogus:\n  value: 1\n  access: inherit\n  modify: inherit\n  inherit: true\n  default_injection: ~\n  conformance: implemented\n")
	if r, err := forms.ResolvePolicy(bad, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, nil); r != nil || err == nil {
		t.Fatalf("an undeclared field was resolved: %v / %v", r, err)
	}
}

// wrapped is an expanded position around v, with the access, modify and inherit members given as text.
func wrapped(v, access, modify, inherit string) string {
	return "{value: " + v + ", access: " + access + ", modify: " + modify + ", inherit: " + inherit + ", default_injection: ~, conformance: implemented}"
}

var refusalText = map[string]string{
	"an access value that is not a rule":     "access of the position",
	"a modify value that is not a rule":      "modify of the position",
	"an inherit value that is not a mode":    "inherit of the position",
	"a list position that holds a scalar":    "a value that is not a list",
	"a list element that is not a record":    "not a record",
	"a list element without an identity":     "without an identity",
	"a short position inside a list element": "$.routes[{to=\"a\"}].to is not an expanded",
	"an undeclared field in a list element":  "is not a field the schema declares",
	"an undeclared field in a composite":     "is not a field the schema declares",
	"a short position in a composite":        "$.ip.gateway is not an expanded",
	"a value that is not a mapping":          "where a mapping is declared",
}

func TestPolicyResolutionRefusesEveryStructureItCannotWalk(t *testing.T) {
	b, _ := os.ReadFile("../testdata/p1/resolution.json")
	var f resolutionFile
	_ = json.Unmarshal(b, &f)
	c := f.Cases[0]
	cs, _ := compileDefaults(c.Schema, f.GrammarPin, "config_surface")
	g := bound(t, 1)
	ok := func(v string) string { return wrapped(v, "inherit", "inherit", "true") }
	for name, y := range map[string]string{
		"an access value that is not a rule":     "type: " + wrapped("t", "bogus", "inherit", "true") + "\n",
		"a modify value that is not a rule":      "type: " + wrapped("t", "inherit", "bogus", "true") + "\n",
		"an inherit value that is not a mode":    "type: " + wrapped("t", "inherit", "inherit", "maybe") + "\n",
		"a list position that holds a scalar":    "routes: " + ok("5") + "\n",
		"a list element that is not a record":    "routes: " + ok("[1]") + "\n",
		"a list element without an identity":     "routes: " + ok("[{metric: "+ok("1")+"}]") + "\n",
		"a short position inside a list element": "routes: " + ok("[{to: a}]") + "\n",
		"an undeclared field in a list element":  "routes: " + ok("[{to: "+ok("a")+", zzz: "+ok("1")+"}]") + "\n",
		"an undeclared field in a composite":     "ip: " + ok("{zzz: "+ok("1")+"}") + "\n",
		"a short position in a composite":        "ip: " + ok("{gateway: g}") + "\n",
		"a value that is not a mapping":          "ip: " + ok("5") + "\n",
	} {
		t.Run(name, func(t *testing.T) {
			doc, err := forms.Parse(y)
			if err != nil {
				t.Fatal(err)
			}
			r, err := forms.ResolvePolicy(doc, cs.Root, cs.Policy, "config_surface", f.AccessKeys, g, nil)
			if r != nil || err == nil {
				t.Fatalf("resolved what it cannot describe: %v", r)
			}
			if !strings.Contains(err.Error(), refusalText[name]) {
				t.Fatalf("refused for another reason: %v (want %q)", err, refusalText[name])
			}
		})
	}
}

func TestResolveRuleOnAnIncompleteOrCyclicGraph(t *testing.T) {
	absent := forms.ObjectPolicy{State: forms.PolicyAbsent}
	inherit := &forms.Carrier{Mode: forms.ModeInherit}
	incomplete := func(g forms.PolicyGraph, target string) {
		t.Helper()
		r := forms.ResolveRule(g, target, "config_surface", nil, target+".f", "access")
		if r.Kind != forms.RuleUnknown || r.Reason != forms.ReasonContextIncomplete || r.Layer != forms.LayerNone {
			t.Fatalf("want unknown / CONTEXT_INCOMPLETE, got %+v", r)
		}
	}
	// a cycle through the carriers
	incomplete(forms.PolicyGraph{
		"a": {ID: "a", Parent: "b", HasParent: true, Policy: absent, Carrier: inherit},
		"b": {ID: "b", Parent: "a", HasParent: true, Policy: absent, Carrier: inherit},
	}, "a")
	// a carrier whose parent is not in the graph
	incomplete(forms.PolicyGraph{"a": {ID: "a", Parent: "zz", HasParent: true, Policy: absent, Carrier: inherit}}, "a")
	// a carrier with no parent at all
	incomplete(forms.PolicyGraph{"a": {ID: "a", Policy: absent, Carrier: inherit}}, "a")
	// the object itself is not in the graph
	incomplete(forms.PolicyGraph{}, "a")
	// the walk over the ancestors' policy: a missing parent and a cycle (no carrier, so the first walk ends at once)
	incomplete(forms.PolicyGraph{"a": {ID: "a", Parent: "zz", HasParent: true, Policy: absent}}, "a")
	incomplete(forms.PolicyGraph{
		"a": {ID: "a", Parent: "b", HasParent: true, Policy: absent},
		"b": {ID: "b", Parent: "a", HasParent: true, Policy: absent},
	}, "a")
}

func TestASurfaceInAnUndeclaredPolicyIsIgnoredAndAParentFlagIsNeeded(t *testing.T) {
	list := &forms.PolicyRule{Patterns: []string{"O=r"}}
	// an absent policy carries no rule, whatever its surface holds
	g := forms.PolicyGraph{"$": {ID: "$", Policy: forms.ObjectPolicy{State: forms.PolicyAbsent, Surface: forms.PolicySurface{Declared: true, DefaultAccess: list}}}}
	if r := forms.ResolveRule(g, "$", "config_surface", nil, "$.f", "access"); r.Layer != forms.LayerSystem {
		t.Fatalf("the surface of an absent policy was used: %+v", r)
	}
	// a parent that is named but not flagged as one is not followed
	g = forms.PolicyGraph{
		"a": {ID: "a", Parent: "b", Policy: forms.ObjectPolicy{State: forms.PolicyAbsent}, Carrier: &forms.Carrier{Mode: forms.ModeInherit}},
		"b": {ID: "b", Policy: forms.ObjectPolicy{State: forms.PolicyAbsent}},
	}
	if r := forms.ResolveRule(g, "a", "config_surface", nil, "a.f", "access"); r.Kind != forms.RuleUnknown || r.Reason != forms.ReasonContextIncomplete {
		t.Fatalf("a parent without the flag was followed: %+v", r)
	}
}

func TestAnUndeclaredInheritPolicyIsIgnored(t *testing.T) {
	list := &forms.PolicyRule{Patterns: []string{"O=r"}}
	g := forms.PolicyGraph{
		"$": {ID: "$", Policy: forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{Declared: true, DefaultAccess: list}}},
		"$.a": {ID: "$.a", Parent: "$", HasParent: true, Carrier: &forms.Carrier{Mode: forms.ModeInherit},
			Policy: forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{Declared: true, InheritPolicy: forms.ModeReset}}},
	}
	r := forms.ResolveRule(g, "$.a", "config_surface", nil, "$.a.f", "access")
	if r.Layer != forms.LayerPolicyInherited || r.SourcePath != "$" {
		t.Fatalf("an inherit_policy the schema did not declare changed the walk: %+v", r)
	}
}

// The scenario was run through the reference (access_policy.py, resolve): an object whose own policy stops the inheritance, under a
// carrier that resets, is still behind the reset barrier of the carrier walk: deny, RESET_NO_LOCAL_RULE.
func TestAResetCarrierAboveAnObjectThatDoesNotInheritStillSetsTheBarrier(t *testing.T) {
	g := forms.PolicyGraph{
		"$": {ID: "$", Policy: forms.ObjectPolicy{State: forms.PolicyAbsent}},
		"a": {ID: "a", Parent: "$", HasParent: true, Policy: forms.ObjectPolicy{State: forms.PolicyAbsent}, Carrier: &forms.Carrier{Mode: forms.ModeReset}},
		"o1": {ID: "o1", Parent: "a", HasParent: true, Carrier: &forms.Carrier{Mode: forms.ModeInherit},
			Policy: forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: forms.PolicySurface{Declared: true, InheritPolicy: forms.ModeNoInherit, InheritPolicyDeclared: true}}},
	}
	r := forms.ResolveRule(g, "o1", "config_surface", nil, "o1.f", "access")
	if r.Kind != forms.RuleNone || r.Reason != forms.ReasonResetNoLocalRule {
		t.Fatalf("want none / RESET_NO_LOCAL_RULE, got %+v", r)
	}
}

// A field name that cannot be written as an ADR 0006 path (a control character) has no key in the resolution: it is refused. An identity is always writable (it is escaped).
// The Rust peer is `resolution_refuses_a_position_it_cannot_name` in rust/tests/resolution.rs.
func TestPolicyResolutionRefusesAPositionItCannotName(t *testing.T) {
	g := bound(t, 1)
	name := "a" + string(rune(7)) + "b" // built, not written: a control character in a source file is easy to lose
	root := forms.Shape{Kind: forms.ShapeComposite, Presence: forms.Mandatory, Fields: []forms.NamedShape{{Name: name,
		Shape: forms.Shape{Kind: forms.ShapeScalar, Scalar: forms.ScalarString, Presence: forms.Optional}}}}
	doc, err := forms.Parse("\"" + `a\a` + "b\": " + wrapped("y", "inherit", "inherit", "true") + "\n")
	if err != nil {
		t.Fatal(err)
	}
	if r, err := forms.ResolvePolicy(doc, root, forms.PolicySurface{}, "config_surface", keySet, g, nil); r != nil || err == nil {
		t.Fatalf("a field whose name cannot be written was resolved: %v / %v", r, err)
	}
}
