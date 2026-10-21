package forms

// Expand and collapse (ADR 0001 "Round trip", ADR 0003 rules 5-6, ADR 0010), recursive over a compiled schema.
//
// Every primitive position (ADR 0002) is Access-wrapped by Expand: a short position gets the symbolic defaults
// (access: inherit, modify: inherit, inherit in the spelling of the bound grammar, default_injection: null,
// conformance: implemented) and its value; an expanded position stays as authored. Nothing is resolved: the symbol
// inherit stays the symbol (P3 resolves it), and an explicit policy setting is never touched. The recursion follows
// the schema, never the YAML shape: into the fields of a composite and into the fields of a list element, not into a
// set, an opaque value or a scalar. A list element is a record, not a position.
//
// Collapse is the inverse under the law of ADR 0001: a position becomes short only if every policy field equals the
// symbolic default, it was not authored expanded, its conformance basis is not binding_attested and the short form
// would be recognized as short. What Expand learned about the authored form is kept in a Provenance, keyed by the
// canonical logical path, so Collapse(Expand(x)) gives back the meaning of x: a short position is short again, an
// authored expanded one stays expanded, absent stays absent and null stays null. The input is never modified.
//
// Both expect a document that validates: a key of a composite the schema does not declare passes through
// untouched, and a structure the schema does not allow is an Unsupported error.

import "slices"

// BasisBinding is the conformance basis (ADR 0010) that is never shortened: the value is binding data.
const BasisBinding = "binding_attested"

// Origin says how a primitive position of the authored document was written.
type Origin struct {
	// AuthoredExpanded: the position was written in expanded form and stays expanded, even when it equals the defaults.
	AuthoredExpanded bool
}

// Provenance is the origin of each primitive position, keyed by its canonical logical path (ADR 0006).
type Provenance map[string]Origin

func policyDefaults(g Grammar) (map[string]*Node, error) {
	inherit, err := WriteMode(ModeInherit, g)
	if err != nil {
		return nil, err
	}
	return map[string]*Node{
		"access":            {Kind: KindStr, Str: InheritSymbol},
		"modify":            {Kind: KindStr, Str: InheritSymbol},
		"inherit":           inherit,
		"default_injection": {Kind: KindNull},
		"conformance":       {Kind: KindStr, Str: "implemented"},
	}, nil
}

func cloneTree(n *Node) *Node {
	c := *n
	if n.Items != nil {
		c.Items = make([]*Node, len(n.Items))
		for i, it := range n.Items {
			c.Items[i] = cloneTree(it)
		}
	}
	if n.Entries != nil {
		c.Entries = make([]Entry, len(n.Entries))
		for i, e := range n.Entries {
			c.Entries[i] = Entry{Key: e.Key, KeyPos: e.KeyPos, Value: cloneTree(e.Value)}
		}
	}
	return &c
}

// ExpandPosition expands one primitive position: a short node is wrapped with the symbolic defaults, an expanded node
// is returned as authored. authored says which of the two it was. A node with all of A and more is
// EXPANDED_WITH_EXTRA_KEY (a recognition error).
func ExpandPosition(n *Node, a []string, g Grammar) (out *Node, authored bool, err error) {
	class, v := classify(n, a)
	switch class {
	case ClassError:
		return nil, false, &Error{Kind: ErrContract, Violations: []Violation{*v}}
	case ClassExpanded:
		return cloneTree(n), true, nil
	}
	defaults, err := policyDefaults(g)
	if err != nil {
		return nil, false, err
	}
	out = &Node{Kind: KindMap}
	for _, k := range a {
		if k == valueKey {
			out.Entries = append(out.Entries, Entry{Key: k, Value: cloneTree(n)})
		} else if d, ok := defaults[k]; ok {
			out.Entries = append(out.Entries, Entry{Key: k, Value: cloneTree(d)})
		} else {
			return nil, false, unsupported("the expanded key set holds %q, which has no symbolic default", k)
		}
	}
	return out, false, nil
}

// CollapsePosition shortens one expanded position when the law of ADR 0001 allows it, and otherwise returns it as it
// is. A node that is not expanded is returned unchanged.
func CollapsePosition(n *Node, authoredExpanded bool, basis string, a []string, g Grammar) (*Node, error) {
	if class, _ := classify(n, a); class != ClassExpanded {
		return cloneTree(n), nil
	}
	if authoredExpanded || basis == BasisBinding {
		return cloneTree(n), nil
	}
	defaults, err := policyDefaults(g)
	if err != nil {
		return nil, err
	}
	for k, d := range defaults {
		if got := entry(n, k); got == nil || !got.Same(d) {
			return cloneTree(n), nil
		}
	}
	value := entry(n, valueKey)
	if class, _ := classify(value, a); class != ClassShort {
		return cloneTree(n), nil // a short form would be read as expanded, or rejected
	}
	return cloneTree(value), nil
}

type formWalker struct {
	a     []string
	g     Grammar
	prov  Provenance
	basis func(path string) string
}

func pathText(path []seg) (string, error) {
	t := logicalPath(path)
	if t == "" {
		return "", unsupported("a primitive position whose logical path cannot be written")
	}
	return t, nil
}

// Expand wraps every primitive position of doc, which must be a mapping read against shape (a composite), and returns
// the expanded document and the provenance of each position.
func Expand(doc *Node, shape Shape, a []string, g Grammar) (*Node, Provenance, error) {
	w := &formWalker{a: a, g: g, prov: Provenance{}}
	out, err := w.record(doc, shape, nil, true)
	if err != nil {
		return nil, nil, err
	}
	return out, w.prov, nil
}

// Collapse is the inverse of Expand under the law of ADR 0001. prov says which positions were authored expanded (a
// position it does not name is not); basis, when it is not nil, gives the conformance basis of a position by its path.
func Collapse(doc *Node, shape Shape, a []string, g Grammar, prov Provenance, basis func(path string) string) (*Node, error) {
	w := &formWalker{a: a, g: g, prov: prov, basis: basis}
	return w.record(doc, shape, nil, false)
}

// record maps the declared fields of a composite value (the root, a composite position's value, a list element).
func (w *formWalker) record(n *Node, shape Shape, path []seg, expand bool) (*Node, error) {
	return w.fields(n, shape.Fields, path, expand)
}

func (w *formWalker) fields(n *Node, fields []NamedShape, path []seg, expand bool) (*Node, error) {
	if n.Kind != KindMap {
		return nil, unsupported("a %s value where a mapping is declared", kindName(n))
	}
	out := &Node{Kind: KindMap, Pos: n.Pos, Entries: make([]Entry, 0, len(n.Entries))}
	for _, e := range n.Entries {
		i := slices.IndexFunc(fields, func(f NamedShape) bool { return f.Name == e.Key })
		if i < 0 {
			out.Entries = append(out.Entries, Entry{Key: e.Key, KeyPos: e.KeyPos, Value: cloneTree(e.Value)})
			continue
		}
		v, err := w.position(e.Value, fields[i].Shape, append(slices.Clone(path), seg{field: e.Key}), expand)
		if err != nil {
			return nil, err
		}
		out.Entries = append(out.Entries, Entry{Key: e.Key, KeyPos: e.KeyPos, Value: v})
	}
	return out, nil
}

// position expands or collapses one primitive position and what is below it.
func (w *formWalker) position(n *Node, shape Shape, path []seg, expand bool) (*Node, error) {
	text, err := pathText(path)
	if err != nil {
		return nil, err
	}
	if expand {
		ep, authored, err := ExpandPosition(n, w.a, w.g)
		if err != nil {
			return nil, withPath(err, path)
		}
		w.prov[text] = Origin{AuthoredExpanded: authored}
		return w.rewrap(ep, shape, path, true)
	}
	class, _ := classify(n, w.a)
	if class != ClassExpanded {
		return w.below(n, shape, path, false)
	}
	inner, err := w.rewrap(cloneTree(n), shape, path, false)
	if err != nil {
		return nil, err
	}
	basis := ""
	if w.basis != nil {
		basis = w.basis(text)
	}
	return CollapsePosition(inner, w.prov[text].AuthoredExpanded, basis, w.a, w.g)
}

// rewrap replaces the value of an expanded wrapper by the value processed below it.
func (w *formWalker) rewrap(wrapper *Node, shape Shape, path []seg, expand bool) (*Node, error) {
	for i, e := range wrapper.Entries {
		if e.Key != valueKey {
			continue
		}
		v, err := w.below(e.Value, shape, path, expand)
		if err != nil {
			return nil, err
		}
		wrapper.Entries[i].Value = v
	}
	return wrapper, nil
}

// below processes what a position holds: the child positions of a composite, the fields of the elements of a list.
// A scalar, a set and an opaque value are not recursed.
func (w *formWalker) below(v *Node, shape Shape, path []seg, expand bool) (*Node, error) {
	switch {
	case v.Kind == KindNull:
		return cloneTree(v), nil
	case shape.Kind == ShapeComposite:
		return w.fields(v, shape.Fields, path, expand)
	case shape.Kind == ShapeDeclaredList && shape.Elements != nil:
		if v.Kind != KindSeq {
			return nil, unsupported("a %s value where a list is declared", kindName(v))
		}
		out := &Node{Kind: KindSeq, Pos: v.Pos, Items: make([]*Node, 0, len(v.Items))}
		for _, it := range v.Items {
			if it.Kind != KindMap {
				return nil, unsupported("a %s value where a list element record is declared", kindName(it))
			}
			c, id, code, _ := identify(it, shape.Elements, w.a)
			if code != "" {
				return nil, unsupported("a list element without an identity (%s): validate the document first", code)
			}
			el, err := w.fields(it, c.Fields, append(slices.Clone(path), seg{item: id, isItem: true}), expand)
			if err != nil {
				return nil, err
			}
			out.Items = append(out.Items, el)
		}
		return out, nil
	}
	return cloneTree(v), nil
}

// withPath puts the logical path of the position on the one violation of a contract error.
func withPath(err error, path []seg) error {
	if e, ok := err.(*Error); ok && e.Kind == ErrContract && len(e.Violations) == 1 {
		e.Violations[0].LogicalPath = logicalPath(path)
	}
	return err
}
