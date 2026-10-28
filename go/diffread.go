package forms

import (
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ReadDiff (P4, after P4-3; ADR 0008, "Canonical serialization"): the canonical bytes of a diff read back into events.
//
// It is strict and it refuses: the bytes must be exactly the writing ADR 0008 fixes, so that
//
//	DiffResult{Changes, Representation}.Canonical() == input
//
// holds for everything it accepts. It reads no schema and decides nothing about meaning: it checks the form of the bytes, the shape of every event and entry, the
// canonical text and the order of the paths, and that no event describes no change. Representation entries are returned apart and are never events of a patch
// (PatchDiff does not carry them); an event kind the patch does not apply is returned like any other, for the patch to refuse by name.
//
// A diff of another version cannot be interpreted: its bytes must still be canonical JSON with an integer `version`, and the result holds the version and no events,
// which is what the patch needs to refuse it (PATCH_CONTEXT_MISMATCH).
//
// The error is a *DiffReadError. Its Reason is one of the constants below; they are reasons of this reader, not codes of the registry of ADR 0007.
// The Rust peer is rust/src/diffread.rs.

// The reasons a diff is not read.
const (
	ReadNotUTF8        = "not_utf8"
	ReadSyntax         = "syntax"
	ReadDepth          = "depth"
	ReadDuplicateKey   = "duplicate_key"
	ReadNumber         = "unsupported_number" // a float, an integer beyond signed 64 bits, a leading zero, a minus zero
	ReadShape          = "shape"              // a key, a kind or a type the contract does not have
	ReadPath           = "path"               // not a canonical path of ADR 0006, or the root
	ReadOrder          = "order"              // events or entries not strictly in the order of ADR 0008, or one (path, kind) twice
	ReadNoChange       = "no_change"          // an event that describes no change
	ReadNotCanonical   = "not_canonical"      // valid, but not the writing ADR 0008 fixes
	readMaxDepth       = 64
	maxVersion         = 1<<31 - 1
	readVersion        = 1
	readRepresentation = "representation_change"
)

// DiffReadError says why the bytes of a diff were refused, and at which byte offset the reader stood (0 when the refusal is about the whole document).
type DiffReadError struct {
	Reason string
	Offset int
}

func (e *DiffReadError) Error() string {
	return "diff not read: " + e.Reason + " at byte " + strconv.Itoa(e.Offset)
}

// CanonicalDiff is a diff read from its canonical bytes.
type CanonicalDiff struct {
	Version        int
	Changes        []DiffEvent
	Representation []RepresentationChange
}

// PatchDiff is the diff as the patch takes it: the version and the events; the representation entries are not events.
func (c CanonicalDiff) PatchDiff() PatchDiff {
	return PatchDiff{Version: c.Version, Changes: c.Changes}
}

type jsonReader struct {
	b  []byte
	at int
}

func (r *jsonReader) fail(reason string) *DiffReadError {
	return &DiffReadError{Reason: reason, Offset: r.at}
}

func (r *jsonReader) peek() int {
	if r.at < len(r.b) {
		return int(r.b[r.at])
	}
	return -1
}

func (r *jsonReader) value(depth int) (*Node, *DiffReadError) {
	if depth > readMaxDepth {
		return nil, r.fail(ReadDepth)
	}
	switch c := r.peek(); {
	case c == '{':
		return r.object(depth)
	case c == '[':
		return r.array(depth)
	case c == '"':
		s, err := r.str()
		return &Node{Kind: KindStr, Str: s}, err
	case c == '-' || (c >= '0' && c <= '9'):
		return r.number()
	}
	for _, lit := range []struct {
		text string
		node Node
	}{{"true", Node{Kind: KindBool, Bool: true}}, {"false", Node{Kind: KindBool}}, {"null", Node{Kind: KindNull}}} {
		if strings.HasPrefix(string(r.b[r.at:]), lit.text) {
			r.at += len(lit.text)
			n := lit.node
			return &n, nil
		}
	}
	return nil, r.fail(ReadSyntax)
}

func (r *jsonReader) number() (*Node, *DiffReadError) {
	start := r.at
	if r.peek() == '-' {
		r.at++
	}
	digits := r.at
	for c := r.peek(); c >= '0' && c <= '9'; c = r.peek() {
		r.at++
	}
	if r.at == digits {
		return nil, r.fail(ReadSyntax)
	}
	if c := r.peek(); c == '.' || c == 'e' || c == 'E' {
		return nil, r.fail(ReadNumber)
	}
	text := string(r.b[start:r.at])
	n, err := strconv.ParseInt(text, 10, 64)
	if err != nil || (r.b[digits] == '0' && r.at-digits > 1) || text == "-0" {
		return nil, r.fail(ReadNumber)
	}
	return &Node{Kind: KindInt, Int: n}, nil
}

// hex4 reads the four hexadecimal digits of a \u escape.
func (r *jsonReader) hex4() (rune, bool) {
	if r.at+4 > len(r.b) {
		return 0, false
	}
	n, err := strconv.ParseUint(string(r.b[r.at:r.at+4]), 16, 16)
	r.at += 4
	return rune(n), err == nil
}

func (r *jsonReader) str() (string, *DiffReadError) {
	r.at++ // the opening quote
	var b strings.Builder
	for {
		c := r.peek()
		r.at++
		switch {
		case c < 0:
			return "", r.fail(ReadSyntax)
		case c == '"':
			return b.String(), nil
		case c < 0x20:
			return "", r.fail(ReadSyntax)
		case c != '\\':
			b.WriteByte(byte(c))
		default:
			ch, err := r.escape()
			if err != nil {
				return "", err
			}
			b.WriteRune(ch)
		}
	}
}

func (r *jsonReader) escape() (rune, *DiffReadError) {
	esc := r.peek()
	r.at++
	switch esc {
	case '"', '\\', '/':
		return rune(esc), nil
	case 'b':
		return '\b', nil
	case 'f':
		return '\f', nil
	case 'n':
		return '\n', nil
	case 'r':
		return '\r', nil
	case 't':
		return '\t', nil
	case 'u':
		return r.unicode()
	}
	return 0, r.fail(ReadSyntax)
}

func (r *jsonReader) unicode() (rune, *DiffReadError) {
	code, ok := r.hex4()
	switch {
	case !ok || (code >= 0xdc00 && code < 0xe000):
		return 0, r.fail(ReadSyntax)
	case code >= 0xd800 && code < 0xdc00:
		// a high surrogate must be followed by a low one; the pair is valid JSON for one character that ADR 0008 writes literally, so the escape is never
		// the canonical writing and is refused at once (the character it stands for would change nothing)
		if !strings.HasPrefix(string(r.b[r.at:]), "\\u") {
			return 0, r.fail(ReadSyntax)
		}
		r.at += 2
		if low, ok := r.hex4(); ok && low >= 0xdc00 && low < 0xe000 {
			return 0, r.fail(ReadNotCanonical)
		}
		return 0, r.fail(ReadSyntax)
	}
	return code, nil
}

func (r *jsonReader) array(depth int) (*Node, *DiffReadError) {
	r.at++
	out := &Node{Kind: KindSeq, Items: []*Node{}}
	if r.peek() == ']' {
		r.at++
		return out, nil
	}
	for {
		it, err := r.value(depth + 1)
		if err != nil {
			return nil, err
		}
		out.Items = append(out.Items, it)
		switch r.peek() {
		case ',':
			r.at++
		case ']':
			r.at++
			return out, nil
		default:
			return nil, r.fail(ReadSyntax)
		}
	}
}

func (r *jsonReader) object(depth int) (*Node, *DiffReadError) {
	r.at++
	out := &Node{Kind: KindMap, Entries: []Entry{}}
	if r.peek() == '}' {
		r.at++
		return out, nil
	}
	for {
		if r.peek() != '"' {
			return nil, r.fail(ReadSyntax)
		}
		key, err := r.str()
		if err != nil {
			return nil, err
		}
		if slices.ContainsFunc(out.Entries, func(e Entry) bool { return e.Key == key }) {
			return nil, r.fail(ReadDuplicateKey)
		}
		if r.peek() != ':' {
			return nil, r.fail(ReadSyntax)
		}
		r.at++
		v, err := r.value(depth + 1)
		if err != nil {
			return nil, err
		}
		out.Entries = append(out.Entries, Entry{Key: key, Value: v})
		switch r.peek() {
		case ',':
			r.at++
		case '}':
			r.at++
			return out, nil
		default:
			return nil, r.fail(ReadSyntax)
		}
	}
}

func readFail(reason string) (CanonicalDiff, error) {
	return CanonicalDiff{}, &DiffReadError{Reason: reason}
}

// members is the map of a node with exactly the given keys, or nil.
func members(n *Node, keys ...string) map[string]*Node {
	if n.Kind != KindMap || len(n.Entries) != len(keys) {
		return nil
	}
	out := map[string]*Node{}
	for _, e := range n.Entries {
		if !slices.Contains(keys, e.Key) {
			return nil
		}
		out[e.Key] = e.Value
	}
	return out
}

func isString(n *Node) bool { return n.Kind == KindStr }

// pathOrder is the key of a canonical, non-root path in the order of ADR 0007, or ok false.
func pathOrder(text string) (order []string, ok bool) {
	segs, perr := ParsePath(text)
	if perr != nil || len(segs) == 0 {
		return nil, false
	}
	return keysOf(segsOf(segs)), true
}

func isScalar(n *Node) bool { return n.Kind != KindSeq && n.Kind != KindMap }

// strictlySorted says whether the scalars are in the order of ADR 0008 with no repeat.
func strictlySorted(items []*Node) bool {
	for i, x := range items {
		if !isScalar(x) || (i > 0 && compareScalars(items[i-1], x) >= 0) {
			return false
		}
	}
	return true
}

func eventOf(n *Node) (DiffEvent, string) {
	var kindNode *Node
	if n.Kind == KindMap {
		for _, e := range n.Entries {
			if e.Key == "kind" {
				kindNode = e.Value
			}
		}
	}
	if kindNode == nil || kindNode.Kind != KindStr {
		return DiffEvent{}, ReadShape
	}
	e := DiffEvent{Kind: kindNode.Str}
	var m map[string]*Node
	switch e.Kind {
	case EventAdded:
		if m = members(n, "kind", "next", "path"); m != nil {
			e.Next = m["next"]
		}
	case EventRemoved:
		if m = members(n, "kind", "path", "previous"); m != nil {
			e.Previous = m["previous"]
		}
	case EventValueChange:
		if m = members(n, "kind", "next", "path", "previous"); m != nil {
			e.Previous, e.Next = m["previous"], m["next"]
		}
	case EventPolicyDeclarationChange:
		if m = members(n, "changed", "kind", "path"); m != nil && m["changed"].Kind == KindMap && len(m["changed"].Entries) > 0 {
			for _, f := range m["changed"].Entries {
				side := members(f.Value, "next", "previous")
				if side == nil {
					return DiffEvent{}, ReadShape
				}
				e.Changed = append(e.Changed, DeclChange{Field: f.Key, Previous: side["previous"], Next: side["next"]})
			}
		} else {
			m = nil
		}
	case EventSetChange:
		if m = members(n, "added", "kind", "path", "removed"); m != nil && m["added"].Kind == KindSeq && m["removed"].Kind == KindSeq {
			e.Added, e.Removed = m["added"].Items, m["removed"].Items
		} else {
			m = nil
		}
	}
	if m == nil || !isString(m["path"]) {
		return DiffEvent{}, ReadShape
	}
	e.Path = m["path"].Str
	return e, ""
}

// noChange says whether the event describes no difference.
func noChange(e DiffEvent) bool {
	switch e.Kind {
	case EventValueChange:
		return e.Previous.Same(e.Next)
	case EventPolicyDeclarationChange:
		return slices.ContainsFunc(e.Changed, func(c DeclChange) bool { return c.Previous.Same(c.Next) })
	case EventSetChange:
		return len(e.Added) == 0 && len(e.Removed) == 0
	}
	return false
}

// ReadDiff reads the canonical bytes of a diff. See the comment at the top of the file.
func ReadDiff(b []byte) (CanonicalDiff, error) {
	if !utf8.Valid(b) {
		return readFail(ReadNotUTF8)
	}
	r := &jsonReader{b: b}
	root, rerr := r.value(0)
	if rerr != nil {
		return CanonicalDiff{}, rerr
	}
	if r.at != len(b) {
		return CanonicalDiff{}, r.fail(ReadSyntax)
	}
	if root.Kind != KindMap {
		return readFail(ReadShape)
	}
	var version *Node
	for _, e := range root.Entries {
		if e.Key == "version" {
			version = e.Value
		}
	}
	if version == nil || version.Kind != KindInt || version.Int < 0 || version.Int > maxVersion {
		return readFail(ReadShape)
	}
	var generic strings.Builder
	writeCanonical(&generic, root)
	if generic.String() != string(b) {
		return readFail(ReadNotCanonical)
	}
	if version.Int != readVersion {
		return CanonicalDiff{Version: int(version.Int)}, nil
	}
	top := members(root, "changes", "representation", "version")
	if top == nil || top["changes"].Kind != KindSeq || top["representation"].Kind != KindSeq {
		return readFail(ReadShape)
	}
	out := CanonicalDiff{Version: readVersion, Changes: []DiffEvent{}, Representation: []RepresentationChange{}}
	var last []string
	var lastKind string
	for _, raw := range top["changes"].Items {
		e, reason := eventOf(raw)
		if reason != "" {
			return readFail(reason)
		}
		order, ok := pathOrder(e.Path)
		if !ok {
			return readFail(ReadPath)
		}
		if last != nil {
			c := slices.Compare(last, order)
			if c > 0 || (c == 0 && strings.Compare(lastKind, e.Kind) >= 0) {
				return readFail(ReadOrder)
			}
		}
		if noChange(e) {
			return readFail(ReadNoChange)
		}
		if e.Kind == EventSetChange && (!strictlySorted(e.Added) || !strictlySorted(e.Removed) || slices.ContainsFunc(e.Added, func(x *Node) bool {
			return slices.ContainsFunc(e.Removed, x.Same)
		})) {
			return readFail(ReadShape)
		}
		e.order, last, lastKind = order, order, e.Kind
		out.Changes = append(out.Changes, e)
	}
	var lastRep []string
	for _, raw := range top["representation"].Items {
		m := members(raw, "kind", "next", "path", "previous")
		if m == nil || !isString(m["kind"]) || m["kind"].Str != readRepresentation || !isString(m["path"]) || !isString(m["next"]) || !isString(m["previous"]) {
			return readFail(ReadShape)
		}
		next, previous := m["next"].Str, m["previous"].Str
		if (next != "short" && next != "expanded") || (previous != "short" && previous != "expanded") {
			return readFail(ReadShape)
		}
		order, ok := pathOrder(m["path"].Str)
		if !ok {
			return readFail(ReadPath)
		}
		if lastRep != nil && slices.Compare(lastRep, order) >= 0 {
			return readFail(ReadOrder)
		}
		if next == previous {
			return readFail(ReadNoChange)
		}
		lastRep = order
		out.Representation = append(out.Representation, RepresentationChange{Path: m["path"].Str, Previous: previous, Next: next, order: order})
	}
	again := (&DiffResult{Changes: out.Changes, Representation: out.Representation}).Canonical()
	if again != string(b) {
		return readFail(ReadNotCanonical)
	}
	return out, nil
}
