package forms

import (
	"slices"
	"strings"
)

// Diff (P4, steps D1-D3; ADR 0008 and docs/P4-DIFF-PATCH.md): the deterministic difference between two logical views of one schema.
//
// A view is a document read under a context: Diff validates it (every expanded Access wrapper read as its value), expands it (ADR 0001:
// every primitive position carries its value and its five declaration fields) and compares the expanded trees along the schema. The
// result is evidence of a difference and nothing else: events in a fixed order, the writing differences in Representation, and the exact
// bytes of both in Canonical. Nothing is resolved, patched or judged; a difference is not drift.
//
// What makes a difference: the logical value of a scalar or an opaque position, a declaration field (compared type-strictly), the
// elements of a set, a node that exists on one side only. What does not: mapping order, list order (lists are paired by identity), flow or
// block style, quoting, and short against expanded writing of the same declaration, which is reported in Representation. A node that appears
// or disappears is one event at its top, carrying its expanded logical fragment; a change below gives no event for the parent.
//
// Scope: a diff compares only what the caller says was observed. A scope entry is a canonical path whose subtree is comparable; absence
// inside it is absence, nothing outside it is compared, and the scope is applied before parent and child events are merged. An entry
// nested in another one adds nothing.
//
// The Rust peer is rust/src/diff.rs.

// The codes of the phase diff (ADR 0008).
const (
	CodeDiffContextMismatch  = "DIFF_CONTEXT_MISMATCH"
	CodeDiffUnsupportedValue = "DIFF_UNSUPPORTED_VALUE"
)

// The kinds of an event of a diff.
const (
	EventAdded                   = "added"
	EventRemoved                 = "removed"
	EventValueChange             = "value_change"
	EventPolicyDeclarationChange = "policy_declaration_change"
	EventSetChange               = "set_change"
)

// DiffContext identifies what a view was read under: the schema and its digest, the grammar and its digest, and the surface. Two views with
// different contexts are never compared (DIFF_CONTEXT_MISMATCH).
type DiffContext struct {
	Schema, SchemaDigest, Grammar, GrammarDigest, Surface string
}

// DiffView is one operand: the document as authored, the context it was read under, and the trusted context of ADR 0010 it is expanded with.
type DiffView struct {
	Doc     *Node
	Context DiffContext
	Trust   Context
}

// DeclChange is one declaration field of an expanded position that differs.
type DeclChange struct {
	Field          string
	Previous, Next *Node
}

// DiffEvent is one difference. Which fields are set depends on Kind: Previous for removed and value_change, Next for added and value_change
// (an appearing or disappearing node carries its expanded fragment), Changed for policy_declaration_change in field order, Added and Removed
// (sorted) for set_change.
type DiffEvent struct {
	Kind           string
	Path           string
	Previous, Next *Node
	Changed        []DeclChange
	Added, Removed []*Node
	order          []string
}

// RepresentationChange is a writing difference that is not a difference: a position present in both views that was written short in one and
// expanded in the other.
type RepresentationChange struct {
	Path           string
	Previous, Next string
	order          []string
}

// DiffResult is the result of Diff. The views are logically equal exactly when Changes is empty.
type DiffResult struct {
	Changes        []DiffEvent
	Representation []RepresentationChange
}

func diffFailure(code, path, operand string) *Error {
	return &Error{Kind: ErrContract, Violations: []Violation{{Code: code, Phase: PhaseDiff, Location: Location{NotApplicable: true}, LogicalPath: path, Operand: operand}}}
}

// pathOfCodes are the codes of an element whose identity cannot be built; a diff reports them in phase path_of (ADR 0008).
var pathOfCodes = []string{CodeKeyMissing, CodeKeyType, CodeKeyUnsupported, CodeCaseNone, CodeCaseAmbiguous, CodeDuplicateIdent}

// forOperand reduces a contract error found while reading a view to its first violation, in the phase path_of for an identity code, and names
// the operand. Any other error is returned as it is.
func forOperand(err error, operand string) error {
	e, ok := err.(*Error)
	if !ok || e.Kind != ErrContract {
		return err
	}
	v := e.Violations[0]
	if slices.Contains(pathOfCodes, v.Code) {
		v.Phase = PhasePathOf
	}
	v.Operand = operand
	return &Error{Kind: ErrContract, Violations: []Violation{v}}
}

// holdsUnsupported says whether anything inside n is an unsupported number.
func holdsUnsupported(n *Node) bool {
	if n.Kind == KindUnsupported {
		return true
	}
	return slices.ContainsFunc(n.Items, holdsUnsupported) || slices.ContainsFunc(n.Entries, func(e Entry) bool { return holdsUnsupported(e.Value) })
}

// unsupportedBelow is the logical path of the first position of the value v that holds an unsupported number, in the order of the
// field names; ok is false when there is none. A value the schema does not describe at the place (an undeclared key, a list element without an
// identity) is named by the position above it.
func unsupportedBelow(v *Node, shape Shape, path []seg, a []string) (found []seg, ok bool) {
	switch {
	case v.Kind == KindMap:
		// (a mapping where the schema declares no fields is read with none: every key is undeclared and names the position above)
		entries := slices.Clone(v.Entries)
		slices.SortFunc(entries, func(x, y Entry) int { return strings.Compare(x.Key, y.Key) })
		for _, e := range entries {
			i := slices.IndexFunc(shape.Fields, func(f NamedShape) bool { return f.Name == e.Key })
			if i < 0 {
				if holdsUnsupported(e.Value) {
					return path, true
				}
				continue
			}
			if p, ok := unsupportedAt(e.Value, shape.Fields[i].Shape, append(slices.Clone(path), seg{field: e.Key}), a); ok {
				return p, true
			}
		}
		return nil, false
	case v.Kind == KindSeq && shape.Elements != nil: // only a declared list has elements
		for _, it := range v.Items {
			c, id, code, _ := identify(it, shape.Elements, a)
			if code != "" {
				if holdsUnsupported(it) {
					return path, true
				}
				continue
			}
			if p, ok := unsupportedBelow(it, Shape{Kind: ShapeComposite, Fields: c.Fields}, append(slices.Clone(path), seg{item: id, isItem: true}), a); ok {
				return p, true
			}
		}
		return nil, false
	}
	return path, holdsUnsupported(v)
}

// unsupportedAt looks at one position: through an expanded wrapper at its value, and at the other members of the wrapper as a whole.
func unsupportedAt(n *Node, shape Shape, path []seg, a []string) ([]seg, bool) {
	if class, _ := classify(n, a); class != ClassExpanded {
		return unsupportedBelow(n, shape, path, a)
	}
	for _, e := range n.Entries {
		if e.Key == valueKey {
			if p, ok := unsupportedBelow(e.Value, shape, path, a); ok {
				return p, true
			}
		} else if holdsUnsupported(e.Value) {
			return path, true
		}
	}
	return nil, false
}

// readView validates and expands one operand. Nothing is compared yet.
func readView(v DiffView, root Shape, a []string, g Grammar, operand string) (*Node, Provenance, error) {
	if err := refuseInvalid(v.Doc, root, a); err != nil {
		return nil, nil, forOperand(err, operand)
	}
	out, prov, err := Expand(v.Doc, root, a, g, v.Trust)
	if err != nil {
		return nil, nil, forOperand(err, operand)
	}
	return out, prov, nil
}

// scopeEntries reads the scope into canonical paths, drops the entries that lie inside another one and the repeated ones, and says whether the
// scope is the whole document (no scope given). A scope that is empty but given compares nothing.
func scopeEntries(root Shape, scope []string) ([][]seg, error) {
	if scope == nil {
		return [][]seg{nil}, nil
	}
	all := make([][]seg, 0, len(scope))
	for _, text := range scope {
		segs, perr := ParsePath(text)
		if perr != nil {
			return nil, pathFailure(perr)
		}
		if _, perr := ResolvePath(root, segs); perr != nil {
			return nil, pathFailure(perr)
		}
		all = append(all, segsOf(segs))
	}
	keep := make([][]seg, 0, len(all))
	for i, e := range all {
		inside := slices.ContainsFunc(all, func(o []seg) bool { return len(o) < len(e) && hasPrefix(e, o) })
		repeated := slices.ContainsFunc(all[:i], func(o []seg) bool { return len(o) == len(e) && hasPrefix(e, o) })
		if !inside && !repeated {
			keep = append(keep, e)
		}
	}
	return keep, nil
}

func segsOf(p []PathSeg) []seg {
	out := make([]seg, len(p))
	for i, s := range p {
		out[i] = seg{field: s.Field, item: s.Item, isItem: s.IsItem}
	}
	return out
}

// hasPrefix says whether path starts with prefix, which is not longer than path.
func hasPrefix(path, prefix []seg) bool {
	for i, s := range prefix {
		if path[i].key() != s.key() {
			return false
		}
	}
	return true
}

// Diff compares two views, A to B, read under root (a composite) with the expanded key set a and the bound grammar g.
//
// The order of what it refuses: the contexts differ (DIFF_CONTEXT_MISMATCH); a scope entry that is not a canonical path of the schema; a float
// or an integer beyond 64 bits anywhere in an operand, A first (DIFF_UNSUPPORTED_VALUE, whether or not it would take part); a view that does not
// validate or expand, A first, with the operand named. One error per response. Nothing is compared before every operand has been read.
//
// scope is nil for the whole document, otherwise the canonical paths of the observed subtrees (an empty list compares nothing).
func Diff(va, vb DiffView, root Shape, a []string, g Grammar, scope []string) (*DiffResult, error) {
	if va.Context != vb.Context {
		return nil, diffFailure(CodeDiffContextMismatch, "", "")
	}
	entries, err := scopeEntries(root, scope)
	if err != nil {
		return nil, err
	}
	for i, v := range []DiffView{va, vb} {
		if p, ok := unsupportedAt(v.Doc, root, nil, a); ok {
			return nil, diffFailure(CodeDiffUnsupportedValue, logicalPath(p), operandName(i))
		}
	}
	ea, pa, err := readView(va, root, a, g, "a")
	if err != nil {
		return nil, err
	}
	eb, pb, err := readView(vb, root, a, g, "b")
	if err != nil {
		return nil, err
	}
	d := &differ{a: a, provA: pa, provB: pb, result: &DiffResult{}}
	for _, e := range entries {
		sa, fa, err := descend(slot{node: ea, shape: root, record: true}, e, a)
		if err != nil {
			return nil, err
		}
		sb, fb, err := descend(slot{node: eb, shape: root, record: true}, e, a)
		if err != nil {
			return nil, err
		}
		if err := d.slots(e, sa, fa, sb, fb); err != nil {
			return nil, err
		}
	}
	d.sort()
	return d.result, nil
}

func operandName(i int) string { return [...]string{"a", "b"}[i] }
