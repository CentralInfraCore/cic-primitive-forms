package forms

// Normalize and Denormalize (P3, third unit; docs/P3-UNIT3-COMPOSITION.md).
//
// Normalize is Expand, then the declared default of every absent optional field inserted into the expanded tree. The author's
// own writing is recorded by Expand (Provenance, how each position was written) and the origin of each value (authored or
// default) is recorded where the default is inserted, in a side map: neither is read back out of the values. An inserted
// default is an Access atom like any position (the symbolic policy of the bound grammar, default_injection null, ADR 0003 rule 6)
// and has no Provenance entry. Denormalize is the inverse: it removes the values whose origin is default and collapses with
// the Provenance, so that Denormalize(Normalize(x)) is x as a value, for every document that Expand accepts, short, expanded or
// mixed, with explicit policy settings, null, and values that equal their default.
//
// Like Expand, Normalize expects a document that validates in the sense of the schema (it refuses an undeclared key and a
// structure the schema does not allow, but does not report a missing mandatory field) and the trusted Context of ADR 0010.
// It never inserts a default for a mandatory field, into an absent composite or list, or over a present value.

import "slices"

// NormalizationResult is what Normalize produces: the object, how the author wrote it, and who made each value.
type NormalizationResult struct {
	// Object is the expanded document with the defaults inserted. It is not a pure data tree to be processed without
	// ValueOrigins: the two travel together.
	Object *Node
	// Provenance is the P2 record, only for the positions the author wrote. An inserted position has none.
	Provenance Provenance
	// ValueOrigins lists every declared position of Object in path order, authored or default (see ApplyDefaults).
	ValueOrigins []ValueOrigin
}

func originsOf(fs []found) []ValueOrigin {
	slices.SortStableFunc(fs, func(x, y found) int { return slices.Compare(x.keys(), y.keys()) })
	out := make([]ValueOrigin, 0, len(fs))
	for _, f := range fs {
		out = append(out, ValueOrigin{Path: f.v.LogicalPath, Origin: f.v.Code})
	}
	return out
}

// Normalize expands doc and inserts the declared defaults of its absent optional fields. The input is not modified.
func Normalize(doc *Node, shape Shape, a []string, g Grammar, ctx Context) (*NormalizationResult, error) {
	if err := ctx.check(shape); err != nil {
		return nil, err
	}
	w := &formWalker{a: a, g: g, prov: Provenance{}, defaults: true}
	out, err := w.record(doc, shape, nil, true)
	if err != nil {
		return nil, err
	}
	return &NormalizationResult{Object: out, Provenance: w.prov, ValueOrigins: originsOf(w.origins)}, nil
}

// Renormalize normalizes a result again, keeping what it knows: how the author wrote each position and which values the
// normalization made. The object of a result is already expanded, so a plain Normalize of it would report every position as
// authored expanded and every inserted default as authored; this one returns the same object, the same Provenance and the same
// ValueOrigins (idempotence is a property of the pair, not of the object alone). A position that the result does not know is
// reported as authored: it is written in the object.
func Renormalize(prior *NormalizationResult, shape Shape, a []string, g Grammar, ctx Context) (*NormalizationResult, error) {
	again, err := Normalize(prior.Object, shape, a, g, ctx)
	if err != nil {
		return nil, err
	}
	origin := map[string]string{}
	for _, o := range prior.ValueOrigins {
		origin[o.Path] = o.Origin
	}
	prov := Provenance{}
	origins := make([]ValueOrigin, 0, len(again.ValueOrigins))
	for _, o := range again.ValueOrigins {
		kept, known := origin[o.Path]
		if !known {
			kept = o.Origin
		}
		origins = append(origins, ValueOrigin{Path: o.Path, Origin: kept})
		if kept == ValueOriginDefault {
			continue // the author did not write it: no provenance
		}
		if p, ok := prior.Provenance[o.Path]; ok {
			prov[o.Path] = p
		} else {
			prov[o.Path] = again.Provenance[o.Path]
		}
	}
	return &NormalizationResult{Object: again.Object, Provenance: prov, ValueOrigins: origins}, nil
}

// Denormalize gives back the authored document: the values the normalization made are removed, then the object is collapsed
// with the Provenance and the trusted Context (ADR 0001, ADR 0010). The result is a value-equal copy of what was normalized.
func Denormalize(res *NormalizationResult, shape Shape, a []string, g Grammar, ctx Context) (*Node, error) {
	if err := ctx.check(shape); err != nil {
		return nil, err
	}
	w := &formWalker{a: a, g: g, prov: res.Provenance, attested: map[string]bool{}, strip: map[string]bool{}}
	for _, p := range ctx.AttestedPositions {
		w.attested[p] = true
	}
	for _, o := range res.ValueOrigins {
		if o.Origin == ValueOriginDefault {
			w.strip[o.Path] = true
		}
	}
	return w.record(res.Object, shape, nil, false)
}
