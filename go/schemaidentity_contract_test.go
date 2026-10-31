package forms

// The engine's D1.3 schema identity contract (cic-primitive-engine PR #64, conformance/differential/schema_identity) run through this library's own
// BindSchemaIdentity, and the binding that the engine's review made mandatory: the name, the version and the digest of a receipt come from one verified
// source. The Rust peer is rust/tests/schema_identity_contract.rs.
//
// The vectors are the engine's, as a snapshot in testdata/p5/schema_identity so that every job that runs the tests has them. The rust job of the CI compares the
// snapshot with the engine's own directory at the pinned commit (diff -r), so the snapshot cannot drift from the engine.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// The two golden digests, written here a second time from the engine's issue and recomputed with sha256sum: the vector files must hold exactly these bytes.
const (
	golden314 = "sha256:a36e841db3d5f108a7d5e400f80a636d095bc5c9ddc670142b938d73d0d1a338"
	golden313 = "sha256:4fff76af7c6313eae102fc1f967b9d607db0f26c51f6f5f8eb11d7c0988b07f7"
)

func siCorpus(t *testing.T) string {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	dir := filepath.Join(root, "testdata", "p5", "schema_identity")
	if st, err := os.Stat(dir); err != nil || !st.IsDir() {
		t.Fatalf("%s is missing", dir)
	}
	return dir
}

func siReadJSON(t *testing.T, path string, v any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, v); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
}

func TestEngineSchemaIdentityVectors(t *testing.T) {
	corpus := siCorpus(t)
	entries, err := os.ReadDir(corpus)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	accepted, rejected := 0, 0
	for _, name := range names {
		var in struct {
			Source        string  `json:"source"`
			ClaimedDigest *string `json:"claimed_digest"`
			Namespace     *string `json:"namespace"`
			Kind          *string `json:"kind"`
		}
		var want struct {
			Accepted     bool   `json:"accepted"`
			SchemaName   string `json:"schema_name"`
			SchemaDigest string `json:"schema_digest"`
			Error        *struct {
				Code, Rule, Path string
			} `json:"error"`
		}
		siReadJSON(t, filepath.Join(corpus, name, "input.json"), &in)
		siReadJSON(t, filepath.Join(corpus, name, "expected.json"), &want)
		source, err := os.ReadFile(filepath.Join(corpus, name, in.Source))
		if err != nil {
			t.Fatal(err)
		}
		gotName, gotDigest, ierr := BindSchemaIdentity(source, in.ClaimedDigest, in.Namespace, in.Kind)
		if want.Accepted {
			accepted++
			if ierr != nil {
				t.Errorf("%s: want accepted, got %v", name, ierr)
			} else if gotName != want.SchemaName || gotDigest != want.SchemaDigest {
				t.Errorf("%s: want (%q, %q), got (%q, %q)", name, want.SchemaName, want.SchemaDigest, gotName, gotDigest)
			}
			continue
		}
		rejected++
		if ierr == nil {
			t.Errorf("%s: want a refusal, got accepted", name)
		} else if ierr.Code != want.Error.Code || ierr.Rule != want.Error.Rule || ierr.Path != want.Error.Path {
			t.Errorf("%s: want %+v, got %+v", name, *want.Error, *ierr)
		}
	}
	if accepted < 5 || rejected < 18 {
		t.Errorf("corpus too thin: %d accepted, %d rejected", accepted, rejected)
	}
}

func TestEngineGoldenFilesHoldTheStatedBytes(t *testing.T) {
	corpus := siCorpus(t)
	for _, c := range []struct {
		dir  string
		n    int
		want string
	}{{"golden_with_final_lf", 314, golden314}, {"golden_without_final_lf", 313, golden313}} {
		b, err := os.ReadFile(filepath.Join(corpus, c.dir, "schema.yaml"))
		if err != nil {
			t.Fatal(err)
		}
		if len(b) != c.n || SchemaDigest(b) != c.want {
			t.Errorf("%s: %d bytes, %s; want %d, %s", c.dir, len(b), SchemaDigest(b), c.n, c.want)
		}
	}
}

// The test can fail: every one of the 314 bytes of the golden source matters to the digest and to the binding.
func TestEveryByteOfTheSourceIsBound(t *testing.T) {
	source, err := os.ReadFile(filepath.Join(siCorpus(t), "golden_with_final_lf", "schema.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	g, ns, kind := golden314, "cic:storage", "K"
	for i := range source {
		altered := append([]byte(nil), source...)
		altered[i] ^= 1
		_, _, ierr := BindSchemaIdentity(altered, &g, &ns, &kind)
		if ierr == nil || ierr.Code != SchemaSourceBinding || ierr.Rule != "R-SCHEMA-DIGEST-MISMATCH" {
			t.Fatalf("byte %d: want the mismatch rule, got %v", i, ierr)
		}
	}
}

func siFlatSchema(t *testing.T) (grammar, text string) {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	var file struct {
		Grammar string            `json:"grammar"`
		Schemas map[string]string `json:"schemas"`
	}
	siReadJSON(t, filepath.Join(root, "testdata", "p5", "materialize.json"), &file)
	return file.Grammar, file.Schemas["flat"]
}

func siInput(text, sha, grammar string) SchemaInput {
	return SchemaInput{Bytes: []byte(text), SHA256: sha, Grammar: grammar, Surface: "config_surface"}
}

func TestSchemaIdentityOfOneVerifiedSource(t *testing.T) {
	grammar, text := siFlatSchema(t)
	sha := SHA256Hex([]byte(text))
	id, err := SchemaIdentityOf(siInput(text, sha, grammar))
	if err != nil {
		t.Fatal(err)
	}
	if id.SchemaName != "cic:demo:Demo" || id.SchemaVersion != "v1.0.0" || id.SchemaDigest != "sha256:"+sha || id.SchemaDigest != SchemaDigest([]byte(text)) {
		t.Errorf("identity %+v", id)
	}
}

// The binding the engine's review asked for. The components cannot come from another file: bytes that are not the pinned ones are refused before any identity
// is read, whichever file the digest belongs to.
func TestComponentsCannotComeFromAnotherFile(t *testing.T) {
	grammar, text := siFlatSchema(t)
	other := strings.Replace(text, "kind: Demo", "kind: Other", 1)
	if other == text {
		t.Fatal("the replacement did not apply")
	}
	shaOriginal, shaOther := SHA256Hex([]byte(text)), SHA256Hex([]byte(other))
	for _, c := range []struct{ bytes, sha string }{{other, shaOriginal}, {text, shaOther}} {
		_, err := SchemaIdentityOf(siInput(c.bytes, c.sha, grammar))
		var e *Error
		if !errors.As(err, &e) || e.Kind != ErrBinding || e.BindingCode != SchemaDigestMismatch {
			t.Errorf("want a binding refusal, got %v", err)
		}
	}
	a, err := SchemaIdentityOf(siInput(text, shaOriginal, grammar))
	if err != nil {
		t.Fatal(err)
	}
	b, err := SchemaIdentityOf(siInput(other, shaOther, grammar))
	if err != nil {
		t.Fatal(err)
	}
	if a.SchemaName != "cic:demo:Demo" || b.SchemaName != "cic:demo:Other" || a.SchemaDigest == b.SchemaDigest {
		t.Errorf("a %+v, b %+v", a, b)
	}
}

// A schema whose own identity breaks the contract is refused with the engine's rule, though its bytes are the pinned ones.
func TestSchemaWithAnInvalidIdentityIsRefused(t *testing.T) {
	grammar, text := siFlatSchema(t)
	for _, c := range []struct{ from, to, rule string }{
		{"kind: Demo", "kind: \"A:B\"", "R-SCHEMA-NAME-KIND"},
		{"kind: Demo", "kind: \"\"", "R-SCHEMA-NAME-KIND"},
		{"namespace: \"cic:demo\"", "namespace: \"cic::demo\"", "R-SCHEMA-NAME-NAMESPACE"},
		{"namespace: \"cic:demo\"", "namespace: \"\"", "R-SCHEMA-NAME-NAMESPACE"},
		{"namespace: \"cic:demo\"", "namespace: \":cic\"", "R-SCHEMA-NAME-NAMESPACE"},
	} {
		bad := strings.Replace(text, c.from, c.to, 1)
		if bad == text {
			t.Fatalf("%s: the replacement did not apply", c.to)
		}
		_, err := SchemaIdentityOf(siInput(bad, SHA256Hex([]byte(bad)), grammar))
		var e *SchemaIdentityError
		if !errors.As(err, &e) || e.Code != SchemaInvalidIdentity || e.Rule != c.rule {
			t.Errorf("%s: want %s, got %v", c.to, c.rule, err)
		}
	}
}

func TestSchemaNameIsUsedAsAuthored(t *testing.T) {
	ns, kind := " Cic:Storage", "kind "
	got, err := SchemaName(&ns, &kind)
	if err != nil || got != " Cic:Storage:kind " {
		t.Errorf("%q %v", got, err)
	}
}
