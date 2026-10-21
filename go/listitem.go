package forms

// The identity of a list element (ADR 0002 "List identity", ADR 0006 "Building a path from a document").
//
// The identity comes only from the pinned schema: the declared key (role: key / item_key, or the key of
// the item_choice case), never from the names of the fields and never from the position in the list. A case
// of an item_choice is selected by field presence on the raw authored record: it matches when every key of
// the record is a declared field of the case and every mandatory field of the case, key fields included,
// is present; zero or several matches is an error. An Access wrapper around a key value is looked
// through, so the short and the expanded form give the same identity.

// KeyComp is one component of an element identity: the name of the key field and a typed value, a string
// or an integer (ADR 0006: no other key type has an encoding).
type KeyComp struct {
	Name  string
	IsInt bool
	Str   string
	Int   int64
}

// The codes of an element whose identity cannot be built or is not unique. They are the path_of codes of
// the corpus registry, reported in the validate phase too (ADR 0007).
const (
	CodeKeyMissing     = "PATH_KEY_MISSING"
	CodeKeyType        = "PATH_KEY_TYPE"
	CodeKeyUnsupported = "PATH_KEY_UNSUPPORTED"
	CodeCaseNone       = "PATH_CASE_NONE"
	CodeCaseAmbiguous  = "PATH_CASE_AMBIGUOUS"
	CodeDuplicateIdent = "PATH_DUPLICATE_IDENTITY"
	valueKey           = "value"
)

// through looks through an expanded Access wrapper: the node whose value is meant. ok is false for a
// wrapper that has no value entry (not an identity).
func through(n *Node, a []string) (*Node, bool) {
	if class, _ := classify(n, a); class == ClassExpanded {
		for _, e := range n.Entries {
			if e.Key == valueKey {
				return e.Value, true
			}
		}
		return nil, false
	}
	return n, true
}

func entry(n *Node, key string) *Node {
	for _, e := range n.Entries {
		if e.Key == key {
			return e.Value
		}
	}
	return nil
}

func fieldOf(c *Case, name string) (Shape, bool) {
	for _, f := range c.Fields {
		if f.Name == name {
			return f.Shape, true
		}
	}
	return Shape{}, false
}

// matches is the ADR 0002 case rule for a record.
func (c *Case) matches(rec *Node) bool {
	for _, e := range rec.Entries {
		if _, ok := fieldOf(c, e.Key); !ok {
			return false
		}
	}
	for _, f := range c.Fields {
		required := f.Shape.Presence == Mandatory
		for _, k := range c.Key {
			required = required || k == f.Name
		}
		if required && entry(rec, f.Name) == nil {
			return false
		}
	}
	return true
}

// identify selects the case of a record and builds its identity. code is "" on success, else the
// error code (CodeKey*, CodeCase*); the case is nil then. at is the node a source position is taken from
// (ADR 0007): the key value for a key of the wrong or an unsupported type, the record for every other error.
func identify(rec *Node, els *Elements, a []string) (c *Case, id []KeyComp, code string, at *Node) {
	if els.Choice {
		for i := range els.Cases {
			if els.Cases[i].matches(rec) {
				if c != nil {
					return nil, nil, CodeCaseAmbiguous, rec
				}
				c = &els.Cases[i]
			}
		}
		if c == nil {
			return nil, nil, CodeCaseNone, rec
		}
	} else {
		c = &els.Cases[0]
	}
	id = make([]KeyComp, 0, len(c.Key))
	for _, name := range c.Key {
		raw := entry(rec, name)
		if raw == nil {
			return nil, nil, CodeKeyMissing, rec
		}
		shape, _ := fieldOf(c, name)
		if shape.Kind != ShapeScalar || shape.Scalar == ScalarBoolean {
			return nil, nil, CodeKeyUnsupported, raw
		}
		v, ok := through(raw, a)
		if !ok {
			return nil, nil, CodeKeyType, raw
		}
		switch {
		case v.Kind == KindStr && shape.Scalar == ScalarString:
			id = append(id, KeyComp{Name: name, Str: v.Str})
		case v.Kind == KindInt && shape.Scalar == ScalarInteger:
			id = append(id, KeyComp{Name: name, IsInt: true, Int: v.Int})
		default:
			return nil, nil, CodeKeyType, raw
		}
	}
	return c, id, "", nil
}

// Step is one step of an address in a document: a field name or a list index. The index only selects
// the element; the path that comes out names it by its identity.
type Step struct {
	Field   string
	Index   int
	IsIndex bool
}

// PathOf is the canonical logical path (ADR 0006) of the node that `at` addresses in doc, read against
// shape (a composite). Access wrappers are looked through on the way, so the short and the expanded form
// give one path; a list element is named by its identity, not by its index.
//
// A failure of the identity is a contract error of phase path_of whose logical path is the list's (or the
// identity's, for a duplicate); an address that does not lead to a node of the document and the schema
// is an Unsupported error.
func PathOf(doc *Node, shape Shape, a []string, at []Step) (string, error) {
	node, cur := doc, shape
	var path []seg
	for i := 0; i < len(at); i++ {
		st := at[i]
		if st.IsIndex {
			return "", unsupported("address step %d: a list index needs the list field before it", i)
		}
		if cur.Kind != ShapeComposite {
			return "", unsupported("address step %d: %q has nothing below it", i, st.Field)
		}
		next := entry(node, st.Field)
		sub, ok := fieldOf(&Case{Fields: cur.Fields}, st.Field)
		if node.Kind != KindMap || next == nil || !ok {
			return "", unsupported("address step %d: %q is not a field of the document and the schema", i, st.Field)
		}
		path = append(path, seg{field: st.Field})
		node, cur = next, sub
		if v, ok := through(node, a); ok {
			node = v
		}
		if cur.Kind != ShapeDeclaredList || i+1 >= len(at) || !at[i+1].IsIndex {
			continue
		}
		i++
		idx := at[i].Index
		if node.Kind != KindSeq || idx < 0 || idx >= len(node.Items) || cur.Elements == nil {
			return "", unsupported("address step %d: no such list element", i)
		}
		rec := node.Items[idx]
		if rec.Kind != KindMap {
			return "", unsupported("address step %d: a list element is not a record", i)
		}
		fail := func(code string, at []seg) error {
			return &Error{Kind: ErrContract, Violations: []Violation{{Code: code, Phase: PhasePathOf, LogicalPath: logicalPath(at)}}}
		}
		c, id, code, _ := identify(rec, cur.Elements, a)
		if code != "" {
			return "", fail(code, path)
		}
		item := seg{item: id, isItem: true}
		for j, other := range node.Items {
			if j == idx || other.Kind != KindMap {
				continue
			}
			_, oid, ocode, _ := identify(other, cur.Elements, a)
			twin := seg{item: oid, isItem: true}
			if ocode == "" && twin.key() == item.key() {
				return "", fail(CodeDuplicateIdent, append(append([]seg{}, path...), item))
			}
		}
		path = append(path, item)
		node, cur = rec, Shape{Kind: ShapeComposite, Fields: c.Fields}
	}
	if len(path) == 0 {
		return "$", nil
	}
	return logicalPath(path), nil
}
