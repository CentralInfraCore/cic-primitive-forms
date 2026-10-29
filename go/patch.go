package forms

import "slices"

// Patch (P4, step P4-2; ADR 0013 and docs/P4-DIFF-PATCH.md): the first unit of applying a diff to a document, text in and text out.
//
// It applies only `value_change` events on existing primitive scalars of A, each as EditScalar (ADR 0012), so every refusal of that operation keeps its code and
// its phase. Everything else in a diff is refused, as a whole, with PATCH_UNSUPPORTED_EVENT. The checks come in the fixed order of ADR 0013, section 3, all of
// rows 1-6 before the first byte changes; on any error no text is returned. A patch is not an authorization: it does not know who may apply a diff, and an empty diff
// is neither evidence of provenance nor of authorization.
//
// What it verifies is A under the schema, that the context matches the schema it was given, that the version of the diff is 1, that every path resolves, that the
// position is there and holds `previous` (type-strict). What it takes from the caller, on trust, is that the diff was made under that context: the bytes of a diff
// carry none (ADR 0008 is not changed). The Rust peer is rust/src/patch.rs.

// The codes of the phase patch (ADR 0013).
const (
	CodePatchContextMismatch  = "PATCH_CONTEXT_MISMATCH"
	CodePatchUnsupportedEvent = "PATCH_UNSUPPORTED_EVENT"
	CodePatchDuplicateTarget  = "PATCH_DUPLICATE_TARGET"
	CodePatchTargetAbsent     = "PATCH_TARGET_ABSENT"
	CodePatchConflict         = "PATCH_CONFLICT"
)

// PhasePatch is the phase of Patch itself (ADR 0013).
const PhasePatch Phase = "patch"

// PatchDiff is a diff as the patch reads it: the version of its canonical bytes and its events in the canonical order of ADR 0008. Representation entries are not
// events and are not passed.
type PatchDiff struct {
	Version int
	Changes []DiffEvent
}

// PatchSpan is where a replaced scalar was in A and where it is in the result.
type PatchSpan struct {
	Path          string
	Before, After Span
}

// PatchResult is a successful patch: the new text, the paths of the scalars it replaced in the order of the diff, and their spans.
type PatchResult struct {
	Text     string
	Replaced []string
	Spans    []PatchSpan
}

func patchFailure(code, path string, at *Pos) *Error {
	loc := Location{NotApplicable: true}
	if at != nil {
		loc = Location{Available: true, Pos: *at}
	}
	return &Error{Kind: ErrContract, Violations: []Violation{{Code: code, Phase: PhasePatch, Location: loc, LogicalPath: path}}}
}

// isKeyComponent says whether segs, the path of a scalar position, ends at a key field of a list element: the element selector is the segment before the
// last one, and the last one names a field of the key of the case it selects.
func isKeyComponent(root Shape, segs []PathSeg) bool {
	fields := root.Fields
	var elements *Elements
	var c *Case
	for _, s := range segs[:len(segs)-1] {
		if !s.IsItem {
			i := slices.IndexFunc(fields, func(f NamedShape) bool { return f.Name == s.Field })
			elements, fields, c = fields[i].Shape.Elements, fields[i].Shape.Fields, nil
			continue
		}
		names := make([]string, len(s.Item))
		for j, k := range s.Item {
			names[j] = k.Name
		}
		i := slices.IndexFunc(elements.Cases, func(cs Case) bool { return slices.Equal(names, cs.Key) })
		c = &elements.Cases[i]
		fields = c.Fields
	}
	return c != nil && slices.Contains(c.Key, segs[len(segs)-1].Field)
}

// Patch applies d to text. schema is the context of the schema the caller passes root for, context the one the caller says d was made under; they must be equal
// (PATCH_CONTEXT_MISMATCH), which is all that can be verified of it. a is the expanded key set and g the bound grammar, as everywhere.
func Patch(text string, root Shape, a []string, g Grammar, schema, context DiffContext, d PatchDiff) (*PatchResult, error) {
	doc, err := Parse(text)
	if err != nil {
		return nil, err
	}
	if err := refuseInvalid(doc, root, a); err != nil {
		return nil, err
	}
	if schema != context || d.Version != 1 {
		return nil, patchFailure(CodePatchContextMismatch, "", nil)
	}
	parsed := make([][]PathSeg, len(d.Changes))
	unresolved := make([]*PathError, len(d.Changes))
	for i, e := range d.Changes {
		// (a value change that does not carry both values is not the event section 2 describes)
		if e.Kind != EventValueChange || e.Previous == nil || e.Next == nil {
			return nil, patchFailure(CodePatchUnsupportedEvent, e.Path, nil)
		}
		segs, perr := ParsePath(e.Path)
		if perr == nil {
			var res Resolved
			if res, perr = ResolvePath(root, segs); perr == nil && (res.Target != "scalar" || isKeyComponent(root, segs)) {
				return nil, patchFailure(CodePatchUnsupportedEvent, e.Path, nil)
			}
		}
		parsed[i], unresolved[i] = segs, perr
	}
	// (row 3a: a path that two events name is refused whatever the events say; the paths are canonical, so the text is the position)
	seen := make(map[string]struct{}, len(d.Changes))
	for _, e := range d.Changes {
		if _, dup := seen[e.Path]; dup {
			return nil, patchFailure(CodePatchDuplicateTarget, e.Path, nil)
		}
		seen[e.Path] = struct{}{}
	}
	for _, perr := range unresolved {
		if perr != nil {
			return nil, pathFailure(perr)
		}
	}
	for i, e := range d.Changes {
		f := locate(doc, root, parsed[i], a)
		// (the document holds a position that is spelled with no value, `a:`, as the null it means; replacing it is the edit's refusal, not an absence)
		if f.value == nil {
			return nil, patchFailure(CodePatchTargetAbsent, e.Path, f.parent.Pos)
		}
		if !f.value.Same(e.Previous) {
			at := f.value.Pos
			if at == nil {
				at = f.parent.Pos
			}
			return nil, patchFailure(CodePatchConflict, e.Path, at)
		}
	}
	out := &PatchResult{Text: text, Replaced: []string{}, Spans: []PatchSpan{}}
	for _, e := range d.Changes {
		r, err := EditScalar(out.Text, root, e.Path, a, e.Next)
		if err != nil {
			return nil, err
		}
		out.Text = r.Text
		out.Replaced = append(out.Replaced, e.Path)
	}
	for _, p := range out.Replaced {
		// (these cannot fail: the checks above found the position and the edit replaced the scalar that is there)
		before, _ := SourceSpan(text, root, p, a)
		after, _ := SourceSpan(out.Text, root, p, a)
		out.Spans = append(out.Spans, PatchSpan{Path: p, Before: before, After: after})
	}
	return out, nil
}
