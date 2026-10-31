package forms

import (
	"encoding/json"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The flat receipt (P5-3, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 17): the shared cases of testdata/p5/receipt.json (expected values computed by
// tools/p5/receipt_cases.py, in Python), and the refusals. The engine's verifier is run over the same receipts by tools/p5/engine-harness-go. The Rust peer is
// rust/tests/receipt_flat.rs.

const receiptTestIdentity = "cic-primitive-forms-go"

type receiptFile struct {
	Token         string            `json:"engine_identity_token"`
	EngineVersion string            `json:"engine_version"`
	Release       map[string]string `json:"release"`
	Cases         []struct {
		ID            string            `json:"id"`
		AuthoredInput string            `json:"authored_input"`
		Document      string            `json:"document"`
		Binding       map[string]string `json:"binding"`
	} `json:"cases"`
}

func loadReceiptCases(t *testing.T) receiptFile {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	b, err := os.ReadFile(filepath.Join(root, "testdata", "p5", "receipt.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f receiptFile
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	return f
}

func releaseOf(f receiptFile) ReleaseFixture {
	r := f.Release
	return ReleaseFixture{
		PrimitiveRelease:      r["primitive_release"],
		PrimitiveSourceCommit: r["primitive_source_commit"],
		PrimitiveBuildHash:    r["primitive_build_hash"],
		GrammarSHA256:         r["grammar_sha256"],
		GrammarSchemaSHA256:   r["grammar_schema_sha256"],
	}
}

// receiptBase is the first accepted case of the materialize file, to be altered one thing at a time.
type receiptBase struct {
	schema  string
	grammar string
	a       []string
	yaml    string
	caps    map[string]string
	release ReleaseFixture
	engine  EngineIdentity
}

func newReceiptBase(t *testing.T) *receiptBase {
	t.Helper()
	m := loadMaterialize(t)
	r := loadReceiptCases(t)
	c := m.Cases[0]
	return &receiptBase{
		schema: m.Schemas[c.Schema], grammar: m.Grammar, a: m.AccessKeys, yaml: c.YAML,
		caps:    maps.Clone(c.Capabilities),
		release: releaseOf(r),
		engine:  EngineIdentity{Identity: receiptTestIdentity, Version: r.EngineVersion},
	}
}

func (b *receiptBase) run(t *testing.T) (*FlatReceipt, error) {
	t.Helper()
	return b.runWith(t, b.schema, &b.yaml)
}

func (b *receiptBase) runWith(t *testing.T, schema string, yaml *string) (*FlatReceipt, error) {
	t.Helper()
	var doc *Node
	if yaml != nil {
		var err error
		if doc, err = Parse(*yaml); err != nil {
			t.Fatal(err)
		}
	}
	text := []byte(schema)
	return ReceiptFlatFixture(SchemaInput{Bytes: text, SHA256: SHA256Hex(text), Grammar: b.grammar, Surface: "config_surface"}, doc, b.a, b.caps, b.release, b.engine)
}

func refusalOf(t *testing.T, err error) (string, string) {
	t.Helper()
	var re *ReceiptError
	if !errors.As(err, &re) {
		t.Fatalf("not a receipt error: %v", err)
	}
	return re.Reason, re.Field
}

func TestSharedReceiptCases(t *testing.T) {
	m := loadMaterialize(t)
	r := loadReceiptCases(t)
	n := 0
	for _, c := range r.Cases {
		var mc int = -1
		for i, x := range m.Cases {
			if x.ID == c.ID {
				mc = i
			}
		}
		if mc < 0 {
			t.Fatalf("%s: no materialize case", c.ID)
		}
		mcase := m.Cases[mc]
		text := []byte(m.Schemas[mcase.Schema])
		doc, err := Parse(mcase.YAML)
		if err != nil {
			t.Fatal(err)
		}
		got, err := ReceiptFlatFixture(SchemaInput{Bytes: text, SHA256: SHA256Hex(text), Grammar: m.Grammar, Surface: "config_surface"}, doc, m.AccessKeys, mcase.Capabilities,
			releaseOf(r), EngineIdentity{Identity: receiptTestIdentity, Version: r.EngineVersion})
		if err != nil {
			t.Fatalf("%s: %v", c.ID, err)
		}
		want := strings.ReplaceAll(c.Document, r.Token, receiptTestIdentity)
		if got.Document != want {
			t.Fatalf("%s: the canonical bytes\n got %s\nwant %s", c.ID, got.Document, want)
		}
		if got.Digest != "sha256:"+SHA256Hex([]byte(want)) {
			t.Fatalf("%s: the digest", c.ID)
		}
		if got.AuthoredInput != c.AuthoredInput {
			t.Fatalf("%s: authored input %s", c.ID, got.AuthoredInput)
		}
		if !maps.Equal(got.Binding, c.Binding) {
			t.Fatalf("%s: binding %v", c.ID, got.Binding)
		}
		if got.Materialized.ProjectionJSON != mcase.Expect.Projection {
			t.Fatalf("%s: the object the receipt describes", c.ID)
		}
		n++
	}
	if n != 7 {
		t.Fatalf("%d cases", n)
	}
}

func TestReceiptNamesTheSchemaItWasMadeFrom(t *testing.T) {
	b := newReceiptBase(t)
	first, err := b.run(t)
	if err != nil {
		t.Fatal(err)
	}
	other := strings.Replace(b.schema, "version: v1.0.0", "version: v1.0.0 ", 1)
	second, err := b.runWith(t, other, &b.yaml)
	if err != nil {
		t.Fatal(err)
	}
	if first.Document == second.Document {
		t.Fatal("another schema file gave the same receipt")
	}
	if !strings.Contains(second.Document, `"schema_digest":"sha256:`+SHA256Hex([]byte(other))+`"`) {
		t.Fatal("the digest is not the digest of the schema file that was read")
	}
}

func TestSchemaThatIsNotThePinnedBytesMakesNoReceipt(t *testing.T) {
	b := newReceiptBase(t)
	altered := []byte(strings.Replace(b.schema, "kind: Demo", "kind: Demx", 1))
	doc, _ := Parse(b.yaml)
	_, err := ReceiptFlatFixture(SchemaInput{Bytes: altered, SHA256: SHA256Hex([]byte(b.schema)), Grammar: b.grammar, Surface: "config_surface"}, doc, b.a, b.caps, b.release, b.engine)
	if reason, _ := refusalOf(t, err); reason != ReceiptIdentity {
		t.Fatalf("reason %s", reason)
	}
	var re *ReceiptError
	errors.As(err, &re)
	var ierr *SchemaIdentityError
	if errors.As(err, &ierr) {
		t.Fatal("a pin mismatch is the schema's error, not an identity rule")
	}
}

func TestSchemaWhoseIdentityBreaksTheContractMakesNoReceipt(t *testing.T) {
	b := newReceiptBase(t)
	bad := strings.Replace(b.schema, `namespace: "cic:demo"`, `namespace: "cic::demo"`, 1)
	_, err := b.runWith(t, bad, &b.yaml)
	var ierr *SchemaIdentityError
	if !errors.As(err, &ierr) || ierr.Code != SchemaInvalidIdentity || ierr.Rule != "R-SCHEMA-NAME-NAMESPACE" {
		t.Fatalf("%v", err)
	}
	if reason, _ := refusalOf(t, err); reason != ReceiptIdentity {
		t.Fatalf("reason %s", reason)
	}
}

func TestEmptyOrMissingSchemaVersionMakesNoReceipt(t *testing.T) {
	b := newReceiptBase(t)
	for _, schema := range []string{
		strings.Replace(b.schema, "version: v1.0.0", `version: ""`, 1),
		strings.Replace(b.schema, "    version: v1.0.0\n", "", 1),
	} {
		_, err := b.runWith(t, schema, &b.yaml)
		if reason, field := refusalOf(t, err); reason != ReceiptSchemaVersionEmpty || field != "schema_version" {
			t.Fatalf("%s %s", reason, field)
		}
	}
}

func TestEveryReleaseAndEngineFieldIsRequiredAndTheDigestsHaveTheirForm(t *testing.T) {
	digestOK := "sha256:" + strings.Repeat("a", 64)
	edits := []struct {
		name, reason string
		digest       bool
		set          func(*receiptBase, string)
	}{
		{"primitive_release", ReceiptReleaseField, false, func(b *receiptBase, v string) { b.release.PrimitiveRelease = v }},
		{"primitive_source_commit", ReceiptReleaseField, false, func(b *receiptBase, v string) { b.release.PrimitiveSourceCommit = v }},
		{"primitive_build_hash", ReceiptReleaseField, true, func(b *receiptBase, v string) { b.release.PrimitiveBuildHash = v }},
		{"grammar_sha256", ReceiptReleaseField, true, func(b *receiptBase, v string) { b.release.GrammarSHA256 = v }},
		{"grammar_schema_sha256", ReceiptReleaseField, true, func(b *receiptBase, v string) { b.release.GrammarSchemaSHA256 = v }},
		{"engine_identity", ReceiptEngineField, false, func(b *receiptBase, v string) { b.engine.Identity = v }},
		{"engine_version", ReceiptEngineField, false, func(b *receiptBase, v string) { b.engine.Version = v }},
	}
	for _, e := range edits {
		b := newReceiptBase(t)
		e.set(b, "")
		_, err := b.run(t)
		if reason, field := refusalOf(t, err); reason != e.reason || field != e.name {
			t.Fatalf("%s empty: %s %s", e.name, reason, field)
		}
		for _, bad := range []string{strings.ToUpper(digestOK[:6]) + digestOK[6:], digestOK[len("sha256:"):], "sha256:" + strings.Repeat("a", 63)} {
			b := newReceiptBase(t)
			e.set(b, bad)
			_, err := b.run(t)
			if e.digest {
				if reason, field := refusalOf(t, err); reason != e.reason || field != e.name {
					t.Fatalf("%s form: %s %s", e.name, reason, field)
				}
			} else if err != nil {
				t.Fatalf("%s: a free-form text is not a digest: %v", e.name, err)
			}
		}
		b = newReceiptBase(t)
		e.set(b, digestOK)
		if _, err := b.run(t); err != nil {
			t.Fatalf("%s: a well-formed value: %v", e.name, err)
		}
	}
}

func TestFieldNameThatIsNotOnePlainSegmentMakesNoReceipt(t *testing.T) {
	for _, name := range []string{"a.b", "a[0]", "a{b", "a=b", "a,b", `a\b`, "a]"} {
		b := newReceiptBase(t)
		quoted := strings.ReplaceAll(name, `\`, `\\`)
		schema := strings.Replace(b.schema, "      - name: type\n",
			"      - name: \""+quoted+"\"\n        shape_type: scalar\n        scalar_type: string\n        mandatory: true\n      - name: type\n", 1)
		b.caps[name] = "implemented"
		yaml := "\"" + quoted + "\": v\ntype: t\n"
		_, err := b.runWith(t, schema, &yaml)
		if reason, field := refusalOf(t, err); reason != ReceiptPathNotOneSegment || field != name {
			t.Fatalf("%q: %s %s (%v)", name, reason, field, err)
		}
	}
}

func TestMaterializationRefusalsComeThroughUnchanged(t *testing.T) {
	b := newReceiptBase(t)
	_, err := b.runWith(t, b.schema, nil)
	var me *MaterializeError
	if !errors.As(err, &me) || me.Reason != MaterializeNoDocument {
		t.Fatalf("%v", err)
	}
	delete(b.caps, "mtu")
	_, err = b.run(t)
	if !errors.As(err, &me) || me.Reason != MaterializeCapabilityMissing || me.Field != "mtu" {
		t.Fatalf("%v", err)
	}
	if reason, _ := refusalOf(t, err); reason != ReceiptMaterialize {
		t.Fatalf("reason %s", reason)
	}
}

func TestOrderOfTheChecksNamesTheFirstReason(t *testing.T) {
	b := newReceiptBase(t)
	b.release.PrimitiveRelease = ""
	b.engine.Identity = ""
	b.caps = map[string]string{}
	badNS := strings.Replace(b.schema, `namespace: "cic:demo"`, `namespace: ""`, 1)
	_, err := b.runWith(t, badNS, nil)
	var ierr *SchemaIdentityError
	if !errors.As(err, &ierr) {
		t.Fatalf("%v", err)
	}
	_, err = b.runWith(t, b.schema, nil)
	if _, field := refusalOf(t, err); field != "primitive_release" {
		t.Fatal(field)
	}
	b.release.PrimitiveRelease = "x"
	_, err = b.runWith(t, b.schema, nil)
	if _, field := refusalOf(t, err); field != "engine_identity" {
		t.Fatal(field)
	}
	b.engine.Identity = "x"
	_, err = b.runWith(t, b.schema, nil)
	var me *MaterializeError
	if !errors.As(err, &me) || me.Reason != MaterializeNoDocument {
		t.Fatalf("%v", err)
	}
}

func TestReceiptErrorSaysWhatItIsAbout(t *testing.T) {
	plain := (&ReceiptError{Reason: ReceiptEngineField, Field: "engine_version"}).Error()
	if plain != "no receipt: engine_field (engine_version)" {
		t.Fatal(plain)
	}
	cause := errors.New("below")
	wrapped := &ReceiptError{Reason: ReceiptMaterialize, Cause: cause}
	if wrapped.Error() != "no receipt: materialize: below" || !errors.Is(wrapped, cause) {
		t.Fatal(wrapped.Error())
	}
}

func TestADigestHasOnlyLowercaseHexCharacters(t *testing.T) {
	// the wrong character at the first, a middle and the last position: a check that skips the start or the end must not pass
	for _, c := range []string{"g", "A", "/", "G", ":"} {
		for _, at := range []int{0, 31, 63} {
			bad := "sha256:" + strings.Repeat("a", at) + c + strings.Repeat("a", 63-at)
			if isReceiptDigest(bad) {
				t.Fatalf("%q", bad)
			}
		}
	}
	if !isReceiptDigest("sha256:" + strings.Repeat("0123456789abcdef", 4)) {
		t.Fatal("a digest")
	}
}
