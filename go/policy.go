package forms

// Reading spec.policy_surface and spec.identity.base of a pinned schema (P3, ADR 0004 and the pinned
// PolicySurface aggregate of cic-primitives, schemas/aggregate/policy-surface.yaml).
//
// The compiler records what the schema declares and nothing more: no rule is evaluated, no requestor,
// certificate, owner or snapshot is looked at, and no system default is written in. A member that is not
// declared stays undeclared (nil); that is the system layer of the resolution, never a materialized
// `open` or `owner`. A member that is declared but invalid is a SCHEMA_INVALID refusal that names the
// node: a policy the walk cannot read is never read as a more permissive one.

// PolicyRule is the declared value of default_access, default_modify or a surface rule member.
type PolicyRule struct {
	// Symbol is "open" (default_access) or "owner" (default_modify) when the schema wrote that symbol,
	// and "" when the rule is a list.
	Symbol string
	// Patterns is the list of CertPatterns when Symbol is "". The empty list is an explicit "nobody".
	Patterns []string
}

// SurfaceRule is one entry of surface_rules: finer than the object default, coarser than a field's Access.
type SurfaceRule struct {
	Surface string
	// Access and Modify are nil when the entry declares no rule for that operation (absent or `inherit`).
	Access, Modify *PolicyRule
}

// PolicySurface is what a schema declares as the object-level access policy.
type PolicySurface struct {
	// Declared is true when spec.policy_surface is present. An absent policy and `{}` are both legal and
	// transparent: the chain goes on to the ancestors.
	Declared bool
	// DefaultAccess and DefaultModify are nil when not declared (the system layer).
	DefaultAccess, DefaultModify *PolicyRule
	// SurfaceRules are in schema order.
	SurfaceRules []SurfaceRule
	// InheritPolicy is the object-scope InheritanceMode; ModeInherit when not declared.
	InheritPolicy         Mode
	InheritPolicyDeclared bool
}

const policyPath = "spec.policy_surface"

// policy reads spec.policy_surface. Every refusal is recorded and the walk goes on, so one compile names
// all of them.
func (c *compiler) policy(spec *Node) PolicySurface {
	n := lookup(spec, "policy_surface")
	if n == nil {
		return PolicySurface{InheritPolicy: ModeInherit}
	}
	return c.policyNode(n, policyPath, Grammar{version: c.profile.AccessGrammar})
}

// policyNode reads one PolicySurface mapping, named path in the problems. g is the grammar its inherit_policy is read under.
func (c *compiler) policyNode(n *Node, policyPath string, g Grammar) PolicySurface {
	out := PolicySurface{InheritPolicy: ModeInherit, Declared: true}
	if n.Kind != KindMap {
		c.record(schemaErr(SchemaInvalid, policyPath, "a mapping is required"))
		return out
	}
	for _, e := range n.Entries {
		path := policyPath + "." + e.Key
		switch e.Key {
		case "default_access":
			out.DefaultAccess = c.defaultRule(e.Value, "open", path)
		case "default_modify":
			out.DefaultModify = c.defaultRule(e.Value, "owner", path)
		case "surface_rules":
			out.SurfaceRules = c.surfaceRules(e.Value, path)
		case "inherit_policy":
			if mode, v := ReadMode(e.Value, g); v != nil {
				c.record(schemaErr(SchemaInvalid, path, "%s", v.Code))
			} else {
				out.InheritPolicy, out.InheritPolicyDeclared = mode, true
			}
		default:
			c.record(schemaErr(SchemaInvalid, path, "not a member of PolicySurface"))
		}
	}
	return out
}

// ReadPolicySurface reads one PolicySurface mapping (the value of a schema's policy_surface, or an object's policy in the
// golden vectors of the resolution) under the bound grammar. A policy that cannot be read is an error, never a more permissive
// policy.
func ReadPolicySurface(n *Node, g Grammar) (PolicySurface, error) {
	c := &compiler{}
	out := c.policyNode(n, policyPath, g)
	if len(c.problems) > 0 {
		first := c.problems[0]
		return PolicySurface{}, &Error{Kind: ErrSchema, SchemaCode: first.Code, Detail: first.Path + ": " + first.Detail, Problems: c.problems}
	}
	return out, nil
}

// defaultRule reads default_access (symbol open) or default_modify (symbol owner): a list of
// CertPatterns or that one symbol. The other symbol and `inherit` are refused.
func (c *compiler) defaultRule(n *Node, symbol, path string) *PolicyRule {
	if n.Str == symbol { // Str is empty for every kind but a string
		return &PolicyRule{Symbol: symbol}
	}
	r, v := ReadRule(n)
	switch {
	case v != nil:
		c.record(schemaErr(SchemaInvalid, path, "%s", v.Code))
	case r.Symbol:
		c.record(schemaErr(SchemaInvalid, path, "the symbol inherit is not a value here: a list of CertPatterns or %s", symbol))
	default:
		return &PolicyRule{Patterns: r.Patterns}
	}
	return nil
}

func (c *compiler) surfaceRules(n *Node, path string) []SurfaceRule {
	if n.Kind != KindSeq {
		c.record(schemaErr(SchemaInvalid, path, "a list is required"))
		return nil
	}
	rules := []SurfaceRule{}
	declared := map[[2]string]bool{}
	for i, item := range n.Items {
		ipath := sprintf("%s[%d]", path, i)
		if item.Kind != KindMap {
			c.record(schemaErr(SchemaInvalid, ipath, "a mapping is required"))
			continue
		}
		rule := SurfaceRule{Surface: strMember(item, "surface")}
		if rule.Surface == "" {
			c.record(schemaErr(SchemaInvalid, ipath+".surface", "a non-empty string is required"))
		}
		for _, e := range item.Entries {
			mpath := ipath + "." + e.Key
			switch e.Key {
			case "surface":
			case "access", "modify":
				r, v := ReadRule(e.Value)
				key := [2]string{rule.Surface, e.Key}
				switch {
				case v != nil:
					c.record(schemaErr(SchemaInvalid, mpath, "%s", v.Code))
				case r.Symbol: // inherit declares no rule for this operation
				case declared[key]:
					c.record(schemaErr(SchemaInvalid, mpath, "SURFACE_RULE_CONFLICT: %s of %q is declared twice", e.Key, rule.Surface))
				default:
					declared[key] = true
					pr := &PolicyRule{Patterns: r.Patterns}
					if e.Key == "access" {
						rule.Access = pr
					} else {
						rule.Modify = pr
					}
				}
			default:
				c.record(schemaErr(SchemaInvalid, mpath, "not a member of SurfaceRule"))
			}
		}
		rules = append(rules, rule)
	}
	return rules
}
