package forms

// Applying the schema defaults to an authored document (P3, second unit; ADR 0003 rules 3 and 4, ADR 0011).
//
// A default applies to an **absent** field and to nothing else: never to an explicit null, "", 0, false, [] or {}.
// Whether a node may take one was decided when the schema was compiled (ADR 0011: authority config, optional, no key,
// reference, derived or volatile; anything else is SCHEMA_INVALID), so a Shape that carries a Default takes it.
//
// Decisions of this unit, written down because the documents do not settle them:
//
//   - The walk goes through the fields that are present: a present composite, and the records of a declared list
//     (named by their declared identity). It never goes into an absent composite or an absent list: defaults do not
//     invent structure, and the defaults of its fields are not applied.
//   - The default of a field is inserted as a copy, in the short form (a bare value), after the entries of its mapping,
//     in schema order. A default is a whole value: nothing below it is defaulted again, and it is origin "default" as a
//     whole. Composing with Expand (the symbolic policy of the position) is the next unit.
//   - The input is validated first. A document with violations, or that this slice does not support, is not defaulted:
//     the violations are returned and no document is produced. The input node is never modified.
//   - ValueOrigin is relative to the input. Applying the defaults to the result again changes nothing (idempotence), but
//     it reports the inserted values as authored, because from then on they are written in the document.

import "slices"

// The two origins of a value (P3 design, section 2). Never "inherited": inheritance concerns policy rules.
const (
	ValueOriginAuthored = "authored"
	ValueOriginDefault  = "default"
)

// ValueOrigin is the origin of one declared position that the output document holds.
type ValueOrigin struct {
	// Path is the canonical logical path (ADR 0006); list elements are named by their identity.
	Path string
	// Origin is ValueOriginAuthored or ValueOriginDefault.
	Origin string
}

// Defaulted is the result of ApplyDefaults.
type Defaulted struct {
	// Violations is the machine response of the validation that ran first. When it is not empty, Document is nil.
	Violations []Violation
	// Document is a copy of the input with the defaults of the absent fields inserted.
	Document *Node
	// Origins lists every declared position of Document, in path order: authored, or default for an inserted value.
	Origins []ValueOrigin
}

// ApplyDefaults validates the authored document against root and inserts the declared default of every absent optional
// field that has one. a is the key set A of the Access atom, as for Validate. Errors are those of Validate (an
// unsupported input is an *Error of kind ErrUnsupported, never a guess).
func ApplyDefaults(doc *Node, root Shape, a []string) (*Defaulted, error) {
	v, err := Validate(doc, root, a)
	if err != nil {
		return nil, err
	}
	if len(v.Violations) > 0 || doc == nil { // doc is nil only for a root that is optional
		return &Defaulted{Violations: v.Violations}, nil
	}
	d := &defaulter{}
	out := cloneTree(doc)
	d.composite(out, root.Fields, nil)
	if d.err != nil {
		return nil, d.err
	}
	slices.SortStableFunc(d.found, func(x, y found) int { return slices.Compare(x.keys(), y.keys()) })
	res := &Defaulted{Document: out, Origins: []ValueOrigin{}}
	for _, f := range d.found {
		res.Origins = append(res.Origins, ValueOrigin{Path: f.v.LogicalPath, Origin: f.v.Code})
	}
	return res, nil
}

type defaulter struct {
	found []found
	// err is set when a default would be inserted at a position whose logical path cannot be written: its origin would have no key.
	err error
}

func (d *defaulter) record(path []seg, origin string) {
	d.found = append(d.found, found{path: path, v: Violation{LogicalPath: logicalPath(path), Code: origin}})
}

// composite walks the mapping n, which the validation accepted, and defaults its absent fields.
func (d *defaulter) composite(n *Node, fields []NamedShape, path []seg) {
	for _, f := range fields {
		fp := append(slices.Clone(path), seg{field: f.Name})
		if value := lookup(n, f.Name); value != nil {
			d.record(fp, ValueOriginAuthored)
			d.visit(value, f.Shape, fp)
		} else if f.Shape.Default != nil { // after the validation an absent field is an optional one
			if _, err := pathText(fp); err != nil {
				d.err = err
				return
			}
			n.Entries = append(n.Entries, Entry{Key: f.Name, Value: cloneTree(f.Shape.Default)})
			d.record(fp, ValueOriginDefault)
		}
	}
}

// visit goes below a present value: a composite, or the records of a declared list. Everything else is a leaf.
func (d *defaulter) visit(value *Node, shape Shape, path []seg) {
	switch {
	case value.Kind == KindNull:
	case shape.Kind == ShapeComposite:
		d.composite(value, shape.Fields, path)
	case shape.Kind == ShapeDeclaredList:
		for _, rec := range value.Items {
			// The validation refused a record without an identity, so there is always one here.
			if c, id, code, _ := identify(rec, shape.Elements, accessAtomKeys); code == "" {
				d.composite(rec, c.Fields, append(slices.Clone(path), seg{item: id, isItem: true}))
			}
		}
	}
}
