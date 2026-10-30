package forms

import (
	"slices"
	"strings"
)

// MaterializeFlat (P5-2, first unit; docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 11, ADR 0014). The Rust peer is rust/src/materialize.rs; the shared cases
// are testdata/p5/materialize.json.
//
// The output is the candidate object of the engine's MaterializedObject (cic-primitive-engine engine/src/materialized.rs) for an intent document: a flat map
// from a field name to its capability, its provenance and its value, the projection {"fields": [{key, capability, provenance, value}]} sorted by key, and the
// output_digest of that projection (the digest of its canonical bytes, CanonicalBytes).
//
// What this unit decides, and what it refuses (each refusal is a *MaterializeError, never a guess):
//
//   - Flat only. Every declared position of the schema must be a top-level scalar (or an opaque value that holds a scalar). A composite, a list or a declared
//     list is not_flat: the engine's key is a flat field name and nothing says what the key of a nested position is. A set is set: its order is not decided
//     (decision H, fail closed). The refusal is of the schema, so a nested position that the document leaves out still refuses.
//   - Capability is an input of the caller, a map from a field name to implemented, not_implemented or deprecated. A declared field with none is
//     capability_missing, a value of another spelling is capability_unknown, a key that is not a declared field is capability_undeclared. Nothing is derived
//     from the authored document (ADR 0010: an authored position's capability is not evidence). The digest records the value the caller gave; whether the
//     caller is entitled to it is a second claim that the digest does not make.
//   - An omitted optional field with no default is refused (omitted_optional). The engine can hold an Authored(None) but its spec (B2) leaves open whether
//     it is the same as a field the author did not write, and the provenance is part of the digested claim. Only a field that is written, or filled by its
//     schema default, is materialized.
//   - A document the validation refuses is invalid_document; one the defaults cannot be applied to is unsupported; none at all is no_document.
//
// The order of the checks is part of the contract (the two languages must give the same first reason): the schema, then the capability map (undeclared keys in
// byte order, then the declared fields in schema order), then the document, then the fields in schema order, then the canonical bytes.
//
// ExpectedKeys is the declared top-level field names read from the schema, in byte order. It is what the engine's try_new is given as the key set to be
// complete against. It is derived here from the schema directly, not from the fields that were built, so that a field lost on the way is not lost twice; the
// proof of that independence is the harness (a key set of the generator that never saw this code).

// The reasons a document is not materialized.
const (
	MaterializeNoDocument           = "no_document"
	MaterializeInvalidDocument      = "invalid_document"
	MaterializeUnsupported          = "unsupported"
	MaterializeNotFlat              = "not_flat"
	MaterializeSet                  = "set"
	MaterializeOmittedOptional      = "omitted_optional"
	MaterializeCapabilityMissing    = "capability_missing"
	MaterializeCapabilityUnknown    = "capability_unknown"
	MaterializeCapabilityUndeclared = "capability_undeclared"
)

// The three capabilities of the engine's enum, by their spelling in the projection.
var materializeCapabilities = []string{"implemented", "not_implemented", "deprecated"}

// The provenance of an intent-side field, by its spelling in the projection.
const (
	provenanceAuthored      = "authored"
	provenanceSchemaDefault = "schema_default"
)

// MaterializeError says why a document was not materialized, and at which field ("" when the refusal is not about one).
type MaterializeError struct {
	Reason string
	Field  string
}

func (e *MaterializeError) Error() string {
	return "not materialized: " + e.Reason + " (" + e.Field + ")"
}

// MaterializedField is one field of the candidate object.
type MaterializedField struct {
	Key string
	// Capability is implemented, not_implemented or deprecated, as the caller gave it.
	Capability string
	// Provenance is authored or schema_default.
	Provenance string
	Value      *Node
}

// Materialized is the candidate object of a flat document, its projection and its output_digest.
type Materialized struct {
	// Fields are in byte order of the key.
	Fields []MaterializedField
	// ExpectedKeys are the declared top-level field names, in byte order (see the comment above).
	ExpectedKeys []string
	// ProjectionJSON is the canonical bytes of the projection, as text (they are UTF-8).
	ProjectionJSON string
	// OutputDigest is "sha256:" and the hex digest of the canonical bytes of the projection.
	OutputDigest string
}

// MaterializeFlat materializes the flat document doc against root. a is the key set A of the Access atom, as for ApplyDefaults; capabilities maps a field to
// its capability. The error is a *MaterializeError with the reason of the comment above.
func MaterializeFlat(doc *Node, root Shape, a []string, capabilities map[string]string) (*Materialized, error) {
	if root.Kind != ShapeComposite {
		return nil, &MaterializeError{MaterializeNotFlat, ""}
	}
	declared := root.Fields
	for _, f := range declared {
		switch f.Shape.Kind {
		case ShapeComposite, ShapeList, ShapeDeclaredList:
			return nil, &MaterializeError{MaterializeNotFlat, f.Name}
		case ShapeSet:
			return nil, &MaterializeError{MaterializeSet, f.Name}
		}
	}
	isDeclared := func(name string) bool {
		return slices.ContainsFunc(declared, func(f NamedShape) bool { return f.Name == name })
	}
	given := make([]string, 0, len(capabilities))
	for k := range capabilities {
		given = append(given, k)
	}
	slices.Sort(given)
	for _, k := range given {
		if !isDeclared(k) {
			return nil, &MaterializeError{MaterializeCapabilityUndeclared, k}
		}
	}
	for _, f := range declared {
		c, ok := capabilities[f.Name]
		if !ok {
			return nil, &MaterializeError{MaterializeCapabilityMissing, f.Name}
		}
		if !slices.Contains(materializeCapabilities, c) {
			return nil, &MaterializeError{MaterializeCapabilityUnknown, f.Name}
		}
	}
	if doc == nil {
		return nil, &MaterializeError{MaterializeNoDocument, ""}
	}
	defaulted, err := ApplyDefaults(doc, root, a)
	if err != nil {
		return nil, &MaterializeError{MaterializeUnsupported, ""}
	}
	if defaulted.Document == nil {
		return nil, &MaterializeError{MaterializeInvalidDocument, ""}
	}
	fields := make([]MaterializedField, 0, len(declared))
	for _, f := range declared {
		i := slices.IndexFunc(defaulted.Document.Entries, func(e Entry) bool { return e.Key == f.Name })
		if i < 0 {
			return nil, &MaterializeError{MaterializeOmittedOptional, f.Name}
		}
		value := defaulted.Document.Entries[i].Value
		if value.Kind == KindMap || value.Kind == KindSeq {
			return nil, &MaterializeError{MaterializeNotFlat, f.Name}
		}
		// a field name that cannot be written as a path has no origin of its own: the origins of two such fields would share the key ""
		path := logicalPath([]seg{{field: f.Name}})
		if path == "" {
			return nil, &MaterializeError{MaterializeUnsupported, f.Name}
		}
		provenance := provenanceAuthored
		for _, o := range defaulted.Origins {
			if o.Path == path && o.Origin == ValueOriginDefault {
				provenance = provenanceSchemaDefault
			}
		}
		fields = append(fields, MaterializedField{Key: f.Name, Capability: capabilities[f.Name], Provenance: provenance, Value: value})
	}
	slices.SortFunc(fields, func(x, y MaterializedField) int { return strings.Compare(x.Key, y.Key) })
	expected := make([]string, 0, len(declared))
	for _, f := range declared {
		expected = append(expected, f.Name)
	}
	slices.Sort(expected)
	bytes, err := CanonicalBytes(projectionNode(fields))
	if err != nil {
		reason := MaterializeUnsupported
		if ce, ok := err.(*CanonicalError); ok {
			reason = ce.Reason
		}
		return nil, &MaterializeError{reason, ""}
	}
	return &Materialized{Fields: fields, ExpectedKeys: expected, ProjectionJSON: string(bytes), OutputDigest: CanonicalDigest(bytes)}, nil
}

func textNode(s string) *Node { return &Node{Kind: KindStr, Str: s} }

// projectionNode is {"fields": [{key, capability, provenance, value}]}, as the engine's materialized_object_projection builds it for an intent-side field.
func projectionNode(fields []MaterializedField) *Node {
	items := make([]*Node, 0, len(fields))
	for _, f := range fields {
		items = append(items, &Node{Kind: KindMap, Entries: []Entry{
			{Key: "key", Value: textNode(f.Key)},
			{Key: "capability", Value: textNode(f.Capability)},
			{Key: "provenance", Value: textNode(f.Provenance)},
			{Key: "value", Value: f.Value},
		}})
	}
	return &Node{Kind: KindMap, Entries: []Entry{{Key: "fields", Value: &Node{Kind: KindSeq, Items: items}}}}
}
