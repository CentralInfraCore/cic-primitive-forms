package forms_test

// edit_scalar (P3b, step E3): the guarantees of ADR 0012, section 3, over generated documents written by the emitter. For every scalar of a document
// and a value of the kind the position has: the bytes outside the span are the original's; the result parses to the old tree with that one scalar
// replaced; editing to the value the position already has returns the text byte for byte; two edits of independent positions give the same text in
// either order; and editing back restores the bytes exactly when the original token already was the canonical spelling. The only refusals are the ones
// a value can earn (a control character, a plain scalar that cannot carry the string); EDIT_SPAN_MISMATCH and EDIT_INVALID_RESULT never happen here.
// The Rust peer is `the_edit_of_every_scalar_keeps_the_guarantees`.

import (
	"errors"
	"strconv"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
	"pgregory.net/rapid"
)

// leafType is the scalar type of the position names address in the composite root.
func leafType(root forms.Shape, names []string) (forms.ScalarType, bool) {
	cur := root
	for _, n := range names {
		found := false
		for _, f := range cur.Fields {
			if f.Name == n {
				cur, found = f.Shape, true
				break
			}
		}
		if !found {
			return 0, false
		}
	}
	return cur.Scalar, cur.Kind == forms.ShapeScalar
}

var editStrings = []string{"x", "s7", "alpha beta", "it's", `say "hi"`, "a: b", "#c", " lead", "trail ", "true", "12", "", "é", "tab\there", `back\slash`, "-dash"}

func drawValue(t *rapid.T, typ forms.ScalarType, label string) *forms.Node {
	switch typ {
	case forms.ScalarInteger:
		return &forms.Node{Kind: forms.KindInt, Int: rapid.Int64Range(-1000000, 1000000).Draw(t, label)}
	case forms.ScalarBoolean:
		return &forms.Node{Kind: forms.KindBool, Bool: rapid.Bool().Draw(t, label)}
	}
	return &forms.Node{Kind: forms.KindStr, Str: rapid.SampledFrom(editStrings).Draw(t, label)}
}

// canonicalToken is the spelling an edit writes for the value in a style, written here once more as the oracle of "edit and back".
func canonicalToken(style forms.SpanStyle, v *forms.Node) (string, bool) {
	switch style {
	case forms.SpanSingleQuoted:
		return "'" + strings.ReplaceAll(v.Str, "'", "''") + "'", v.Kind == forms.KindStr
	case forms.SpanDoubleQuoted:
		return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(v.Str) + `"`, v.Kind == forms.KindStr
	}
	switch v.Kind {
	case forms.KindInt:
		return strconv.FormatInt(v.Int, 10), true
	case forms.KindBool:
		return strconv.FormatBool(v.Bool), true
	}
	return v.Str, true
}

func TestPropertyTheEditOfEveryScalarKeepsTheGuarantees(t *testing.T) {
	rapid.Check(t, func(t *rapid.T) {
		root := rootShape(t)
		seed := xorshift(rapid.Uint64Min(1).Draw(t, "seed"))
		text := forms.Emit(conforming(root, &seed, nil, nil))
		doc, err := forms.Parse(text)
		if err != nil {
			t.Fatalf("the emitter writes what the parser reads: %v", err)
		}
		var leaves [][]string
		scalarLeaves(root, nil, &leaves)
		type site struct {
			path string
			span forms.Span
			old  *forms.Node
			typ  forms.ScalarType
		}
		var sites []site
		for _, names := range leaves {
			segs := make([]forms.PathSeg, len(names))
			for i, n := range names {
				segs[i] = forms.PathSeg{Field: n}
			}
			path, perr := forms.FormatPath(segs)
			typ, ok := leafType(root, names)
			if perr != nil || !ok {
				continue
			}
			span, serr := forms.SourceSpan(text, root, path, keySet)
			old := nodeAt(doc, names)
			if serr != nil || old == nil {
				continue
			}
			sites = append(sites, site{path, span, old, typ})
			// the guarantees for one edit
			v := drawValue(t, typ, "value "+path)
			res, eerr := forms.EditScalar(text, root, path, keySet, v)
			if eerr != nil {
				var fe *forms.Error
				if !errors.As(eerr, &fe) || fe.Kind != forms.ErrContract {
					t.Fatalf("%s: outside the contract: %v", path, eerr)
				}
				switch fe.Violations[0].Code {
				case "EDIT_VALUE_NOT_REPRESENTABLE", "EDIT_STYLE_CONFLICT":
				default:
					t.Fatalf("%s: %v for %v in %q", path, fe.Violations[0], v, text)
				}
				if v.Kind != forms.KindStr || v.Str == "x" || v.Str == "s7" { // not vacuous: a short word, a number or a boolean is always written
					t.Fatalf("%s: %v was refused with %v in %q", path, v, fe.Violations[0], text)
				}
			} else {
				out := res.Text
				if !strings.HasPrefix(out, text[:span.Start]) || !strings.HasSuffix(out, text[span.End:]) { // guarantee 1
					t.Fatalf("%s: the untouched bytes changed: %q -> %q", path, text, out)
				}
				parsed, perr := forms.Parse(out)
				if perr != nil {
					t.Fatalf("%s: the result does not parse: %v in %q", path, perr, out)
				}
				if got := nodeAt(parsed, names); got == nil || !got.Same(v) { // guarantee 3, the scalar
					t.Fatalf("%s: the result holds %v, want %v in %q", path, got, v, out)
				}
				if res.Changed != !old.Same(v) {
					t.Fatalf("%s: changed=%v for %v over %v", path, res.Changed, v, old)
				}
				// edit and back (guarantee 6): the original bytes exactly when the original token already was the canonical spelling
				back, berr := forms.EditScalar(out, root, path, keySet, old)
				if berr != nil {
					t.Fatalf("%s: the edit back was refused: %v in %q", path, berr, out)
				}
				if want, ok := canonicalToken(span.Style, old); ok && text[span.Start:span.End] == want && back.Text != text {
					t.Fatalf("%s: edit and back did not restore %q: %q", path, text, back.Text)
				}
				if bp, perr := forms.Parse(back.Text); perr != nil || !bp.Same(doc) {
					t.Fatalf("%s: edit and back is not the original tree: %q", path, back.Text)
				}
			}
			// the no-op: the value the position holds is the text, byte for byte
			same, serr2 := forms.EditScalar(text, root, path, keySet, old)
			if serr2 != nil || same.Text != text || same.Changed {
				t.Fatalf("%s: the edit to the same value changed %q: %v %v", path, text, same.Text, serr2)
			}
		}
		// two edits of independent positions commute
		if len(sites) >= 2 {
			i := rapid.IntRange(0, len(sites)-1).Draw(t, "first")
			j := rapid.IntRange(0, len(sites)-1).Draw(t, "second")
			if i != j {
				vi, vj := drawValue(t, sites[i].typ, "vi"), drawValue(t, sites[j].typ, "vj")
				ab, e1 := editTwice(text, root, sites[i].path, vi, sites[j].path, vj)
				ba, e2 := editTwice(text, root, sites[j].path, vj, sites[i].path, vi)
				if (e1 == nil) != (e2 == nil) {
					t.Fatalf("one order was refused and the other was not: %v %v", e1, e2)
				}
				if e1 == nil && ab != ba {
					t.Fatalf("the edits of %s and %s do not commute:\n%q\n%q", sites[i].path, sites[j].path, ab, ba)
				}
			}
		}
	})
}

func editTwice(text string, root forms.Shape, p1 string, v1 *forms.Node, p2 string, v2 *forms.Node) (string, error) {
	r1, err := forms.EditScalar(text, root, p1, keySet, v1)
	if err != nil {
		return "", err
	}
	r2, err := forms.EditScalar(r1.Text, root, p2, keySet, v2)
	if err != nil {
		return "", err
	}
	return r2.Text, nil
}
