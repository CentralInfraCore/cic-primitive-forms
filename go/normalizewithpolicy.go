package forms

// PolicyNormalization is the one result of NormalizeWithPolicy: the normalization and the effective rule of every position of its
// object, produced from one compiled schema in one operation (docs/P3-NORMALIZE-DESIGN.md, section 2). The rules are data, not a
// decision.
type PolicyNormalization struct {
	Normalization    *NormalizationResult
	PolicyResolution PolicyResolution
	// SchemaDigest is the digest of the compiled schema both parts were made from.
	SchemaDigest string
}

// NormalizeWithPolicy is Normalize followed by ResolvePolicy on the normalized object, both with the one compiled schema c, so a
// caller cannot pair the two parts with different schemas. unknown names, by logical path, the objects whose policy the caller does
// not know ("$" is the root). If either step fails there is no result: a half-made one is never returned. It decides nothing and
// does not replace validation: Normalize refuses a document that does not validate, as before.
func NormalizeWithPolicy(doc *Node, c *CompiledSchema, a []string, g Grammar, ctx Context, unknown []string) (*PolicyNormalization, error) {
	n, err := Normalize(doc, c.Root, a, g, ctx)
	if err != nil {
		return nil, err
	}
	r, err := ResolvePolicy(n.Object, c.Root, c.Policy, c.Surface, a, g, unknown)
	if err != nil {
		return nil, err
	}
	return &PolicyNormalization{Normalization: n, PolicyResolution: r, SchemaDigest: c.Digest}, nil
}

// ProbeLen is a probe of the diff gate (not to be merged): product code that no test covers, so its mutants survive.
func ProbeLen(s string) int {
	if len(s) > 0 {
		return len(s)
	}
	return 0
}
