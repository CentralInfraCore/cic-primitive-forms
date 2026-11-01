package forms_test

// P5-3 end-to-end proof WITHOUT REFERENCE RESOLUTION (docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 21).
//
// One execution chain per case: the schema source bytes -> the verified compile and the identity; the authored document -> parse -> the standalone materialization ->
// ReceiptFlatFixture. The receipt builder materializes again inside, so TWO materializations happen; the chain compares them field by field and states both. Every
// value that leaves the chain (the dump for tools/parity/check_receipt_e2e.py and the engine harnesses) comes from the same run.
//
// The capability map is an explicit caller input (the P5-2 contract): where it comes from is not claimed to be authenticated. The expected values of the cases are
// computed in Python (tools/p5/end_to_end_cases.py). With CIC_E2E_DUMP set, the test writes the chain of every case to that file. The Rust peer is
// rust/tests/receipt_end_to_end.rs.

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

const e2eIdentity = "cic-primitive-forms-go"

type e2eCase struct {
	ID     string `json:"id"`
	Schema string `json:"schema"`
	SHA256 string `json:"sha256"`
	YAML   string `json:"yaml"`
	Base   e2eRun `json:"base"`
	Alt    e2eRun `json:"alt"`
}

type e2eRun struct {
	Capabilities  map[string]string `json:"capabilities"`
	ExpectedKeys  []string          `json:"expected_keys"`
	Projection    string            `json:"projection"`
	OutputDigest  string            `json:"output_digest"`
	AuthoredInput string            `json:"authored_input"`
	InputDigest   string            `json:"input_digest"`
	Document      string            `json:"document"`
	Binding       map[string]string `json:"binding"`
}

type e2eFixture struct {
	Token         string   `json:"engine_identity_token"`
	EngineVersion string   `json:"engine_version"`
	Grammar       string   `json:"grammar"`
	AccessKeys    []string `json:"access_keys"`
	Release       struct {
		PrimitiveRelease      string `json:"primitive_release"`
		PrimitiveSourceCommit string `json:"primitive_source_commit"`
		PrimitiveBuildHash    string `json:"primitive_build_hash"`
		GrammarSHA256         string `json:"grammar_sha256"`
		GrammarSchemaSHA256   string `json:"grammar_schema_sha256"`
	} `json:"release"`
	Cases []e2eCase `json:"cases"`
}

func e2eRoot() string {
	if r := os.Getenv("CIC_FORMS_ROOT"); r != "" {
		return r
	}
	return ".."
}

func e2eLoad(t *testing.T) e2eFixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(e2eRoot(), "testdata", "p5", "end-to-end.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fx e2eFixture
	if err := json.Unmarshal(raw, &fx); err != nil {
		t.Fatal(err)
	}
	return fx
}

// e2eChain is what every step of one execution produced.
type e2eChain struct {
	identity   forms.SchemaIdentity
	authored   string
	standalone *forms.Materialized
	receipt    *forms.FlatReceipt
}

// e2eStop is where a chain stopped, and why.
type e2eStop struct {
	stage string
	err   error
}

func (s *e2eStop) Error() string { return s.stage + ": " + s.err.Error() }

func (fx e2eFixture) input(schema, sha string) forms.SchemaInput {
	return forms.SchemaInput{Bytes: []byte(schema), SHA256: sha, Grammar: fx.Grammar, Surface: "config_surface"}
}

func (fx e2eFixture) release() forms.ReleaseFixture {
	return forms.ReleaseFixture(fx.Release)
}

func e2eSameObject(a, b *forms.Materialized) bool {
	if !slices.Equal(a.ExpectedKeys, b.ExpectedKeys) || a.ProjectionJSON != b.ProjectionJSON || a.OutputDigest != b.OutputDigest || len(a.Fields) != len(b.Fields) {
		return false
	}
	for i := range a.Fields {
		x, y := a.Fields[i], b.Fields[i]
		xb, _ := forms.CanonicalBytes(x.Value)
		yb, _ := forms.CanonicalBytes(y.Value)
		if x.Key != y.Key || x.Capability != y.Capability || x.Provenance != y.Provenance || !bytes.Equal(xb, yb) {
			return false
		}
	}
	return true
}

// e2eRunChain is the chain of ONE execution. The schema is compiled and its identity bound once for the step; the document is parsed once and that very node is
// what the standalone materialization and the receipt builder are given. Nothing is taken from a file but the inputs.
func e2eRunChain(fx e2eFixture, schema, sha, yaml string, caps map[string]string) (*e2eChain, *e2eStop) {
	in := fx.input(schema, sha)
	identity, err := forms.SchemaIdentityOf(in)
	if err != nil {
		return nil, &e2eStop{"identity", err}
	}
	compiled, err := forms.CompileSchema(in)
	if err != nil {
		return nil, &e2eStop{"identity", err}
	}
	doc, err := forms.Parse(yaml)
	if err != nil {
		return nil, &e2eStop{"parse", err}
	}
	authoredBytes, err := forms.CanonicalBytes(doc)
	if err != nil {
		return nil, &e2eStop{"canonical", err}
	}
	standalone, err := forms.MaterializeFlat(doc, compiled.Root, fx.AccessKeys, caps)
	if err != nil {
		return nil, &e2eStop{"materialize", err}
	}
	receipt, err := forms.ReceiptFlatFixture(in, doc, fx.AccessKeys, caps, fx.release(), forms.EngineIdentity{Identity: e2eIdentity, Version: fx.EngineVersion})
	if err != nil {
		return nil, &e2eStop{"receipt", err}
	}
	return &e2eChain{identity, string(authoredBytes), standalone, receipt}, nil
}

func e2eJSON(t *testing.T, text string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(text), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

// e2eLink checks the links of the chain: each step's output is the next step's input, and the receipt carries exactly what the earlier steps produced. It returns
// the body of the receipt.
func e2eLink(t *testing.T, id string, c *e2eChain) map[string]any {
	t.Helper()
	if !e2eSameObject(c.standalone, c.receipt.Materialized) {
		t.Fatalf("%s: the standalone materialization and the one inside the receipt builder differ", id)
	}
	if c.receipt.AuthoredInput != c.authored {
		t.Fatalf("%s: the authored input", id)
	}
	body := e2eJSON(t, c.receipt.Document)["materialization"].(map[string]any)
	for k, want := range map[string]string{
		"schema_name":    c.identity.SchemaName,
		"schema_version": c.identity.SchemaVersion,
		"schema_digest":  c.identity.SchemaDigest,
		"input_digest":   forms.CanonicalDigest([]byte(c.authored)),
		"output_digest":  c.standalone.OutputDigest,
	} {
		if body[k] != want {
			t.Fatalf("%s: %s is %v, the chain made %s", id, k, body[k], want)
		}
	}
	var defaulted []string
	for _, f := range c.standalone.Fields {
		if f.Provenance == "schema_default" {
			defaulted = append(defaulted, "$."+f.Key)
		}
	}
	var paths []string
	for _, d := range body["applied_defaults"].([]any) {
		paths = append(paths, d.(map[string]any)["path"].(string))
	}
	if !slices.Equal(paths, defaulted) {
		t.Fatalf("%s: the evidence is %v, the defaulted fields are %v", id, paths, defaulted)
	}
	keys := slices.Sorted(maps.Keys(c.receipt.Binding))
	if !slices.Equal(keys, defaulted) {
		t.Fatalf("%s: the binding covers %v, the evidence %v", id, keys, defaulted)
	}
	for path, key := range c.receipt.Binding {
		if path != "$."+key {
			t.Fatalf("%s: the binding %s -> %s is not $.field -> field", id, path, key)
		}
	}
	return body
}

// e2eRecord is the record of one run, as the dump and the checker see it. chain binds the schema, the input, the output and the receipt of THIS run, and its
// capability map.
func e2eRecord(t *testing.T, c *e2eChain, caps map[string]string) map[string]any {
	t.Helper()
	capsJSON, err := json.Marshal(caps)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s\n%s\n%s\n%s\n%s", c.identity.SchemaDigest, forms.CanonicalDigest([]byte(c.authored)), c.standalone.OutputDigest, c.receipt.Digest, capsJSON)))
	r := c.receipt
	return map[string]any{
		"capabilities":   caps,
		"schema_name":    c.identity.SchemaName,
		"schema_version": c.identity.SchemaVersion,
		"schema_digest":  c.identity.SchemaDigest,
		"authored_input": c.authored,
		"standalone": map[string]any{
			"projection":    c.standalone.ProjectionJSON,
			"expected_keys": c.standalone.ExpectedKeys,
			"output_digest": c.standalone.OutputDigest,
		},
		"receipt": map[string]any{
			"document":       r.Document,
			"digest":         r.Digest,
			"binding":        r.Binding,
			"authored_input": r.AuthoredInput,
			"projection":     r.Materialized.ProjectionJSON,
			"output_digest":  r.Materialized.OutputDigest,
		},
		"chain": hex.EncodeToString(sum[:]),
	}
}

func e2eAgainstPython(t *testing.T, fx e2eFixture, id, which string, c *e2eChain, want e2eRun) {
	t.Helper()
	if c.standalone.ProjectionJSON != want.Projection || c.standalone.OutputDigest != want.OutputDigest || c.authored != want.AuthoredInput {
		t.Fatalf("%s %s: the object or the authored input differs from the Python reference", id, which)
	}
	if wantDoc := strings.ReplaceAll(want.Document, fx.Token, e2eIdentity); c.receipt.Document != wantDoc {
		t.Fatalf("%s %s: the receipt\n got %s\nwant %s", id, which, c.receipt.Document, wantDoc)
	}
	if !maps.Equal(c.receipt.Binding, want.Binding) {
		t.Fatalf("%s %s: the binding %v, the reference %v", id, which, c.receipt.Binding, want.Binding)
	}
	if !slices.Equal(c.standalone.ExpectedKeys, want.ExpectedKeys) {
		t.Fatalf("%s %s: the expected keys", id, which)
	}
}

func TestEndToEndAgainstThePythonReference(t *testing.T) {
	fx := e2eLoad(t)
	var dump bytes.Buffer
	for _, c := range fx.Cases {
		records := map[string]any{"id": c.ID}
		for _, which := range []struct {
			name string
			run  e2eRun
		}{{"base", c.Base}, {"alt", c.Alt}} {
			chain, stop := e2eRunChain(fx, c.Schema, c.SHA256, c.YAML, which.run.Capabilities)
			if stop != nil {
				t.Fatalf("%s: %v", c.ID, stop)
			}
			e2eLink(t, c.ID, chain)
			e2eAgainstPython(t, fx, c.ID, which.name, chain, which.run)
			if chain.identity.SchemaDigest != "sha256:"+c.SHA256 {
				t.Fatalf("%s: the schema digest", c.ID)
			}
			records[which.name] = e2eRecord(t, chain, which.run.Capabilities)
		}
		line, err := json.Marshal(records)
		if err != nil {
			t.Fatal(err)
		}
		dump.Write(line)
		dump.WriteByte('\n')
	}
	if len(fx.Cases) != 4 {
		t.Fatalf("%d cases, want 4", len(fx.Cases))
	}
	if path := os.Getenv("CIC_E2E_DUMP"); path != "" {
		if err := os.WriteFile(path, dump.Bytes(), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func TestEndToEndACapabilityChangeMovesTheObjectTheDigestAndTheReceiptAndNothingElse(t *testing.T) {
	fx := e2eLoad(t)
	for _, c := range fx.Cases {
		if maps.Equal(c.Base.Capabilities, c.Alt.Capabilities) {
			t.Fatalf("%s: the alt run changes no capability", c.ID)
		}
		base, s1 := e2eRunChain(fx, c.Schema, c.SHA256, c.YAML, c.Base.Capabilities)
		alt, s2 := e2eRunChain(fx, c.Schema, c.SHA256, c.YAML, c.Alt.Capabilities)
		if s1 != nil || s2 != nil {
			t.Fatalf("%s: %v %v", c.ID, s1, s2)
		}
		if base.standalone.ProjectionJSON == alt.standalone.ProjectionJSON || base.standalone.OutputDigest == alt.standalone.OutputDigest ||
			base.receipt.Document == alt.receipt.Document || base.receipt.Digest == alt.receipt.Digest {
			t.Fatalf("%s: a capability change did not move the object, the digest and the receipt", c.ID)
		}
		b, a := e2eLink(t, c.ID, base), e2eLink(t, c.ID, alt)
		if b["input_digest"] != a["input_digest"] {
			t.Fatalf("%s: the input digest moved with a capability", c.ID)
		}
		if base.identity != alt.identity || !maps.Equal(base.receipt.Binding, alt.receipt.Binding) {
			t.Fatalf("%s: the schema identity or the binding moved", c.ID)
		}
		if b["output_digest"] == alt.standalone.OutputDigest {
			t.Fatalf("%s: the receipt of the base run names the output of the alt run", c.ID)
		}
		delete(b, "output_digest")
		delete(a, "output_digest")
		bj, _ := json.Marshal(b)
		aj, _ := json.Marshal(a)
		if !bytes.Equal(bj, aj) {
			t.Fatalf("%s: more than the output digest of the receipt moved", c.ID)
		}
	}
}

func TestEndToEndACapabilityFaultIsRefusedInTheStandaloneStepAndInTheReceiptAlike(t *testing.T) {
	fx := e2eLoad(t)
	c := fx.Cases[2] // many-defaults
	good := c.Base.Capabilities
	with := func(f func(m map[string]string)) map[string]string {
		m := maps.Clone(good)
		f(m)
		return m
	}
	for _, tc := range []struct {
		what, reason, field string
		caps                map[string]string
	}{
		{"missing", forms.MaterializeCapabilityMissing, "mtu", with(func(m map[string]string) { delete(m, "mtu") })},
		{"unknown", forms.MaterializeCapabilityUnknown, "mtu", with(func(m map[string]string) { m["mtu"] = "Implemented" })},
		{"unverified", forms.MaterializeCapabilityUnknown, "label", with(func(m map[string]string) { m["label"] = "unverified" })},
		{"undeclared", forms.MaterializeCapabilityUndeclared, "aaa", with(func(m map[string]string) { m["aaa"] = "implemented" })},
	} {
		// the chain stops at the standalone materialization: no receipt step is reached
		_, stop := e2eRunChain(fx, c.Schema, c.SHA256, c.YAML, tc.caps)
		var me *forms.MaterializeError
		if stop == nil || stop.stage != "materialize" || !errors.As(stop.err, &me) || me.Reason != tc.reason || me.Field != tc.field {
			t.Fatalf("%s: not a controlled refusal of the materialization: %v", tc.what, stop)
		}
		// and the receipt builder, asked on its own, refuses it alike and makes no receipt
		doc, err := forms.Parse(c.YAML)
		if err != nil {
			t.Fatal(err)
		}
		r, err := forms.ReceiptFlatFixture(fx.input(c.Schema, c.SHA256), doc, fx.AccessKeys, tc.caps, fx.release(), forms.EngineIdentity{Identity: e2eIdentity, Version: fx.EngineVersion})
		var re *forms.ReceiptError
		if r != nil || !errors.As(err, &re) || re.Reason != forms.ReceiptMaterialize || !errors.As(re.Cause, &me) || me.Reason != tc.reason || me.Field != tc.field {
			t.Fatalf("%s: the receipt builder did not refuse alike: %v", tc.what, err)
		}
	}
}

func TestEndToEndTheChainIsBoundToItsSources(t *testing.T) {
	fx := e2eLoad(t)
	c := fx.Cases[2]
	caps := c.Base.Capabilities
	base, stop := e2eRunChain(fx, c.Schema, c.SHA256, c.YAML, caps)
	if stop != nil {
		t.Fatal(stop)
	}

	// a schema source that is not the pinned bytes stops the chain at its first step, before the document is read
	altered := strings.Replace(c.Schema, "kind: Demo", "kind: Demx", 1)
	if _, stop := e2eRunChain(fx, altered, c.SHA256, c.YAML, caps); stop == nil || stop.stage != "identity" {
		t.Fatalf("an altered schema source was not stopped by the identity step: %v", stop)
	}
	// the same source with its own pin is another schema: another digest in the receipt
	sum := sha256.Sum256([]byte(altered))
	other, stop := e2eRunChain(fx, altered, hex.EncodeToString(sum[:]), c.YAML, caps)
	if stop != nil {
		t.Fatal(stop)
	}
	if other.identity.SchemaDigest == base.identity.SchemaDigest || other.receipt.Document == base.receipt.Document {
		t.Fatal("another schema gave the same digest or the same receipt")
	}

	// an authored document that is changed gives another input digest, another output and another receipt
	changed, stop := e2eRunChain(fx, c.Schema, c.SHA256, "type: u\n", caps)
	if stop != nil {
		t.Fatal(stop)
	}
	if changed.authored == base.authored || changed.standalone.OutputDigest == base.standalone.OutputDigest {
		t.Fatal("a changed document gave the same input or the same output")
	}
	if e2eLink(t, "base", base)["input_digest"] == e2eLink(t, "changed", changed)["input_digest"] {
		t.Fatal("a changed document kept its input digest")
	}

	// a document the schema does not allow stops at the materialization, one that does not parse at the parse
	if _, stop := e2eRunChain(fx, c.Schema, c.SHA256, "type: t\nbogus: 1\n", caps); stop == nil || stop.stage != "materialize" {
		t.Fatalf("an invalid document: %v", stop)
	}
	if _, stop := e2eRunChain(fx, c.Schema, c.SHA256, "a: [\n", caps); stop == nil || stop.stage != "parse" {
		t.Fatalf("an unparsable document: %v", stop)
	}

	// a schema that is not flat is refused explicitly, never walked into
	raw, err := os.ReadFile(filepath.Join(e2eRoot(), "testdata", "p5", "materialize.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m struct {
		Schemas map[string]string `json:"schemas"`
	}
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatal(err)
	}
	nested := m.Schemas["nested"]
	nsum := sha256.Sum256([]byte(nested))
	_, stop = e2eRunChain(fx, nested, hex.EncodeToString(nsum[:]), "type: t\nip:\n  gateway: g\n", map[string]string{"type": "implemented", "ip": "implemented"})
	var me *forms.MaterializeError
	if stop == nil || !errors.As(stop.err, &me) || me.Reason != forms.MaterializeNotFlat {
		t.Fatalf("a nested schema was not refused: %v", stop)
	}
}
