package forms

import "strings"

// The flat receipt (P5-3; docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 17). The Rust peer is rust/src/receipt.rs; the shared cases are testdata/p5/receipt.json.
//
// The document is the engine's (cic-primitive-engine docs/RECEIPT-SCHEMA.md): {"materialization": {15 fields}}, as canonical bytes (the same A1-A6 form as
// everything else here), with the digest of those bytes. The engine's receipt.Verify is the gate that decides whether a receipt of this file is consistent with
// its object and its input (tools/p5/engine-harness-go); this file does not claim it by itself.
//
// One compile. ReceiptFlatFixture takes the schema as a SchemaInput and compiles it once. The name, the version and the digest come from that compile
// (SchemaIdentityOf's rules), and so does the shape the document is materialized against, so the key set of the object is the key set of the schema that the
// identity names. There is no parameter through which another schema's component or key set could enter.
//
// Fixture only. The three release fields and the two grammar digests are not facts of this library, and the pinned grammar is a commit of devel that is not
// shown to belong to a signed release (section 15.1, decision L). The function is named for what it is: it takes a ReleaseFixture, checks the form of its
// fields and nothing about their origin, and a receipt it makes proves nothing about where the release came from. There is no production function yet; it needs
// a verified release record, which does not exist. The receipt layout has no field that could mark a receipt as fixture-made, so the separation is in the
// function, not in the document.
//
// What it refuses (a *ReceiptError, never a guess): the errors of the schema identity and of the materialization as they are; a schema whose identity.version is
// empty (the engine's verifier refuses an empty schema_version); a release or engine field that is empty, or a digest that is not sha256: and 64 lowercase hex;
// and a field name that is not exactly one plain path segment (decision O: a dot, an escape character, a bracket, a brace, = or , in a name; the verifier cannot
// tell a.b as two segments from one field named a.b, and the library adds no escape of its own).
//
// applied_defaults lists the fields of provenance schema_default, sorted by path in byte order, rule schema-default, with the digest of the canonical bytes of the
// value. derived_values is []: this library derives nothing. input_digest is the digest of the canonical bytes of the parsed authored document, before the
// defaults. FlatReceipt.Binding maps $.<field> to <field> for the listed fields, the explicit path binding that the verifier requires.

// The reasons of a *ReceiptError. ReceiptIdentity, ReceiptMaterialize and ReceiptCanonical carry the refusal of the layer below in Cause.
const (
	ReceiptIdentity           = "identity"
	ReceiptMaterialize        = "materialize"
	ReceiptCanonical          = "canonical"
	ReceiptSchemaVersionEmpty = "schema_version_empty"
	ReceiptReleaseField       = "release_field"
	ReceiptEngineField        = "engine_field"
	ReceiptPathNotOneSegment  = "path_not_one_segment"
)

// ReleaseFixture is the release and grammar identity of a fixture receipt: stated by the caller, checked for form, not for origin.
type ReleaseFixture struct {
	PrimitiveRelease      string
	PrimitiveSourceCommit string
	PrimitiveBuildHash    string
	GrammarSHA256         string
	GrammarSchemaSHA256   string
}

// EngineIdentity is the implementation that materialized: its identity and its own version.
type EngineIdentity struct {
	Identity string
	Version  string
}

// ReceiptError says why no receipt was made. Field is the receipt field (or the document field) the refusal is about; Cause is the refusal of the layer below
// for ReceiptIdentity (a schema error or a *SchemaIdentityError), ReceiptMaterialize (a *MaterializeError) and ReceiptCanonical.
type ReceiptError struct {
	Reason string
	Field  string
	Cause  error
}

func (e *ReceiptError) Error() string {
	s := "no receipt: " + e.Reason
	if e.Field != "" {
		s += " (" + e.Field + ")"
	}
	if e.Cause != nil {
		s += ": " + e.Cause.Error()
	}
	return s
}

func (e *ReceiptError) Unwrap() error { return e.Cause }

// FlatReceipt is a receipt, its canonical bytes and digest, the explicit path binding the verifier needs, and the object it describes.
type FlatReceipt struct {
	// Document is the canonical bytes of {"materialization": ...}, as text.
	Document string
	// Digest is "sha256:" and the hex digest of those bytes.
	Digest string
	// Binding maps $.<field> to <field>, for every entry of applied_defaults.
	Binding map[string]string
	// AuthoredInput is the canonical bytes of the parsed authored document, as text (what input_digest is the digest of).
	AuthoredInput string
	// Materialized is the object the receipt describes.
	Materialized *Materialized
}

func isReceiptDigest(s string) bool {
	hex, ok := strings.CutPrefix(s, "sha256:")
	if !ok || len(hex) != 64 {
		return false
	}
	for i := 0; i < len(hex); i++ {
		c := hex[i]
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

func oneSegment(name string) bool { return logicalPath([]seg{{field: name}}) == "$."+name }

func intNode(i int64) *Node { return &Node{Kind: KindInt, Int: i} }

func mapNode(entries ...Entry) *Node { return &Node{Kind: KindMap, Entries: entries} }

func refusedReceipt(reason, field string) *ReceiptError {
	return &ReceiptError{Reason: reason, Field: field}
}

// ReceiptFlatFixture makes the receipt of the flat document doc against in, as a fixture (see the comment above). a and capabilities are as for MaterializeFlat.
//
// The order of the checks is part of the contract: the schema identity (compile, digest binding, namespace, kind), the schema version, the release fields (in the
// order of the struct), the engine fields, the presence of the document (before the capability map, so a missing document is named first), then the
// materialization, then the field names, then the canonical bytes.
func ReceiptFlatFixture(in SchemaInput, doc *Node, a []string, capabilities map[string]string, release ReleaseFixture, engine EngineIdentity) (*FlatReceipt, error) {
	compiled, err := CompileSchema(in)
	if err != nil {
		return nil, &ReceiptError{Reason: ReceiptIdentity, Cause: err}
	}
	// CompileSchema has verified in.Bytes against in.SHA256; the claim below is that same pin
	claimed := "sha256:" + in.SHA256
	name, digest, ierr := BindSchemaIdentity(in.Bytes, &claimed, &compiled.Namespace, &compiled.Kind)
	if ierr != nil {
		return nil, &ReceiptError{Reason: ReceiptIdentity, Cause: ierr}
	}
	if compiled.Version == "" {
		return nil, refusedReceipt(ReceiptSchemaVersionEmpty, "schema_version")
	}
	for _, c := range []struct {
		reason, field, value string
		digest               bool
	}{
		{ReceiptReleaseField, "primitive_release", release.PrimitiveRelease, false},
		{ReceiptReleaseField, "primitive_source_commit", release.PrimitiveSourceCommit, false},
		{ReceiptReleaseField, "primitive_build_hash", release.PrimitiveBuildHash, true},
		{ReceiptReleaseField, "grammar_sha256", release.GrammarSHA256, true},
		{ReceiptReleaseField, "grammar_schema_sha256", release.GrammarSchemaSHA256, true},
		{ReceiptEngineField, "engine_identity", engine.Identity, false},
		{ReceiptEngineField, "engine_version", engine.Version, false},
	} {
		if c.value == "" || (c.digest && !isReceiptDigest(c.value)) {
			return nil, refusedReceipt(c.reason, c.field)
		}
	}
	if doc == nil {
		return nil, &ReceiptError{Reason: ReceiptMaterialize, Cause: &MaterializeError{MaterializeNoDocument, ""}}
	}
	materialized, err := MaterializeFlat(doc, compiled.Root, a, capabilities)
	if err != nil {
		return nil, &ReceiptError{Reason: ReceiptMaterialize, Cause: err}
	}
	for _, f := range materialized.Fields {
		if !oneSegment(f.Key) {
			return nil, refusedReceipt(ReceiptPathNotOneSegment, f.Key)
		}
	}
	authored, err := CanonicalBytes(doc)
	if err != nil {
		return nil, &ReceiptError{Reason: ReceiptCanonical, Cause: err}
	}

	type def struct{ path, digest string }
	var defaults []def
	binding := map[string]string{}
	for _, f := range materialized.Fields {
		if f.Provenance != provenanceSchemaDefault {
			continue
		}
		path := "$." + f.Key
		bytes, err := CanonicalBytes(f.Value)
		if err != nil {
			return nil, &ReceiptError{Reason: ReceiptCanonical, Cause: err}
		}
		defaults = append(defaults, def{path, CanonicalDigest(bytes)})
		binding[path] = f.Key
	}
	// no sort: the fields are in byte order of the key, every path is `$.` and the key (one plain segment, so no escape changes a byte), and the order of the
	// paths is therefore the order of the keys; the engine's verifier refuses any other order (R-RECEIPT-ORDER, seen in the harness)
	applied := make([]*Node, 0, len(defaults))
	for _, d := range defaults {
		applied = append(applied, mapNode(
			Entry{Key: "path", Value: textNode(d.path)},
			Entry{Key: "rule", Value: textNode("schema-default")},
			Entry{Key: "value_digest", Value: textNode(d.digest)},
		))
	}

	body := mapNode(
		Entry{Key: "receipt_schema_version", Value: intNode(1)},
		Entry{Key: "grammar_sha256", Value: textNode(release.GrammarSHA256)},
		Entry{Key: "grammar_schema_sha256", Value: textNode(release.GrammarSchemaSHA256)},
		Entry{Key: "primitive_release", Value: textNode(release.PrimitiveRelease)},
		Entry{Key: "primitive_source_commit", Value: textNode(release.PrimitiveSourceCommit)},
		Entry{Key: "primitive_build_hash", Value: textNode(release.PrimitiveBuildHash)},
		Entry{Key: "schema_name", Value: textNode(name)},
		Entry{Key: "schema_version", Value: textNode(compiled.Version)},
		Entry{Key: "schema_digest", Value: textNode(digest)},
		Entry{Key: "engine_identity", Value: textNode(engine.Identity)},
		Entry{Key: "engine_version", Value: textNode(engine.Version)},
		Entry{Key: "input_digest", Value: textNode(CanonicalDigest(authored))},
		Entry{Key: "output_digest", Value: textNode(materialized.OutputDigest)},
		Entry{Key: "applied_defaults", Value: &Node{Kind: KindSeq, Items: applied}},
		Entry{Key: "derived_values", Value: &Node{Kind: KindSeq, Items: []*Node{}}},
	)
	bytes, err := CanonicalBytes(mapNode(Entry{Key: "materialization", Value: body}))
	if err != nil {
		return nil, &ReceiptError{Reason: ReceiptCanonical, Cause: err}
	}
	return &FlatReceipt{
		Document:      string(bytes),
		Digest:        CanonicalDigest(bytes),
		Binding:       binding,
		AuthoredInput: string(authored),
		Materialized:  materialized,
	}, nil
}
