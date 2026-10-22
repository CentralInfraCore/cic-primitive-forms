package forms

// The effective access / modify rule of a position (P3, fourth unit; docs/P3-UNIT4-POLICY-RESOLUTION.md).
//
// This is the port of the reference resolution of access-policy/1 (cic-primitives D-018, access_policy.py `resolve`) up to,
// and not including, its decision: the walk finds WHICH rule applies, in which layer and where it was declared, and stops. It
// knows no requestor, certificate, owner or channel, and it says neither allow nor deny; the Relay decides, from this rule and a
// request context. A rule that is not known is `unknown`, a barrier with no local rule is `none`, and neither is ever turned
// into a permission.
//
// The order is the reference's: the position's own list (field); the containing object's surface_rules (surface_rule) and
// declared defaults (object_default); the rule a carrier passes down (field_inherited); the ancestors' PolicySurface
// (policy_inherited); the documented system default (system), not after a reset barrier (none). The reference's layer names
// `surface_rules` and `default` are `surface_rule` and `object_default` here.

import "slices"

// The kinds of an effective rule.
type RuleKind int

// The effective rule kinds: a list of CertPatterns (possibly empty: a terminal "nobody"), the symbols open and owner, none (a
// reset barrier with no local rule) and unknown (the walk had to read something that is not known).
const (
	RuleList RuleKind = iota
	RuleOpen
	RuleOwner
	RuleNone
	RuleUnknown
)

// The layers an effective rule is found in.
const (
	LayerField           = "field"
	LayerSurfaceRule     = "surface_rule"
	LayerObjectDefault   = "object_default"
	LayerFieldInherited  = "field_inherited"
	LayerPolicyInherited = "policy_inherited"
	LayerSystem          = "system"
	LayerNone            = "none"
)

// The reasons of a rule that is none or unknown (the reference codes).
const (
	ReasonResetNoLocalRule     = "RESET_NO_LOCAL_RULE"
	ReasonPolicyContextUnknown = "POLICY_CONTEXT_UNKNOWN"
	ReasonContextIncomplete    = "CONTEXT_INCOMPLETE"
)

// EffectiveRule is data, not a decision.
type EffectiveRule struct {
	Kind RuleKind
	// Patterns is the list of CertPatterns when Kind is RuleList; the empty list is an explicit "nobody".
	Patterns []string
	// Layer is where the rule was found; LayerNone for none and unknown.
	Layer string
	// SourcePath is the logical path of what declared it: the target position (field), the carrier that passed it down
	// (field_inherited), the declaring object (surface_rule, object_default, policy_inherited); "" for system, none and unknown.
	SourcePath string
	// Reason is set for none and unknown, one of the Reason constants.
	Reason string
}

// PolicyState says what is known about an object's PolicySurface.
type PolicyState int

// absent: known to have none (transparent); unknown: not known (an error where the walk has to read it); declared: Surface.
const (
	PolicyAbsent PolicyState = iota
	PolicyUnknown
	PolicyDeclared
)

// ObjectPolicy is the PolicySurface of one object.
type ObjectPolicy struct {
	State   PolicyState
	Surface PolicySurface
}

// Carrier is the Access of the parent field that holds an object: its InheritanceMode and the access and modify values it passes
// down (nil is the symbol inherit: no rule).
type Carrier struct {
	Mode           Mode
	Access, Modify *PolicyRule
}

// PolicyObject is one node of the object graph a resolution walks.
type PolicyObject struct {
	// ID is the logical path of the object ("$" for the root); it is the source path of what the object declares.
	ID string
	// Parent is the ID of the containing object; HasParent is false for the root.
	Parent    string
	HasParent bool
	Policy    ObjectPolicy
	// Carrier is nil for an object that no field holds (the root).
	Carrier *Carrier
	// CarrierPath is the logical path of the position whose Access is the Carrier (the source path of a field_inherited rule).
	CarrierPath string
}

// PolicyGraph maps an object ID to the object.
type PolicyGraph map[string]*PolicyObject

func unknownRule(reason string) EffectiveRule {
	return EffectiveRule{Kind: RuleUnknown, Layer: LayerNone, Reason: reason}
}

func fromRule(r *PolicyRule, layer, source string) EffectiveRule {
	switch r.Symbol {
	case "open":
		return EffectiveRule{Kind: RuleOpen, Layer: layer, SourcePath: source}
	case "owner":
		return EffectiveRule{Kind: RuleOwner, Layer: layer, SourcePath: source}
	}
	return EffectiveRule{Kind: RuleList, Patterns: slices.Clone(r.Patterns), Layer: layer, SourcePath: source}
}

// localRule is layers 2 and 3 of one object: its declared surface rule for the surface, then its declared default.
func localRule(o *PolicyObject, surface, op string) (EffectiveRule, bool) {
	if o.Policy.State != PolicyDeclared {
		return EffectiveRule{}, false
	}
	p := o.Policy.Surface
	for _, sr := range p.SurfaceRules {
		r := sr.Access
		if op == "modify" {
			r = sr.Modify
		}
		if sr.Surface == surface && r != nil {
			return fromRule(r, LayerSurfaceRule, o.ID), true
		}
	}
	d := p.DefaultAccess
	if op == "modify" {
		d = p.DefaultModify
	}
	if d != nil {
		return fromRule(d, LayerObjectDefault, o.ID), true
	}
	return EffectiveRule{}, false
}

// policyMode is the object's own inherit_policy when it declares one, and false otherwise.
func policyMode(o *PolicyObject) (Mode, bool) {
	if o.Policy.State == PolicyDeclared && o.Policy.Surface.InheritPolicyDeclared {
		return o.Policy.Surface.InheritPolicy, true
	}
	return ModeInherit, false
}

// ResolveRule finds the effective rule of one operation ("access" or "modify") for a field of an object of the graph. field is the
// field's own value for the operation (nil for the symbol inherit) and fieldPath the path of the position, for the source of a
// field rule. Same input, same output: no clock, no I/O.
func ResolveRule(g PolicyGraph, object, surface string, field *PolicyRule, fieldPath, op string) EffectiveRule {
	cur, ok := g[object]
	if !ok {
		return unknownRule(ReasonContextIncomplete)
	}
	// layer 1: the explicit rule of the target field itself
	if field != nil {
		return fromRule(field, LayerField, fieldPath)
	}
	// layers 2-3: the object's own surface rules and defaults
	if cur.Policy.State == PolicyUnknown {
		return unknownRule(ReasonPolicyContextUnknown)
	}
	if hit, ok := localRule(cur, surface, op); ok {
		return hit
	}
	// layer 4a: the rule a carrier passes down (nearest explicit list wins); a no-inherit carrier stops the field rules above it,
	// a reset carrier stops them and the chain too
	barrier, fieldOpen := false, true
	seen := map[string]bool{object: true}
	for o := cur; ; {
		if mode, declared := policyMode(o); declared && mode == ModeReset {
			barrier = true // an object-level reset cuts what the object takes from above, the carrier's rule included
			break
		}
		if o.Carrier == nil {
			break
		}
		if o.Policy.State == PolicyUnknown && o != cur {
			return unknownRule(ReasonPolicyContextUnknown)
		}
		switch o.Carrier.Mode {
		case ModeReset:
			barrier, fieldOpen = true, false
		case ModeNoInherit:
			fieldOpen = false
		default:
			if fieldOpen {
				r := o.Carrier.Access
				if op == "modify" {
					r = o.Carrier.Modify
				}
				if r != nil {
					return fromRule(r, LayerFieldInherited, o.CarrierPath)
				}
			}
		}
		if o.Carrier.Mode == ModeReset {
			break
		}
		next, ok := g[o.Parent]
		if !o.HasParent || !ok || seen[o.Parent] {
			return unknownRule(ReasonContextIncomplete)
		}
		seen[o.Parent] = true
		o = next
	}
	// layer 4b: the ancestors' PolicySurface, only where each step permits it
	seen = map[string]bool{object: true}
	for o := cur; ; {
		mode, declared := policyMode(o)
		if declared && mode == ModeReset {
			barrier = true
			break
		}
		if declared && mode == ModeNoInherit {
			break
		}
		if o.Carrier != nil && o.Carrier.Mode == ModeReset {
			barrier = true
			break
		}
		if !o.HasParent {
			break
		}
		next, ok := g[o.Parent]
		if !ok || seen[o.Parent] {
			return unknownRule(ReasonContextIncomplete)
		}
		seen[o.Parent] = true
		o = next
		if o.Policy.State == PolicyUnknown {
			return unknownRule(ReasonPolicyContextUnknown)
		}
		if hit, ok := localRule(o, surface, op); ok {
			hit.Layer = LayerPolicyInherited
			return hit
		}
	}
	// layer 5: the documented system default, never after a reset barrier
	if barrier {
		return EffectiveRule{Kind: RuleNone, Layer: LayerNone, Reason: ReasonResetNoLocalRule}
	}
	if op == "modify" {
		return EffectiveRule{Kind: RuleOwner, Layer: LayerSystem}
	}
	return EffectiveRule{Kind: RuleOpen, Layer: LayerSystem}
}
