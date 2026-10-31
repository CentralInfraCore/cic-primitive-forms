package forms

import "strings"

// The schema identity of a receipt (D1.3; P5-3, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 16). The Rust peer is rust/src/schema_identity.rs.
//
// This is this library's side of the contract that cic-primitive-engine fixed in its PR #64 (docs/RECEIPT-SCHEMA.md, "Schema identity contract"), under the same
// names, rules and codes, so that the engine's golden and negative vectors run unchanged through it:
//
//   - schema_digest is "sha256:" and the lowercase hex of the SHA-256 of the raw bytes of the pinned schema file. Nothing is parsed, normalized or
//     re-serialized first; the line endings and the final newline are part of the identity.
//   - schema_name is identity.namespace + ":" + identity.kind, as authored: both non-empty, the namespace made of non-empty ":"-separated components, the kind
//     without a colon, nothing trimmed or case-folded.
//   - The order of the checks is the digest binding, then the namespace, then the kind.
//
// BindSchemaIdentity is the contract on loose values, as the engine has it. It cannot know that the namespace and the kind were read from the bytes it is given,
// and the engine's review made that binding mandatory for the receipt. SchemaIdentityOf is where it is made true: it takes the SchemaInput (the bytes and their
// pinned digest), compiles it (which verifies the bytes against the pin and reads the identity from those bytes), and builds the name, the version and the digest
// from that one compile. There is no way to hand it a component from another file.
//
// This establishes what two receipt fields mean and that they come from one verified source. It does not establish where the source came from.

// The codes of the contract, spelled as the engine spells them.
const (
	SchemaInvalidIdentity = "E_INVALID_SCHEMA_IDENTITY"
	SchemaSourceBinding   = "E_SCHEMA_SOURCE_BINDING"
)

const (
	schemaNamespacePath = "$.spec.identity.namespace"
	schemaKindPath      = "$.spec.identity.kind"
	schemaDigestPath    = "$.schema_digest"
)

// SchemaIdentityError is why an identity was refused: the code and the rule of the engine's contract, and the path it names.
type SchemaIdentityError struct {
	Code string
	Rule string
	Path string
}

func (e *SchemaIdentityError) Error() string { return e.Code + " (" + e.Rule + ") at " + e.Path }

// SchemaIdentity is the three schema fields of a receipt, from one verified source.
type SchemaIdentity struct {
	SchemaName string
	// SchemaVersion is identity.version, as authored.
	SchemaVersion string
	SchemaDigest  string
}

func invalidIdentity(rule, path string) *SchemaIdentityError {
	return &SchemaIdentityError{Code: SchemaInvalidIdentity, Rule: rule, Path: path}
}

func bindingIdentity(rule string) *SchemaIdentityError {
	return &SchemaIdentityError{Code: SchemaSourceBinding, Rule: rule, Path: schemaDigestPath}
}

// SchemaDigest is "sha256:" and the lowercase hex SHA-256 of source, over the bytes as they are.
func SchemaDigest(source []byte) string { return CanonicalDigest(source) }

// SchemaName is namespace + ":" + kind, exactly as given, if both are well formed. A nil pointer is an absent component.
func SchemaName(namespace, kind *string) (string, *SchemaIdentityError) {
	if namespace == nil || *namespace == "" || hasEmptyNamespaceComponent(*namespace) {
		return "", invalidIdentity("R-SCHEMA-NAME-NAMESPACE", schemaNamespacePath)
	}
	if kind == nil || *kind == "" || strings.Contains(*kind, ":") {
		return "", invalidIdentity("R-SCHEMA-NAME-KIND", schemaKindPath)
	}
	return *namespace + ":" + *kind, nil
}

func hasEmptyNamespaceComponent(s string) bool {
	for _, part := range strings.Split(s, ":") {
		if part == "" {
			return true
		}
	}
	return false
}

// BindSchemaIdentity is the engine's contract on loose values: claimedDigest must be the digest of source, then the name is formed. It returns the name and the
// digest. A nil claimedDigest is no claim.
func BindSchemaIdentity(source []byte, claimedDigest, namespace, kind *string) (name, digest string, err *SchemaIdentityError) {
	if claimedDigest == nil {
		return "", "", bindingIdentity("R-SCHEMA-DIGEST-MISSING")
	}
	actual := SchemaDigest(source)
	if *claimedDigest != actual {
		return "", "", bindingIdentity("R-SCHEMA-DIGEST-MISMATCH")
	}
	name, err = SchemaName(namespace, kind)
	if err != nil {
		return "", "", err
	}
	return name, actual, nil
}

// SchemaIdentityOf is the schema identity of in, with the namespace, the kind and the version read from the very bytes whose digest is stated. A compile
// failure is returned as the *Error of CompileSchema; an identity that breaks the contract as a *SchemaIdentityError.
func SchemaIdentityOf(in SchemaInput) (SchemaIdentity, error) {
	compiled, err := CompileSchema(in)
	if err != nil {
		return SchemaIdentity{}, err
	}
	// CompileSchema has verified in.Bytes against in.SHA256; the claim below is that same pin, so the digest bound is the one of the bytes read
	claimed := "sha256:" + in.SHA256
	name, digest, ierr := BindSchemaIdentity(in.Bytes, &claimed, &compiled.Namespace, &compiled.Kind)
	if ierr != nil {
		return SchemaIdentity{}, ierr
	}
	return SchemaIdentity{SchemaName: name, SchemaVersion: compiled.Version, SchemaDigest: digest}, nil
}
