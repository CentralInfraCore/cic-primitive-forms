package forms

import (
	"cmp"
	"slices"
	"strings"
)

// The comparison of two expanded views (see diff.go). Both trees are the output of Expand: every primitive position is a wrapper with its
// value and its declaration fields, a list element is a record of such positions.

// slot is a place of an expanded tree: a position (a wrapper, with the shape of the field) or a record (the root, or a list element, with a
// composite shape of its fields).
type slot struct {
	node   *Node
	shape  Shape
	record bool
}

// fieldsOf is the fields below a slot and the record that holds them: the record itself, or the value of a composite position. ok is false
// for a position that holds a value other than a mapping.
func (s slot) fieldsOf() (rec *Node, ok bool) {
	if s.record {
		return s.node, true
	}
	v := entry(s.node, valueKey)
	return v, v != nil && v.Kind == KindMap
}

// descend walks the path below s in an expanded tree. found is false when the tree does not have the place; a place below a null or
// otherwise unfit value is refused (not guessed).
func descend(s slot, path []seg, a []string) (slot, bool, error) {
	for _, sg := range path {
		if !sg.isItem {
			rec, ok := s.fieldsOf()
			if !ok {
				return slot{}, false, unsupported("a path below a value that is not a mapping")
			}
			e := entry(rec, sg.field)
			if e == nil {
				return slot{}, false, nil
			}
			i := slices.IndexFunc(s.shape.Fields, func(f NamedShape) bool { return f.Name == sg.field })
			s = slot{node: e, shape: s.shape.Fields[i].Shape}
			continue
		}
		list := entry(s.node, valueKey)
		if list == nil || list.Kind != KindSeq || s.shape.Elements == nil {
			return slot{}, false, unsupported("a list element below a value that is not a list")
		}
		var next *slot
		for _, it := range list.Items {
			c, id, code, _ := identify(it, s.shape.Elements, a)
			if code == "" && (seg{item: id, isItem: true}).key() == sg.key() {
				next = &slot{node: it, shape: Shape{Kind: ShapeComposite, Fields: c.Fields}, record: true}
				break
			}
		}
		if next == nil {
			return slot{}, false, nil
		}
		s = *next
	}
	return s, true, nil
}

type differ struct {
	a            []string
	provA, provB Provenance
	result       *DiffResult
}

func (d *differ) emit(e DiffEvent, path []seg) {
	e.order = keysOf(path)
	d.result.Changes = append(d.result.Changes, e)
}

func keysOf(path []seg) []string {
	out := make([]string, len(path))
	for i, s := range path {
		out[i] = s.key()
	}
	return out
}

// sort puts the events in the order of ADR 0008: the path order of ADR 0007, then the kind bytewise; the writing differences by path.
func (d *differ) sort() {
	slices.SortStableFunc(d.result.Changes, func(x, y DiffEvent) int {
		return cmp.Or(slices.Compare(x.order, y.order), strings.Compare(x.Kind, y.Kind))
	})
	slices.SortStableFunc(d.result.Representation, func(x, y RepresentationChange) int { return slices.Compare(x.order, y.order) })
}

// slots compares one scope entry: the place in A and the place in B. A place that only one side has appears or disappears as one event.
func (d *differ) slots(path []seg, sa slot, fa bool, sb slot, fb bool) error {
	switch {
	case fa && fb:
		if sa.record {
			return d.record(sa.shape.Fields, sa.node, sb.node, path)
		}
		return d.position(sa.shape, sa.node, sb.node, path)
	case fa:
		return d.gone(EventRemoved, sa, path)
	case fb:
		return d.gone(EventAdded, sb, path)
	}
	return nil
}

// gone is the event of a place that exists on one side only, with its fragment.
func (d *differ) gone(kind string, s slot, path []seg) error {
	text, err := pathText(path)
	if err != nil {
		return err
	}
	var frag *Node
	if s.record {
		frag = d.recordFragment(s.node, s.shape.Fields)
	} else {
		frag = d.positionFragment(s.node, s.shape)
	}
	e := DiffEvent{Kind: kind, Path: text}
	if kind == EventAdded {
		e.Next = frag
	} else {
		e.Previous = frag
	}
	d.emit(e, path)
	return nil
}

// record compares the declared fields of two records.
func (d *differ) record(fields []NamedShape, ra, rb *Node, path []seg) error {
	for _, f := range fields {
		fp := append(slices.Clone(path), seg{field: f.Name})
		na, nb := entry(ra, f.Name), entry(rb, f.Name)
		var err error
		switch {
		case na != nil && nb != nil:
			err = d.position(f.Shape, na, nb, fp)
		case na != nil:
			err = d.gone(EventRemoved, slot{node: na, shape: f.Shape}, fp)
		case nb != nil:
			err = d.gone(EventAdded, slot{node: nb, shape: f.Shape}, fp)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

// position compares one primitive position present in both views: its declaration, its writing, its value.
func (d *differ) position(shape Shape, na, nb *Node, path []seg) error {
	text, err := pathText(path)
	if err != nil {
		return err
	}
	var changed []DeclChange
	for _, k := range d.a {
		if k == valueKey {
			continue
		}
		if pa, pb := entry(na, k), entry(nb, k); !pa.Same(pb) {
			changed = append(changed, DeclChange{Field: k, Previous: pa, Next: pb})
		}
	}
	if len(changed) > 0 {
		slices.SortFunc(changed, func(x, y DeclChange) int { return strings.Compare(x.Field, y.Field) })
		d.emit(DiffEvent{Kind: EventPolicyDeclarationChange, Path: text, Changed: changed}, path)
	}
	if wa, wb := d.provA[text].AuthoredExpanded, d.provB[text].AuthoredExpanded; wa != wb {
		d.result.Representation = append(d.result.Representation, RepresentationChange{Path: text, Previous: writing(wa), Next: writing(wb), order: keysOf(path)})
	}
	return d.value(shape, entry(na, valueKey), entry(nb, valueKey), text, path)
}

func writing(expanded bool) string {
	if expanded {
		return "expanded"
	}
	return "short"
}

// value compares what two positions hold.
func (d *differ) value(shape Shape, va, vb *Node, text string, path []seg) error {
	switch shape.Kind {
	case ShapeScalar, ShapeOpaque:
		if !va.Same(vb) {
			d.emit(DiffEvent{Kind: EventValueChange, Path: text, Previous: va, Next: vb}, path)
		}
		return nil
	}
	if va.Kind == KindNull || vb.Kind == KindNull {
		if va.Kind == vb.Kind {
			return nil
		}
		return unsupported("a null against a collection (no contract for it)")
	}
	switch shape.Kind {
	case ShapeSet:
		added, removed := setDifference(va, vb)
		if len(added) > 0 || len(removed) > 0 {
			d.emit(DiffEvent{Kind: EventSetChange, Path: text, Added: added, Removed: removed}, path)
		}
		return nil
	case ShapeComposite:
		return d.record(shape.Fields, va, vb, path)
	case ShapeDeclaredList:
		return d.list(shape, va, vb, path)
	}
	return unsupported("a list that declares no elements (no contract for it)")
}

// element is a list element with its identity, in the order the declaration gives (the case first).
type element struct {
	node  *Node
	c     *Case
	id    []KeyComp
	order int
}

func (d *differ) elements(list *Node, shape Shape) []element {
	out := make([]element, 0, len(list.Items))
	for _, it := range list.Items {
		c, id, _, _ := identify(it, shape.Elements, d.a)
		out = append(out, element{node: it, c: c, id: id, order: slices.IndexFunc(shape.Elements.Cases, func(o Case) bool { return o.Name == c.Name })})
	}
	return out
}

func compareElements(x, y element) int {
	if c := cmp.Compare(x.order, y.order); c != 0 {
		return c
	}
	return strings.Compare(seg{item: x.id, isItem: true}.key(), seg{item: y.id, isItem: true}.key())
}

// list pairs the elements of two lists by identity.
func (d *differ) list(shape Shape, va, vb *Node, path []seg) error {
	ea, eb := d.elements(va, shape), d.elements(vb, shape)
	for _, x := range ea {
		p := append(slices.Clone(path), seg{item: x.id, isItem: true})
		i := slices.IndexFunc(eb, func(y element) bool { return compareElements(x, y) == 0 })
		if i < 0 {
			if err := d.gone(EventRemoved, slot{node: x.node, shape: Shape{Kind: ShapeComposite, Fields: x.c.Fields}, record: true}, p); err != nil {
				return err
			}
			continue
		}
		if err := d.record(x.c.Fields, x.node, eb[i].node, p); err != nil {
			return err
		}
	}
	for _, y := range eb {
		if !slices.ContainsFunc(ea, func(x element) bool { return compareElements(x, y) == 0 }) {
			p := append(slices.Clone(path), seg{item: y.id, isItem: true})
			if err := d.gone(EventAdded, slot{node: y.node, shape: Shape{Kind: ShapeComposite, Fields: y.c.Fields}, record: true}, p); err != nil {
				return err
			}
		}
	}
	return nil
}

// rank orders the kinds of a set element: null, booleans, integers, strings.
func rank(n *Node) int {
	return slices.Index([]Kind{KindNull, KindBool, KindInt, KindStr}, n.Kind)
}

// compareScalars is the order of ADR 0008 for set elements: by kind rank, then by value (false before true, integers numerically, strings by UTF-8 bytes).
func compareScalars(x, y *Node) int {
	if c := cmp.Compare(rank(x), rank(y)); c != 0 {
		return c
	}
	switch x.Kind {
	case KindBool:
		return cmp.Compare(boolInt(x.Bool), boolInt(y.Bool))
	case KindInt:
		return cmp.Compare(x.Int, y.Int)
	case KindStr:
		return strings.Compare(x.Str, y.Str)
	}
	return 0
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func sortedScalars(items []*Node) []*Node {
	out := slices.Clone(items)
	slices.SortStableFunc(out, compareScalars)
	return slices.CompactFunc(out, func(x, y *Node) bool { return compareScalars(x, y) == 0 })
}

// setDifference is the elements of b that a lacks and of a that b lacks, sorted.
func setDifference(a, b *Node) (added, removed []*Node) {
	added, removed = []*Node{}, []*Node{}
	for _, x := range sortedScalars(b.Items) {
		if !slices.ContainsFunc(a.Items, x.Same) {
			added = append(added, x)
		}
	}
	for _, x := range sortedScalars(a.Items) {
		if !slices.ContainsFunc(b.Items, x.Same) {
			removed = append(removed, x)
		}
	}
	return added, removed
}

// positionFragment is the expanded logical fragment of a position: its wrapper, with what it holds in the order of ADR 0008 (the elements of a
// list by identity, the elements of a set sorted).
func (d *differ) positionFragment(n *Node, shape Shape) *Node {
	out := &Node{Kind: KindMap, Entries: make([]Entry, len(n.Entries))}
	for i, e := range n.Entries {
		out.Entries[i] = Entry{Key: e.Key, Value: e.Value}
		if e.Key == valueKey {
			out.Entries[i].Value = d.valueFragment(e.Value, shape)
		}
	}
	return out
}

func (d *differ) valueFragment(v *Node, shape Shape) *Node {
	if v.Kind == KindNull {
		return v
	}
	switch shape.Kind {
	case ShapeComposite:
		return d.recordFragment(v, shape.Fields)
	case ShapeSet:
		return &Node{Kind: KindSeq, Items: sortedScalars(v.Items)}
	case ShapeDeclaredList:
		els := d.elements(v, shape)
		slices.SortStableFunc(els, compareElements)
		out := &Node{Kind: KindSeq, Items: make([]*Node, len(els))}
		for i, x := range els {
			out.Items[i] = d.recordFragment(x.node, x.c.Fields)
		}
		return out
	}
	return v
}

func (d *differ) recordFragment(rec *Node, fields []NamedShape) *Node {
	out := &Node{Kind: KindMap, Entries: make([]Entry, 0, len(rec.Entries))}
	for _, f := range fields {
		if e := entry(rec, f.Name); e != nil {
			out.Entries = append(out.Entries, Entry{Key: f.Name, Value: d.positionFragment(e, f.Shape)})
		}
	}
	return out
}
