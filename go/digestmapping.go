package forms

import (
	"encoding/base64"
	"encoding/hex"
	"strings"
)

// The release record to receipt digest mapping (P5-3, condition 8; docs/P5-DIGEST-MAPPING-CONTRACT.md). The Rust peer is rust/src/digest_mapping.rs; the shared cases are
// testdata/p5/digest-mapping.json, written by tools/p5/digest_mapping_cases.py, whose Python reference is tools/p5/digest_mapping.py.
//
// A pure function over a record that somebody else has already authenticated. It verifies no signature, reads no certificate, trust anchor or clock, opens no file, and
// says nothing about the release being genuine: a record with a forged signature maps all the same, on purpose (contract section 7). It is not a production receipt
// function and takes no part in one; that needs a verified release record and stays on hold (question L).
//
// Every receipt value has ONE primary source and every other place that holds it is a check; there is no choice between two sources. The primary source of the two
// grammar digests is the artifacts.files entry of a fixed path (decision D1), the cross-checks are provenance.grammar_* (decision D2: absent is a refusal, not a
// skipped check) and the pin, and only an envelope 3 record is read (decision D3).
//
// What it refuses (a *MappingError with one reason and the field it concerns, never a guess and never a value with a warning): see the Mapping* constants. The checks run
// in the order of the contract, section 5, so a case has one expected reason.

// The reasons of a *MappingError.
const (
	MappingEnvelopeNot3              = "envelope_not_3"
	MappingFieldMissing              = "field_missing"
	MappingFieldType                 = "field_type"
	MappingBase64Shape               = "base64_shape"
	MappingBase64NotCanonical        = "base64_not_canonical"
	MappingBase64Length              = "base64_length"
	MappingDigestForm                = "digest_form"
	MappingArtifactAbsent            = "artifact_absent"
	MappingArtifactDuplicate         = "artifact_duplicate"
	MappingProvenanceArtifactsDiffer = "provenance_artifacts_disagree"
	MappingRecordPinDiffer           = "record_pin_disagree"
)

// The artifacts the two grammar digests are read from, and the provenance members that have to agree with them.
const (
	mappingCheckGrammar  = "proposals/atom-grammar/check_grammar.py"
	mappingGrammarSchema = "proposals/atom-grammar/instance-grammar.schema.yaml"
)

// MappingError says why no receipt digest was made. Field is the member, the artifact path or the receipt field the refusal is about.
type MappingError struct {
	Reason string
	Field  string
}

func (e *MappingError) Error() string {
	s := "no digest mapping: " + e.Reason
	if e.Field != "" {
		s += " (" + e.Field + ")"
	}
	return s
}

// DigestMapping is the three receipt digests, each `sha256:` and 64 lowercase hex.
type DigestMapping struct {
	PrimitiveBuildHash  string
	GrammarSHA256       string
	GrammarSchemaSHA256 string
}

const base64Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

// base64Shape reports whether s is, as a whole, 43 characters of the standard alphabet and one padding character: the shape of a SHA-256 in base64. It is checked
// before any decoder runs, because Go's decoders ignore a final newline and accept more spellings than the contract does.
func base64Shape(s string) bool {
	if len(s) != 44 || s[43] != '=' {
		return false
	}
	for i := 0; i < 43; i++ {
		if strings.IndexByte(base64Alphabet, s[i]) < 0 {
			return false
		}
	}
	return true
}

// checkDigestBytes judges the bytes a text decoded to: exactly 32 (the shape already implies it; the check guards a decoder that returns another length), and the text
// is the one canonical spelling of them. It is a function of its own so that the length guard, which no input can reach through the shape check, is exercised directly.
func checkDigestBytes(raw []byte, text, field string) *MappingError {
	if len(raw) != 32 {
		return &MappingError{MappingBase64Length, field}
	}
	if base64.StdEncoding.EncodeToString(raw) != text {
		return &MappingError{MappingBase64NotCanonical, field}
	}
	return nil
}

// ReceiptDigestOf is the conversion of the contract, section 3: a base64 SHA-256 of exactly 32 bytes, in its one canonical spelling, as `sha256:` and lowercase hex.
// field names the member the value was read from and goes into the refusal. The value is judged exactly as it is: nothing is trimmed, folded or coerced.
func ReceiptDigestOf(value *Node, field string) (string, error) {
	if value == nil || value.Kind != KindStr {
		return "", &MappingError{MappingFieldType, field}
	}
	text := value.Str
	if !base64Shape(text) {
		return "", &MappingError{MappingBase64Shape, field}
	}
	// the shape made the text a valid base64 string of 44 characters: the decoder cannot fail on it
	raw, _ := base64.StdEncoding.DecodeString(text)
	if err := checkDigestBytes(raw, text, field); err != nil {
		return "", err
	}
	return "sha256:" + hex.EncodeToString(raw), nil
}

// mappingDigestMember converts the base64 digest at a member of the record: an absent or null member is field_missing (the presence is judged first), anything else is
// left to ReceiptDigestOf.
func mappingDigestMember(record *Node, member string) (string, error) {
	v := mappingGet(record, member)
	if v == nil || v.Kind == KindNull {
		return "", &MappingError{MappingFieldMissing, member}
	}
	return ReceiptDigestOf(v, member)
}

// mappingGet is the value at a dotted path of nested mappings, or nil when a member on the way is absent or the parent is not a mapping.
func mappingGet(record *Node, path string) *Node {
	cur := record
	for _, part := range strings.Split(path, ".") {
		if cur == nil || cur.Kind != KindMap {
			return nil
		}
		var next *Node
		for _, e := range cur.Entries {
			if e.Key == part {
				next = e.Value
				break
			}
		}
		if next == nil {
			return nil
		}
		cur = next
	}
	return cur
}

// artifactDigest is the digest of the one artifacts.files entry of path, as it is.
func artifactDigest(files []*Node, path string) (string, error) {
	var found []*Node
	for _, f := range files {
		if f != nil && f.Kind == KindMap {
			if p := mappingGet(f, "path"); p != nil && p.Kind == KindStr && p.Str == path {
				found = append(found, f)
			}
		}
	}
	if len(found) == 0 {
		return "", &MappingError{MappingArtifactAbsent, path}
	}
	if len(found) > 1 {
		return "", &MappingError{MappingArtifactDuplicate, path}
	}
	field := "artifacts.files[" + path + "].digest"
	digest := mappingGet(found[0], "digest")
	if digest == nil || digest.Kind != KindStr {
		return "", &MappingError{MappingFieldType, field}
	}
	if !isReceiptDigest(digest.Str) {
		return "", &MappingError{MappingDigestForm, field}
	}
	return digest.Str, nil
}

// MapRelease maps an authenticated envelope 3 release record to the three receipt digests, or refuses with one reason (contract sections 4 and 5). pin is
// {path: 64 lowercase hex}, the digests of the manifest of corpus/pins.yaml without the `sha256:` prefix.
func MapRelease(record *Node, pin map[string]string) (*DigestMapping, error) {
	for _, member := range []string{"release.envelope", "provenance.envelope"} {
		v := mappingGet(record, member)
		if v == nil || v.Kind != KindInt || v.Int != 3 {
			return nil, &MappingError{MappingEnvelopeNot3, member}
		}
	}

	buildHash, err := mappingDigestMember(record, "release.build_hash")
	if err != nil {
		return nil, err
	}

	filesNode := mappingGet(record, "artifacts.files")
	if filesNode == nil || filesNode.Kind == KindNull {
		return nil, &MappingError{MappingFieldMissing, "artifacts.files"}
	}
	if filesNode.Kind != KindSeq {
		return nil, &MappingError{MappingFieldType, "artifacts.files"}
	}

	out := &DigestMapping{PrimitiveBuildHash: buildHash}
	for _, g := range []struct {
		receiptField, path, member string
		into                       *string
	}{
		{"grammar_sha256", mappingCheckGrammar, "grammar_sha256", &out.GrammarSHA256},
		{"grammar_schema_sha256", mappingGrammarSchema, "grammar_schema_sha256", &out.GrammarSchemaSHA256},
	} {
		primary, err := artifactDigest(filesNode.Items, g.path)
		if err != nil {
			return nil, err
		}
		where := "provenance." + g.member
		cross, err := mappingDigestMember(record, where)
		if err != nil {
			return nil, err
		}
		if cross != primary {
			return nil, &MappingError{MappingProvenanceArtifactsDiffer, g.receiptField}
		}
		pinned, ok := pin[g.path]
		if !ok {
			return nil, &MappingError{MappingFieldMissing, "pin." + g.path}
		}
		if "sha256:"+pinned != primary {
			return nil, &MappingError{MappingRecordPinDiffer, g.receiptField}
		}
		*g.into = primary
	}
	return out, nil
}
