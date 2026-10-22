package forms_test

// The golden vectors of the resolution of access-policy/1 (cic-primitives, access-vectors/resolution.yaml, in the grammar pin) as the
// cross-check of ResolveRule.
//
// The vectors are request-bound: each ends in allow, deny or error for a requestor. The library decides nothing, so this test
// carries a TEST-ONLY port of the reference decision (`_decide`, `check_request`): it applies the vector's request to the effective
// rule ResolveRule found, and the outcome, the code and the layer must equal the vector's expectation. The port lives here and
// nowhere else; the library has no requestor, owner or certificate. The Python reference is never executed: only the bytes of
// the vector file are read, after their SHA-256 matched the pin.

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"gopkg.in/yaml.v3"
)

const resolutionVectors = "proposals/atom-grammar/access-vectors/resolution.yaml"

func pinnedFileSHA256(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "corpus", "pins.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var pins obj
	if err := yaml.Unmarshal(b, &pins); err != nil {
		t.Fatal(err)
	}
	for _, pin := range pins["grammar"].(obj) {
		for _, f := range pin.(obj)["files"].([]any) {
			if f.(obj)["path"] == path {
				return f.(obj)["sha256"].(string)
			}
		}
	}
	t.Fatalf("the pin does not list %s", path)
	return ""
}

func upstreamFile(t *testing.T, path string) []byte {
	t.Helper()
	root := os.Getenv("CIC_PRIMITIVES_ROOT")
	if root == "" {
		t.Fatal("CIC_PRIMITIVES_ROOT must point at a checkout of CentralInfraCore/cic-primitives at the pinned commit")
	}
	b, err := os.ReadFile(filepath.Join(root, path))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(b)
	if hex.EncodeToString(sum[:]) != pinnedFileSHA256(t, path) {
		t.Fatalf("%s does not match the pin: not evidence", path)
	}
	return b
}

func member(n *forms.Node, key string) *forms.Node {
	if n == nil {
		return nil
	}
	for _, e := range n.Entries {
		if e.Key == key {
			return e.Value
		}
	}
	return nil
}

func readRuleNode(t *testing.T, n *forms.Node) *forms.PolicyRule {
	t.Helper()
	if n == nil {
		return nil
	}
	r, v := forms.ReadRule(n)
	if v != nil {
		t.Fatalf("a vector rule does not read: %s", v.Code)
	}
	if r.Symbol {
		return nil
	}
	return &forms.PolicyRule{Patterns: r.Patterns}
}

func vectorGraph(t *testing.T, ctx *forms.Node, g forms.Grammar) forms.PolicyGraph {
	t.Helper()
	graph := forms.PolicyGraph{}
	for _, e := range member(ctx, "objects").Entries {
		o := &forms.PolicyObject{ID: e.Key, CarrierPath: e.Key}
		if p := member(e.Value, "parent"); p != nil && p.Kind == forms.KindStr {
			o.Parent, o.HasParent = p.Str, true
		}
		pol := member(e.Value, "policy")
		switch {
		case pol.Kind == forms.KindStr && pol.Str == "absent":
			o.Policy.State = forms.PolicyAbsent
		case pol.Kind == forms.KindStr && pol.Str == "unknown":
			o.Policy.State = forms.PolicyUnknown
		default:
			ps, err := forms.ReadPolicySurface(pol, g)
			if err != nil {
				t.Fatalf("%s: %v", e.Key, err)
			}
			o.Policy = forms.ObjectPolicy{State: forms.PolicyDeclared, Surface: ps}
		}
		if car := member(e.Value, "carrier"); car != nil {
			c := &forms.Carrier{Mode: forms.ModeInherit}
			if m := member(car, "inherit"); m != nil {
				mode, v := forms.ReadMode(m, g)
				if v != nil {
					t.Fatalf("%s carrier: %s", e.Key, v.Code)
				}
				c.Mode = mode
			}
			c.Access, c.Modify = readRuleNode(t, member(car, "access")), readRuleNode(t, member(car, "modify"))
			o.Carrier = c
		}
		graph[e.Key] = o
	}
	return graph
}

// ---- the TEST-ONLY port of the reference decision ------------------------------------------------------------------------------

type outcome struct{ outcome, code, layer string }

var patternWhole = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9.-]*=[^=,\s][^=,]*(,[A-Za-z][A-Za-z0-9.-]*=[^=,\s][^=,]*)*$`)

func pairs(s string) [][2]string {
	var out [][2]string
	for _, p := range strings.Split(s, ",") {
		a, v, _ := strings.Cut(p, "=")
		out = append(out, [2]string{a, v})
	}
	return out
}

func patternMatches(pattern, subject string) bool {
	have := pairs(subject)
	for _, want := range pairs(pattern) {
		parts := strings.Split(want[1], "*")
		for i := range parts {
			parts[i] = regexp.QuoteMeta(parts[i])
		}
		rx := regexp.MustCompile("^" + strings.Join(parts, ".*") + "$")
		ok := false
		for _, h := range have {
			if h[0] == want[0] && rx.MatchString(h[1]) {
				ok = true
				break
			}
		}
		if !ok {
			return false
		}
	}
	return true
}

func validIdentity(n *forms.Node) bool {
	return n != nil && n.Kind == forms.KindStr && n.Str != "" && n.Str == strings.TrimSpace(n.Str)
}

func decide(rule forms.EffectiveRule, req *forms.Node) outcome {
	op := member(req, "op").Str
	_ = op
	r := member(req, "requestor")
	layer := rule.Layer
	switch rule.Kind {
	case forms.RuleNone:
		return outcome{"deny", rule.Reason, layer}
	case forms.RuleUnknown:
		return outcome{"error", rule.Reason, layer}
	}
	if !member(r, "authenticated").Bool {
		return outcome{"deny", "UNAUTHENTICATED", layer}
	}
	switch rule.Kind {
	case forms.RuleList:
		if len(rule.Patterns) == 0 {
			return outcome{"deny", "EXPLICIT_EMPTY", layer}
		}
		for _, p := range rule.Patterns {
			if patternMatches(p, member(r, "subject").Str) {
				return outcome{"allow", "", layer}
			}
		}
		return outcome{"deny", "NO_PATTERN_MATCH", layer}
	case forms.RuleOpen:
		return outcome{"allow", "", layer}
	}
	owner := member(req, "owner")
	if owner == nil || owner.Kind != forms.KindMap || member(owner, "trust").Str != "trusted" {
		return outcome{"deny", "OWNER_NOT_TRUSTED", layer}
	}
	if !validIdentity(member(owner, "identity")) {
		return outcome{"deny", "OWNER_IDENTITY_INVALID", layer}
	}
	if !validIdentity(member(r, "identity")) {
		return outcome{"deny", "REQUESTOR_IDENTITY_INVALID", layer}
	}
	if member(owner, "identity").Str != member(r, "identity").Str {
		return outcome{"deny", "OWNER_MISMATCH", layer}
	}
	return outcome{"allow", "", layer}
}

func requestValid(req *forms.Node) bool {
	op := member(req, "op")
	r := member(req, "requestor")
	if op == nil || (op.Str != "access" && op.Str != "modify") || r == nil || r.Kind != forms.KindMap {
		return false
	}
	auth := member(r, "authenticated")
	if auth == nil || auth.Kind != forms.KindBool {
		return false
	}
	if auth.Bool {
		s := member(r, "subject")
		if s == nil || s.Kind != forms.KindStr || !patternWhole.MatchString(s.Str) || strings.Contains(s.Str, "*") {
			return false
		}
	}
	if o := member(req, "owner"); o != nil && o.Kind != forms.KindNull {
		if o.Kind != forms.KindMap {
			return false
		}
		if tr := member(o, "trust"); tr == nil || (tr.Str != "trusted" && tr.Str != "untrusted") {
			return false
		}
	}
	return true
}

var layerNames = map[string]string{"surface_rules": forms.LayerSurfaceRule, "default": forms.LayerObjectDefault}

func TestResolveRuleAgainstTheGoldenVectors(t *testing.T) {
	b := upstreamFile(t, resolutionVectors)
	doc, err := forms.Parse(string(b))
	if err != nil {
		t.Fatalf("the vector file does not parse: %v", err)
	}
	cases := member(doc, "cases")
	if cases == nil || len(cases.Items) < 80 {
		t.Fatalf("expected the resolution vectors, found %d cases", len(cases.Items))
	}
	for _, c := range cases.Items {
		id := member(c, "id").Str
		t.Run(id, func(t *testing.T) {
			in := member(c, "input")
			gv := member(in, "grammar").Int
			gn := &forms.Node{Kind: forms.KindInt, Int: gv}
			g, err := forms.BindGrammar(gn, gn)
			if err != nil {
				t.Fatal(err)
			}
			exp := member(c, "expect")
			want := outcome{member(exp, "outcome").Str, member(exp, "code").Str, member(exp, "layer").Str}
			if mapped, ok := layerNames[want.layer]; ok {
				want.layer = mapped
			}
			req := member(in, "req")
			var got outcome
			if !requestValid(req) {
				got = outcome{"error", "INVALID_REQUEST_CONTEXT", "none"}
			} else {
				graph := vectorGraph(t, member(in, "ctx"), g)
				target := member(in, "target")
				op := member(req, "op").Str
				field := readRuleNode(t, member(member(target, "field"), op))
				rule := forms.ResolveRule(graph, member(target, "object").Str, member(target, "surface").Str, field, "$.target", op)
				got = decide(rule, req)
			}
			if got != want {
				t.Fatalf("got %+v, want %+v", got, want)
			}
		})
	}
}
