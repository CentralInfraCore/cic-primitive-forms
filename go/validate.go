package forms

import "slices"

// Presence is the presence axis of the Shape: mandatory or optional.
type Presence int

// The two presences.
const (
	Mandatory Presence = iota
	Optional
)

// ScalarType is a scalar_type this slice checks.
type ScalarType int

// The scalar types.
const (
	ScalarString ScalarType = iota
	ScalarInteger
	ScalarBoolean
)

func (t ScalarType) String() string {
	return [...]string{"string", "integer", "boolean"}[t]
}

// ShapeKind is the shape_type and what it needs.
type ShapeKind int

// The shape types this slice checks.
const (
	// ShapeScalar has a ScalarType.
	ShapeScalar ShapeKind = iota
	// ShapeComposite has Fields, checked in the order given.
	ShapeComposite
	// ShapeList is a list; its elements are not described by this slice. MinItems is checked when
	// HasMinItems is set.
	ShapeList
	// ShapeOpaque is any value, nothing below it.
	ShapeOpaque
	// ShapeSet is a collection/set: a list of scalars of type Scalar, no key. MinItems is checked
	// when HasMinItems is set.
	ShapeSet
	// ShapeDeclaredList is a list whose element structure the schema declares (item_fields or
	// item_choice). The elements are not validated by this slice: a non-empty value is an
	// Unsupported error, never a silent pass. MinItems is checked when HasMinItems is set.
	ShapeDeclaredList
)

// NamedShape is one field of a composite.
type NamedShape struct {
	Name  string
	Shape Shape
}

// Shape is a field descriptor: the members of a nodes entry that presence validation reads.
type Shape struct {
	Kind        ShapeKind
	Scalar      ScalarType
	Fields      []NamedShape
	MinItems    int
	HasMinItems bool
	Presence    Presence
	Nullable    bool
	// HasDefault says only whether a default is declared: it is never applied.
	HasDefault bool
}

// Validation is the result of validating one document (or its absence) against a shape.
type Validation struct {
	// Violations is the machine response; empty means valid.
	Violations []Violation
	// DefaultCandidates are the canonical paths ("$" for the root) of absent optional fields that
	// declare a default, in path order. Candidates only: nothing is substituted.
	DefaultCandidates []string
}

type walk struct {
	a           []string
	recognition []found
	validation  []found
	candidates  []found
	expanded    bool
}

func (w *walk) add(path []seg, code string, at Location) {
	w.validation = append(w.validation, found{path, Violation{Code: code, Phase: PhaseValidate, Location: at}})
}

// visit validates node (nil is an absent field). keyPos and parentPos are where the field's key
// and its parent mapping start.
func (w *walk) visit(node *Node, shape Shape, path []seg, keyPos, parentPos *Pos) error {
	if node == nil {
		w.absent(shape, path, parentPos)
		return nil
	}
	if node.Kind == KindNull {
		if !shape.Nullable {
			// An empty scalar has no position of its own: the key is the anchor.
			at := node.Pos
			if at == nil {
				at = keyPos
			}
			w.add(path, "NULL_NOT_ALLOWED", loc(at))
		}
		return nil
	}
	class, v := classify(node, w.a)
	switch class {
	case ClassError:
		w.recognition = append(w.recognition, found{path, *v})
		return nil
	case ClassExpanded:
		w.expanded = true
		return nil
	}
	switch shape.Kind {
	case ShapeScalar:
		return w.scalar(node, shape.Scalar, path)
	case ShapeList:
		return w.list(node, shape, path)
	case ShapeSet:
		return w.set(node, shape, path)
	case ShapeDeclaredList:
		return w.declaredList(node, shape, path)
	case ShapeComposite:
		return w.composite(node, shape.Fields, path)
	}
	return nil
}

func (w *walk) absent(shape Shape, path []seg, parentPos *Pos) {
	if shape.Presence == Mandatory {
		w.add(path, "MISSING_MANDATORY", loc(parentPos))
	} else if shape.HasDefault {
		text := "$"
		if len(path) > 0 {
			text = logicalPath(path)
		}
		if text != "" {
			w.candidates = append(w.candidates, found{path: path, v: Violation{LogicalPath: text}})
		}
	}
}

func kindName(n *Node) string {
	return [...]string{"null", "boolean", "integer", "string", "list", "mapping"}[n.Kind]
}

func (w *walk) scalar(node *Node, t ScalarType, path []seg) error {
	if node.Kind == KindMap {
		w.add(path, "MAP_AT_SCALAR_POSITION", loc(node.Pos))
		return nil
	}
	fits := (node.Kind == KindStr && t == ScalarString) ||
		(node.Kind == KindInt && t == ScalarInteger) ||
		(node.Kind == KindBool && t == ScalarBoolean)
	if !fits {
		return unsupported("a %s value where %s is declared (no code in the registry)", kindName(node), t)
	}
	return nil
}

func (w *walk) list(node *Node, shape Shape, path []seg) error {
	if node.Kind != KindSeq {
		return unsupported("a %s value where a list is declared (no code in the registry)", kindName(node))
	}
	if shape.HasMinItems && len(node.Items) < shape.MinItems {
		w.add(path, "MIN_ITEMS_NOT_MET", loc(node.Pos))
	}
	return nil
}

func (w *walk) set(node *Node, shape Shape, path []seg) error {
	if node.Kind != KindSeq {
		return unsupported("a %s value where a set is declared (no code in the registry)", kindName(node))
	}
	if shape.HasMinItems && len(node.Items) < shape.MinItems {
		w.add(path, "MIN_ITEMS_NOT_MET", loc(node.Pos))
	}
	for _, it := range node.Items {
		if err := w.scalar(it, shape.Scalar, path); err != nil {
			return err
		}
	}
	return nil
}

func (w *walk) declaredList(node *Node, shape Shape, path []seg) error {
	if err := w.list(node, shape, path); err != nil {
		return err
	}
	if len(node.Items) > 0 {
		return unsupported("validation of the elements of a list with a declared element structure (item_fields, item_choice) is outside this slice")
	}
	return nil
}

func (w *walk) composite(node *Node, fields []NamedShape, path []seg) error {
	if node.Kind != KindMap {
		return unsupported("a %s value where a mapping is declared (no code in the registry)", kindName(node))
	}
	for _, f := range fields {
		var value *Node
		var keyPos *Pos
		for _, e := range node.Entries {
			if e.Key == f.Name {
				value, keyPos = e.Value, e.KeyPos
				break
			}
		}
		fp := append(slices.Clone(path), seg{field: f.Name})
		if err := w.visit(value, f.Shape, fp, keyPos, node.Pos); err != nil {
			return err
		}
	}
	for _, e := range node.Entries {
		if !slices.ContainsFunc(fields, func(f NamedShape) bool { return f.Name == e.Key }) {
			w.add(append(slices.Clone(path), seg{field: e.Key}), "UNKNOWN_FIELD", loc(e.KeyPos))
		}
	}
	return nil
}

// Validate validates node (nil is an absent field) against shape. a is the expanded key set A
// that recognition needs.
//
// Absent is not a value: nil is a missing field and a Node of KindNull is a field present with
// the value null, with different answers (MISSING_MANDATORY against NULL_NOT_ALLOWED). A default is
// a candidate and nothing more: an absent optional field that declares one is listed, nothing is
// applied and Role eligibility is not decided (P3). A missing mandatory field is anchored at its
// parent, an explicit null at the value and an empty scalar, which has no text, at the key. The
// response is every error of the phase ordered by canonical logical path, then code. Recognition
// runs first at every node: an EXPANDED_WITH_EXTRA_KEY makes the response a recognition one.
//
// Outside the slice, each an *Error of kind ErrUnsupported rather than a guess: a value of the
// wrong type that has no code in the registry, an expanded Access node in the validated document
// (P2), and the scalar types other than string, integer and boolean. The input is not modified.
func Validate(node *Node, shape Shape, a []string) (*Validation, error) {
	w := &walk{a: a}
	if err := w.visit(node, shape, nil, nil, nil); err != nil {
		return nil, err
	}
	if w.expanded {
		return nil, unsupported("an expanded Access node inside a validated document (P2)")
	}
	slices.SortStableFunc(w.candidates, func(x, y found) int { return slices.Compare(x.keys(), y.keys()) })
	out := &Validation{DefaultCandidates: []string{}}
	for _, c := range w.candidates {
		out.DefaultCandidates = append(out.DefaultCandidates, c.v.LogicalPath)
	}
	if len(w.recognition) > 0 {
		out.Violations = sorted(w.recognition)
	} else {
		out.Violations = sorted(w.validation)
	}
	return out, nil
}
