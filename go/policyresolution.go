package forms

// PolicyResolution (P3, fourth unit; docs/P3-UNIT4-POLICY-RESOLUTION.md): the effective access and modify rule of every position of
// a normalized object, found by walking the object together with the compiled schema.
//
// The positions are the ones that exist in the object: nothing is created to be resolved, and the origin map of the normalization is
// neither read nor changed. The objects of the graph are the root, the value of a composite position and every list element
// (named by its declared identity); the carrier of an object is the Access wrapper of the position that holds it (for a list element,
// the wrapper of the list field). The only PolicySurface a compiled schema has is the root's. The result is data: ResolveRule finds the
// rule, and the decision (a requestor, a certificate, an owner) is the Relay's.

import "slices"

// PolicyEntry is the effective rule of one position for both operations.
type PolicyEntry struct {
	Access, Modify EffectiveRule
}

// PolicyResolution is keyed by the canonical logical path of the position.
type PolicyResolution map[string]PolicyEntry

// ResolvePolicy resolves every position of object, an expanded document (the Object of a NormalizationResult) read against root.
// policy is the compiled schema's PolicySurface and surface the surface it was compiled for. unknown names, by logical path, the
// objects whose policy the caller does not know ("$" is the root): a rule that has to read one of them is unknown. An object that is
// not expanded, a structure the schema does not allow, or an unknown path that names no object is an Unsupported error. An object
// that the walk accepts but that does not validate (every wrapper read as its value) is refused with its violations before a result
// is made: two list elements of one identity would otherwise be one position of the result (#88).
func ResolvePolicy(object *Node, root Shape, policy PolicySurface, surface string, a []string, g Grammar, unknown []string) (PolicyResolution, error) {
	w := &resolveWalker{a: a, g: g, graph: PolicyGraph{}}
	state := PolicyAbsent
	if policy.Declared {
		state = PolicyDeclared
	}
	rootObj := &PolicyObject{ID: "$", Policy: ObjectPolicy{State: state, Surface: policy}}
	w.graph["$"] = rootObj
	if err := w.object(object, root.Fields, nil, rootObj); err != nil {
		return nil, err
	}
	if err := refuseInvalid(object, root, a); err != nil {
		return nil, err
	}
	for _, u := range unknown {
		o, ok := w.graph[u]
		if !ok {
			return nil, unsupported("%q is marked as an object whose policy is unknown, but the object has no such object", u)
		}
		o.Policy = ObjectPolicy{State: PolicyUnknown}
	}
	out := PolicyResolution{}
	for _, t := range w.targets {
		out[t.path] = PolicyEntry{
			Access: ResolveRule(w.graph, t.object, surface, t.access, t.path, "access"),
			Modify: ResolveRule(w.graph, t.object, surface, t.modify, t.path, "modify"),
		}
	}
	return out, nil
}

type resolveTarget struct {
	path, object   string
	access, modify *PolicyRule
}

type resolveWalker struct {
	a       []string
	g       Grammar
	graph   PolicyGraph
	targets []resolveTarget
}

// object walks the mapping n of the object o: each entry is an expanded position.
func (w *resolveWalker) object(n *Node, fields []NamedShape, path []seg, o *PolicyObject) error {
	if n.Kind != KindMap {
		return unsupported("a %s value where a mapping is declared", kindName(n))
	}
	for _, e := range n.Entries {
		i := slices.IndexFunc(fields, func(f NamedShape) bool { return f.Name == e.Key })
		if i < 0 {
			return unsupported("%q is not a field the schema declares", e.Key)
		}
		fp := append(slices.Clone(path), seg{field: e.Key})
		text, err := pathText(fp)
		if err != nil {
			return err
		}
		if class, _ := classify(e.Value, w.a); class != ClassExpanded {
			return unsupported("%s is not an expanded position: normalize the document first", text)
		}
		car, access, modify, err := w.wrapper(e.Value, text)
		if err != nil {
			return err
		}
		w.targets = append(w.targets, resolveTarget{path: text, object: o.ID, access: access, modify: modify})
		if err := w.below(lookup(e.Value, valueKey), fields[i].Shape, fp, text, o, car, access, modify); err != nil {
			return err
		}
	}
	return nil
}

// wrapper reads the Access wrapper of a position: the mode and the two rule values (nil is the symbol inherit).
func (w *resolveWalker) wrapper(x *Node, text string) (mode Mode, access, modify *PolicyRule, err error) {
	// a key set without one of the three gives a wrapper without it: refused, as in Rust, never read as nil (#88)
	read := func(key string) (*PolicyRule, error) {
		n := lookup(x, key)
		if n == nil {
			return nil, unsupported("%s: the position has no %s", text, key)
		}
		r, v := ReadRule(n)
		if v != nil {
			return nil, unsupported("%s: %s of the position: %s", text, key, v.Code)
		}
		if r.Symbol {
			return nil, nil
		}
		return &PolicyRule{Patterns: r.Patterns}, nil
	}
	if access, err = read("access"); err != nil {
		return
	}
	if modify, err = read("modify"); err != nil {
		return
	}
	inherit := lookup(x, "inherit")
	if inherit == nil {
		err = unsupported("%s: the position has no inherit", text)
		return
	}
	var v *Violation
	if mode, v = ReadMode(inherit, w.g); v != nil {
		err = unsupported("%s: inherit of the position: %s", text, v.Code)
	}
	return
}

// below goes into what a position holds: the object of a composite, the elements of a declared list.
func (w *resolveWalker) below(v *Node, shape Shape, path []seg, text string, parent *PolicyObject, mode Mode, access, modify *PolicyRule) error {
	switch {
	case v.Kind == KindNull:
		return nil
	case shape.Kind == ShapeComposite:
		child := w.child(text, parent, mode, access, modify, text)
		return w.object(v, shape.Fields, path, child)
	case shape.Kind == ShapeDeclaredList:
		if shape.Elements == nil || v.Kind != KindSeq {
			return unsupported("%s: a list with no element structure, or a value that is not a list", text)
		}
		for _, rec := range v.Items {
			if rec.Kind != KindMap {
				return unsupported("%s: a list element that is not a record", text)
			}
			c, id, code, _ := identify(rec, shape.Elements, w.a)
			if code != "" {
				return unsupported("%s: a list element without an identity (%s): validate the document first", text, code)
			}
			ep := append(slices.Clone(path), seg{item: id, isItem: true})
			etext, _ := pathText(ep) // the path above it was written already, and an identity is always writable
			// an element inherits from its list field and its object, never from a sibling
			child := w.child(etext, parent, mode, access, modify, text)
			if err := w.object(rec, c.Fields, ep, child); err != nil {
				return err
			}
		}
	}
	return nil
}

func (w *resolveWalker) child(id string, parent *PolicyObject, mode Mode, access, modify *PolicyRule, carrierPath string) *PolicyObject {
	o := &PolicyObject{ID: id, Parent: parent.ID, HasParent: true, CarrierPath: carrierPath,
		Carrier: &Carrier{Mode: mode, Access: access, Modify: modify}}
	w.graph[id] = o
	return o
}
