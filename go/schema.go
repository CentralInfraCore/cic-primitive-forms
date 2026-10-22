package forms

// Compiling a pinned CIC schema into the Shape tree that Validate reads (ADR 0002).
//
// The library receives a preprocessed schema, the digest of its exact bytes and the identity of the
// grammar it conforms to. It never fetches, never resolves a reference and never looks for a newer
// version. Anything the schema declares that this slice does not process is a structured ErrSchema
// refusal that names the schema node; nothing is simplified silently.
//
// What is read: spec.<surface>.nodes with shape_type scalar (string, integer, boolean), composite,
// collection (list, set) and opaque, the mandatory/optional axis, nullable, default (presence only),
// min_items, the list key rules of ADR 0002 and the case rules of item_choice. What is refused:
// shape_type choice, scalar types number and bytes, an item_choice under a grammar profile without
// that feature, a structure outside ADR 0002, a reference that is not a pinned
// cic:<namespace>:<Name>@vX.Y.Z. What is passed through and not evaluated: contract (listed in
// UnevaluatedContracts), description, role, capability and the other descriptive members.

import (
	"fmt"
	"regexp"
	"slices"
)

// The schema refusal codes. They are outside corpus/codes.yaml, like the upstream binding codes.
const (
	// SchemaInvalid: the structure violates ADR 0002 (a missing companion, a forbidden one, a list
	// with no key, two cases with the same key set, ...).
	SchemaInvalid = "SCHEMA_INVALID"
	// SchemaShapeUnsupported: a well-formed feature this slice does not process.
	SchemaShapeUnsupported = "SCHEMA_SHAPE_UNSUPPORTED"
	// SchemaRefUnpinned: a reference that is not pinned to a version (ADR 0002: rejected, not resolved).
	SchemaRefUnpinned = "SCHEMA_REF_UNPINNED"
	// SchemaFeatureNotInGrammar: the schema uses a construct the pinned grammar does not define.
	SchemaFeatureNotInGrammar = "SCHEMA_FEATURE_NOT_IN_GRAMMAR"
)

// The binding codes of CompileSchema (ErrBinding).
const (
	// SchemaDigestMismatch: the bytes differ from the pinned SHA-256.
	SchemaDigestMismatch = "SCHEMA_DIGEST_MISMATCH"
	// GrammarPinUnknown: the grammar identity is not one this library carries a profile for.
	GrammarPinUnknown = "GRAMMAR_PIN_UNKNOWN"
	// SurfaceUnsupported: the surface is not one this slice validates. Only config_surface, the authored
	// intent, is: a missing mandatory field on state_surface is a coverage fact, not an authoring error
	// (ADR 0003, rule 8), and validating an observation with authoring semantics would state it wrongly.
	SurfaceUnsupported = "SURFACE_UNSUPPORTED"
)

// grammarProfile is what a pinned grammar defines, as far as the schema reader needs it. It is
// keyed by the pin name of corpus/pins.yaml (<repo>-<commit7>-<manifest8>), so a re-pin renames it.
type grammarProfile struct {
	// ItemChoice: the grammar defines item_choice (cic-primitives PR #9). The primitives release
	// v0.2.0 does not; the registry's network-interface.yaml already uses it (ADR 0002, finding 3).
	ItemChoice bool
	// AccessGrammar is the version of the InheritanceMode spelling the grammar defines (access-policy/1
	// is version 1: true | false | 0). It is the grammar the schema's inherit_policy is read under.
	AccessGrammar int64
}

var grammarProfiles = map[string]grammarProfile{
	// devel at 73ae469: the temporary P0 compatibility profile of ADR 0002, not a release contract.
	"cic-primitives-73ae469-71ee71ca": {ItemChoice: true, AccessGrammar: 1},
}

// SchemaInput is the preprocessed, version-pinned schema the caller supplies.
type SchemaInput struct {
	// Bytes is the exact schema file.
	Bytes []byte
	// SHA256 is the pinned digest of Bytes, lower-case hex.
	SHA256 string
	// Grammar is the grammar pin name (corpus/pins.yaml).
	Grammar string
	// Surface is config_surface, named by the caller and never by the document (state_surface is refused).
	Surface string
}

// CompiledSchema is a schema read for one surface.
type CompiledSchema struct {
	// Kind, Namespace and Version are the schema's identity.* members.
	Kind, Namespace, Version string
	// Digest is the verified SHA-256 of the schema bytes; Grammar the pin it was read under.
	Digest, Grammar, Surface string
	// DeclaredValidatedBy is metadata.validatedBy as written ("name version"), kept so a caller can
	// see a schema that declares an older release than the grammar it is read under.
	DeclaredValidatedBy string
	// Base is identity.base, a pinned reference ("" when the schema declares none).
	Base string
	// Policy is spec.policy_surface as declared (see PolicySurface); nothing is evaluated or defaulted.
	Policy PolicySurface
	// Root is the composite of the surface's nodes, mandatory.
	Root Shape
	// UsesItemChoice says whether any list of the surface is an item_choice.
	UsesItemChoice bool
	// UnevaluatedContracts are the canonical paths of the nodes that declare a contract. Contracts
	// are passed through, not evaluated: validation does not check them.
	UnevaluatedContracts []string
}

func sprintf(format string, a ...any) string { return fmt.Sprintf(format, a...) }

func schemaErr(code, path, format string, a ...any) *Error {
	detail := sprintf(format, a...)
	return &Error{Kind: ErrSchema, SchemaCode: code, Detail: path + ": " + detail,
		Problems: []SchemaProblem{{Code: code, Path: path, Detail: detail}}}
}

var pinnedRef = regexp.MustCompile(`^cic:[a-z0-9_-]+:[A-Za-z0-9_]+@v[0-9]+\.[0-9]+\.[0-9]+$`)

type compiler struct {
	profile     grammarProfile
	itemChoice  bool
	unevaluated []string
	problems    []SchemaProblem
}

// record keeps a node's refusal and lets the walk go on to its siblings.
func (c *compiler) record(e *Error) {
	c.problems = append(c.problems, e.Problems...)
}

// CompileSchema verifies the schema bytes against the pinned digest, binds the grammar identity and
// reads the surface into a Shape tree.
//
// Errors are *Error: ErrBinding (SCHEMA_DIGEST_MISMATCH, GRAMMAR_PIN_UNKNOWN, SURFACE_UNSUPPORTED),
// the parse errors of the YAML subset, or ErrSchema (see the codes above).
func CompileSchema(in SchemaInput) (*CompiledSchema, error) {
	return compileWith(in, grammarProfiles)
}

func compileWith(in SchemaInput, profiles map[string]grammarProfile) (*CompiledSchema, error) {
	if SHA256Hex(in.Bytes) != in.SHA256 {
		return nil, binding(SchemaDigestMismatch)
	}
	profile, ok := profiles[in.Grammar]
	if !ok {
		return nil, binding(GrammarPinUnknown)
	}
	if in.Surface != "config_surface" {
		return nil, binding(SurfaceUnsupported)
	}
	doc, err := Parse(string(in.Bytes))
	if err != nil {
		return nil, err
	}
	spec := lookup(doc, "spec")
	if spec == nil || spec.Kind != KindMap {
		return nil, schemaErr(SchemaInvalid, "spec", "a mapping is required")
	}
	c := &compiler{profile: profile}
	out := &CompiledSchema{Digest: in.SHA256, Grammar: in.Grammar, Surface: in.Surface}
	if err := c.refs(spec, in.Surface); err != nil {
		return nil, err
	}
	if id := lookup(spec, "identity"); id != nil {
		out.Kind, out.Namespace, out.Version = strMember(id, "kind"), strMember(id, "namespace"), strMember(id, "version")
		out.Base = strMember(id, "base")
	}
	if md := lookup(doc, "metadata"); md != nil {
		if vb := lookup(md, "validatedBy"); vb != nil {
			out.DeclaredValidatedBy = strMember(vb, "name") + " " + strMember(vb, "version")
		}
	}
	surface := lookup(spec, in.Surface)
	if surface == nil || surface.Kind != KindMap {
		return nil, schemaErr(SchemaInvalid, in.Surface, "the surface is not declared")
	}
	nodes := lookup(surface, "nodes")
	if nodes == nil || nodes.Kind != KindSeq {
		return nil, schemaErr(SchemaInvalid, in.Surface+".nodes", "a list is required")
	}
	fields := c.fields(nodes, "$")
	out.Policy = c.policy(spec)
	if len(c.problems) > 0 {
		first := c.problems[0]
		return nil, &Error{Kind: ErrSchema, SchemaCode: first.Code, Detail: first.Path + ": " + first.Detail, Problems: c.problems}
	}
	out.Root = Shape{Kind: ShapeComposite, Fields: fields, Presence: Mandatory}
	out.UsesItemChoice = c.itemChoice
	out.UnevaluatedContracts = c.unevaluated
	return out, nil
}

// str is a string member, "" when absent or not a string.
func strMember(n *Node, key string) string {
	if v := lookup(n, key); v != nil && v.Kind == KindStr {
		return v.Str
	}
	return ""
}

// refs rejects an unpinned reference of the parts the contract reads: the base, the identity and the
// selected surface.
func (c *compiler) refs(spec *Node, surface string) *Error {
	check := func(path string, v *Node) *Error {
		if v == nil {
			return nil
		}
		if v.Kind != KindStr || !pinnedRef.MatchString(v.Str) {
			return schemaErr(SchemaRefUnpinned, path, "a reference must be cic:<namespace>:<Name>@vX.Y.Z")
		}
		return nil
	}
	if base := lookup(spec, "base"); base != nil {
		if err := check("spec.base.ref", lookup(base, "ref")); err != nil {
			return err
		}
	}
	if id := lookup(spec, "identity"); id != nil {
		for _, k := range []string{"atomic_ref", "base"} {
			if err := check("spec.identity."+k, lookup(id, k)); err != nil {
				return err
			}
		}
	}
	if s := lookup(spec, surface); s != nil {
		if err := check("spec."+surface+".aggregate_ref", lookup(s, "aggregate_ref")); err != nil {
			return err
		}
		if nodes := lookup(s, "nodes"); nodes != nil && nodes.Kind == KindSeq {
			for i, n := range nodes.Items {
				if err := check(sprintf("spec.%s.nodes[%d].atomic_ref", surface, i), lookup(n, "atomic_ref")); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func (c *compiler) fields(list *Node, path string) []NamedShape {
	var out []NamedShape
	seen := map[string]bool{}
	for i, n := range list.Items {
		name := strMember(n, "name")
		if name == "" { // a node that is not a mapping has no name either
			c.record(schemaErr(SchemaInvalid, sprintf("%s[%d]", path, i), "a node must be a mapping with a non-empty string name"))
			continue
		}
		if seen[name] {
			c.record(schemaErr(SchemaInvalid, path+"."+name, "duplicate field name"))
			continue
		}
		seen[name] = true
		shape, err := c.node(n, path+"."+name)
		if err != nil {
			c.record(err)
			continue
		}
		out = append(out, NamedShape{Name: name, Shape: shape})
	}
	return out
}

// boolMember returns a boolean member; ok is false when the member is absent.
func boolMember(n *Node, key, path string) (v, ok bool, err *Error) {
	m := lookup(n, key)
	if m == nil {
		return false, false, nil
	}
	if m.Kind != KindBool {
		return false, true, schemaErr(SchemaInvalid, path+"."+key, "a boolean is required")
	}
	return m.Bool, true, nil
}

func (c *compiler) presence(n *Node, path string) (Presence, *Error) {
	mand, hasM, err := boolMember(n, "mandatory", path)
	if err != nil {
		return zero[Presence](), err
	}
	opt, hasO, err := boolMember(n, "optional", path)
	if err != nil {
		return zero[Presence](), err
	}
	// shape.yaml: only the value true is valid for either member; mandatory: false and optional: false are not.
	switch {
	case (hasM && !mand) || (hasO && !opt):
		return zero[Presence](), schemaErr(SchemaInvalid, path, "mandatory and optional take only the value true")
	case hasM && hasO:
		return zero[Presence](), schemaErr(SchemaInvalid, path, "mandatory and optional contradict each other")
	case hasO:
		return Optional, nil
	}
	return Mandatory, nil // neither written means mandatory (shape.yaml)
}

var scalarTypes = map[string]ScalarType{"string": ScalarString, "integer": ScalarInteger, "boolean": ScalarBoolean}

func (c *compiler) scalarType(n *Node, key, path string) (ScalarType, *Error) {
	v := lookup(n, key)
	if v == nil || v.Kind != KindStr {
		return zero[ScalarType](), schemaErr(SchemaInvalid, path+"."+key, "a scalar type is required")
	}
	if t, ok := scalarTypes[v.Str]; ok {
		return t, nil
	}
	if v.Str == "number" || v.Str == "bytes" {
		return zero[ScalarType](), schemaErr(SchemaShapeUnsupported, path+"."+key, "scalar type %q is not processed by this slice", v.Str)
	}
	return zero[ScalarType](), schemaErr(SchemaInvalid, path+"."+key, "unknown scalar type %q", v.Str)
}

func forbid(n *Node, path string, keys ...string) *Error {
	for _, k := range keys {
		if lookup(n, k) != nil {
			return schemaErr(SchemaInvalid, path, "%s is not allowed here", k)
		}
	}
	return nil
}

func (c *compiler) node(n *Node, path string) (Shape, *Error) {
	var s Shape
	var err *Error
	if s.Presence, err = c.presence(n, path); err != nil {
		return s, err
	}
	if s.Nullable, _, err = boolMember(n, "nullable", path); err != nil {
		return s, err
	}
	role, err := readRole(n, path)
	if err != nil {
		return s, err
	}
	declared := lookup(n, "default")
	if declared != nil {
		if s.Presence == Mandatory {
			return s, schemaErr(SchemaInvalid, path, "a default is not allowed on a mandatory field (the field does not say optional)")
		}
		if declared.Kind == KindNull {
			return s, schemaErr(SchemaInvalid, path, "default: null is invalid")
		}
		if !role.takesDefault() {
			return s, schemaErr(SchemaInvalid, path, "a default is not allowed on a node whose Role does not take one (ADR 0011)")
		}
		s.HasDefault = true
	}
	if lookup(n, "contract") != nil {
		c.unevaluated = append(c.unevaluated, path)
	}
	st := lookup(n, "shape_type")
	if st == nil || st.Kind != KindStr {
		return s, schemaErr(SchemaInvalid, path+".shape_type", "shape_type is required")
	}
	switch st.Str {
	case "scalar":
		if err := forbid(n, path, "fields", "item_fields", "item_choice", "cases"); err != nil {
			return s, err
		}
		s.Kind = ShapeScalar
		s.Scalar, err = c.scalarType(n, "scalar_type", path)
	case "opaque":
		if err := forbid(n, path, "scalar_type", "fields", "item_fields", "item_choice", "cases", "collection_variant"); err != nil {
			return s, err
		}
		s.Kind = ShapeOpaque
	case "composite":
		if err := forbid(n, path, "scalar_type", "item_fields", "item_choice", "cases"); err != nil {
			return s, err
		}
		s.Kind = ShapeComposite
		list := lookup(n, "fields")
		if list == nil || list.Kind != KindSeq || len(list.Items) == 0 {
			return s, schemaErr(SchemaInvalid, path+".fields", "a composite needs at least one field")
		}
		s.Fields = c.fields(list, path)
	case "collection":
		err = c.collection(n, path, &s)
	case "choice":
		return s, schemaErr(SchemaShapeUnsupported, path, "shape_type choice is not processed by this slice")
	default:
		return s, schemaErr(SchemaInvalid, path+".shape_type", "unknown shape_type %q", st.Str)
	}
	if err == nil && declared != nil && !defaultFits(s, declared) {
		return s, schemaErr(SchemaInvalid, path+".default", "the default is not a valid value of its own node")
	}
	if err == nil && declared != nil {
		s.Default = cloneTree(declared)
	}
	return s, err
}

// accessAtomKeys is the key set A of the Access atom (access.yaml): what recognition needs to tell an expanded node.
var accessAtomKeys = []string{"value", "access", "modify", "inherit", "default_injection", "conformance"}

// defaultFits says whether d is a valid value of the node s itself: the schema's own check of a default, so that
// applying it can never produce a document that does not validate.
func defaultFits(s Shape, d *Node) bool {
	s.Presence, s.HasDefault, s.Default = Mandatory, false, nil
	probe := Shape{Kind: ShapeComposite, Presence: Mandatory, Fields: []NamedShape{{Name: "default", Shape: s}}}
	doc := &Node{Kind: KindMap, Entries: []Entry{{Key: "default", Value: d}}}
	v, err := Validate(doc, probe, accessAtomKeys)
	return err == nil && len(v.Violations) == 0
}

func (c *compiler) collection(n *Node, path string, s *Shape) *Error {
	if err := forbid(n, path, "scalar_type", "fields", "cases"); err != nil {
		return err
	}
	if v, ok, err := intMember(n, "min_items", path); err != nil {
		return err
	} else if ok {
		s.MinItems, s.HasMinItems = v, true
	}
	if _, _, err := intMember(n, "max_items", path); err != nil {
		return err
	}
	variant := lookup(n, "collection_variant")
	if variant == nil || variant.Kind != KindStr || (variant.Str != "list" && variant.Str != "set") {
		return schemaErr(SchemaInvalid, path+".collection_variant", "list or set is required")
	}
	if variant.Str == "set" {
		if err := forbid(n, path, "item_fields", "item_choice", "item_key"); err != nil {
			return err
		}
		t, err := c.scalarType(n, "item_scalar_type", path)
		s.Kind, s.Scalar = ShapeSet, t
		return err
	}
	if err := forbid(n, path, "item_scalar_type"); err != nil {
		return err
	}
	s.Kind = ShapeDeclaredList
	fields, choice := lookup(n, "item_fields"), lookup(n, "item_choice")
	switch {
	case fields != nil && choice != nil:
		return schemaErr(SchemaInvalid, path, "item_fields and item_choice are exclusive")
	case fields != nil:
		els, err := c.itemFields(n, fields, path)
		s.Elements = els
		return err
	case choice != nil:
		els, err := c.itemChoiceCases(choice, path)
		s.Elements = els
		return err
	}
	return schemaErr(SchemaInvalid, path, "a list needs item_fields or item_choice")
}

func intMember(n *Node, key, path string) (int, bool, *Error) {
	m := lookup(n, key)
	if m == nil {
		return zero[int](), false, nil
	}
	if m.Kind != KindInt || m.Int < 0 {
		return zero[int](), true, schemaErr(SchemaInvalid, path+"."+key, "a non-negative integer is required")
	}
	return int(m.Int), true, nil
}

// keyFields are the names of the item_fields entries that carry role: key, in declaration order.
func keyFields(list *Node) []string {
	var out []string
	for _, f := range list.Items {
		if r := lookup(f, "role"); r != nil && r.Kind == KindStr && r.Str == "key" {
			out = append(out, strMember(f, "name"))
		}
	}
	return out
}

func (c *compiler) itemFields(owner, list *Node, path string) (*Elements, *Error) {
	if list.Kind != KindSeq || len(list.Items) == 0 {
		return nil, schemaErr(SchemaInvalid, path+".item_fields", "at least one element field is required")
	}
	fields := c.fields(list, path+"[]")
	keys := keyFields(list)
	itemKey := lookup(owner, "item_key")
	var order []string
	switch {
	case len(keys) == 0:
		return nil, schemaErr(SchemaInvalid, path, "a list needs a key: one item_fields entry with role: key")
	case len(keys) == 1 && itemKey == nil:
		order = keys
	case itemKey == nil:
		return nil, schemaErr(SchemaInvalid, path, "several key fields need item_key")
	default:
		order = names(itemKey)
		if order == nil || !sameSet(order, keys) {
			return nil, schemaErr(SchemaInvalid, path+".item_key", "item_key must name exactly the role: key fields")
		}
	}
	return &Elements{Cases: []Case{{Fields: fields, Key: order}}}, nil
}

func names(n *Node) []string {
	if n.Kind != KindSeq {
		return nil
	}
	out := []string{}
	for _, it := range n.Items {
		if it.Kind != KindStr {
			return nil
		}
		out = append(out, it.Str)
	}
	return out
}

func sameSet(a, b []string) bool {
	x, y := slices.Clone(a), slices.Clone(b)
	slices.Sort(x)
	slices.Sort(y)
	return slices.Equal(x, y) && len(slices.Compact(x)) == len(x)
}

func (c *compiler) itemChoiceCases(choice *Node, path string) (*Elements, *Error) {
	if !c.profile.ItemChoice {
		return nil, schemaErr(SchemaFeatureNotInGrammar, path+".item_choice", "the pinned grammar does not define item_choice")
	}
	c.itemChoice = true
	cases := lookup(choice, "cases")
	if cases == nil || cases.Kind != KindSeq || len(cases.Items) < 2 { // a choice that is not a mapping has no cases either
		return nil, schemaErr(SchemaInvalid, path+".item_choice", "cases (at least two) are required")
	}
	els := &Elements{Choice: true}
	seenName := map[string]bool{}
	var seenKeys [][]string
	for i, cs := range cases.Items {
		name := strMember(cs, "name")
		cp := sprintf("%s.item_choice.cases[%d]", path, i)
		if name == "" {
			return nil, schemaErr(SchemaInvalid, cp, "a case needs a name")
		}
		if seenName[name] {
			return nil, schemaErr(SchemaInvalid, cp, "duplicate case name %q", name)
		}
		seenName[name] = true
		fields := lookup(cs, "fields")
		if fields == nil || fields.Kind != KindSeq || len(fields.Items) == 0 {
			return nil, schemaErr(SchemaInvalid, cp+".fields", "at least one field is required")
		}
		compiled := c.fields(fields, cp)
		key := names(orEmpty(lookup(cs, "key")))
		if len(key) == 0 || !sameSet(key, keyFields(fields)) {
			return nil, schemaErr(SchemaInvalid, cp+".key", "key must name exactly the role: key fields of the case")
		}
		for _, k := range seenKeys {
			if sameSet(k, key) {
				return nil, schemaErr(SchemaInvalid, cp+".key", "two cases resolve to the same key field set (C17)")
			}
		}
		seenKeys = append(seenKeys, key)
		els.Cases = append(els.Cases, Case{Name: name, Fields: compiled, Key: key})
	}
	return els, nil
}

func orEmpty(n *Node) *Node {
	if n == nil {
		return &Node{Kind: KindNull}
	}
	return n
}
