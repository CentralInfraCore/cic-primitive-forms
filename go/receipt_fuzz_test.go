package forms_test

// The fuzz target of the flat receipt (P5-3, docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md section 19). The Rust peer is rust/fuzz/receipt_check.rs (the property, shared by the
// `receipt` fuzz target and the test that replays the seeds) and rust/fuzz/fuzz_targets/receipt.rs. The inputs - the schema, the fixture data, the seeds - are derived by
// tools/p5/receipt_fuzz_seeds.py from the files the receipt already has; that script describes the input: the FLAGS byte, the KIND byte, the document (YAML text).
//
// The properties, on every input that is text and parses (the parser is not the receipt's): nothing panics, the input is left alone and the same input gives the same answer
// twice; the error boundary is the order of the checks - a broken schema binding is an identity error whatever the rest is, then the empty version, the release fields and
// the engine fields (a refusal of the receipt, with the field), only then the document: a refusal of the materialization has a reason the faults of the document (and, with
// the capability fault, of the capability map) can cause, and a repeated key a caller built is a canonical error, never a panic; a receipt is made only when no fault was
// made, and then it is exactly what the contract says (one line {"materialization":{...}} whose digest is that of its bytes, the schema digest of the bytes, the digest
// of the canonical authored input, the output digest of the materialization, the applied defaults in the order of the paths with the value digests of the declared
// defaults, a binding of every path to its field, the fields in byte order, authored exactly where the document writes the field), and the document written as YAML and
// read again gives the same receipt. CIC_FUZZ_CANARY works as in FuzzParse.
//
// TestReceiptFuzzSeeds runs the seeds and a deterministic mutation round in every `go test` and, with CIC_FUZZ_RECEIPT_DUMP_DIR set, writes go.in.jsonl, go.jsonl and go.trace there,
// for tools/parity/compare_receipt_fuzz.py and the engine harnesses.

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"unicode/utf8"

	forms "github.com/CentralInfraCore/cic-primitive-forms/go"
)

const (
	rfBind = 1 << iota
	rfNoVersion
	rfBadHash
	rfNoEngineVersion
	rfCaps
	rfDuplicate
	rfDotted
)

const receiptFuzzIdentity = "cic-primitive-forms-go"

type receiptFuzzFixture struct {
	schema  string
	values  map[string]string
	caps    map[string]string
	declare []string
}

func loadReceiptFuzzFixture(t testing.TB) receiptFuzzFixture {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	dir := filepath.Join(root, "testdata", "p5", "receipt-fuzz")
	schema, err := os.ReadFile(filepath.Join(dir, "schema.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text, err := os.ReadFile(filepath.Join(dir, "fixture.txt"))
	if err != nil {
		t.Fatal(err)
	}
	fx := receiptFuzzFixture{schema: string(schema), values: map[string]string{}, caps: map[string]string{}}
	for _, line := range strings.Split(strings.TrimSuffix(string(text), "\n"), "\n") {
		k, v, _ := strings.Cut(line, "\t")
		if name, ok := strings.CutPrefix(k, "cap:"); ok {
			fx.caps[name] = v
			fx.declare = append(fx.declare, name)
		} else {
			fx.values[k] = v
		}
	}
	slices.Sort(fx.declare)
	return fx
}

func receiptFuzzSeeds(t testing.TB) [][]byte {
	t.Helper()
	root := os.Getenv("CIC_FORMS_ROOT")
	if root == "" {
		root = ".."
	}
	raw, err := os.ReadFile(filepath.Join(root, "testdata", "p5", "receipt-fuzz", "seeds.json"))
	if err != nil {
		t.Fatal(err)
	}
	var seeds []struct {
		Flags int    `json:"flags"`
		Kind  int    `json:"kind"`
		Doc   string `json:"doc"`
	}
	if err := json.Unmarshal(raw, &seeds); err != nil {
		t.Fatal(err)
	}
	var out [][]byte
	for _, s := range seeds {
		out = append(out, append([]byte{byte(s.Flags), byte(s.Kind)}, s.Doc...))
	}
	return out
}

// receiptEnd is how a call ended, for the callers that count it.
type receiptEnd string

const (
	endLeft       receiptEnd = "Left"
	endIdentity   receiptEnd = "Identity"
	endVersion    receiptEnd = "Version"
	endRelease    receiptEnd = "Release"
	endEngine     receiptEnd = "Engine"
	endDocument   receiptEnd = "Document"
	endCapability receiptEnd = "Capability"
	endRepeated   receiptEnd = "Repeated"
	endSegment    receiptEnd = "Segment"
	endReceipt    receiptEnd = "Receipt"
)

func receiptFuzzMake(fx receiptFuzzFixture, flags, kind byte, doc *forms.Node) (*forms.FlatReceipt, error) {
	schema := fx.schema
	if flags&rfNoVersion != 0 {
		schema = strings.Replace(schema, "    version: v1.0.0\n", "", 1)
		if strings.Contains(schema, "v1.0.0") {
			panic("the version line was not found")
		}
	}
	if flags&rfDotted != 0 {
		renamed := strings.Replace(schema, "      - name: label\n", "      - name: \"la.bel\"\n", 1)
		if renamed == schema {
			panic("the field label was not found")
		}
		schema = renamed
	}
	claim := forms.SHA256Hex([]byte(schema))
	bytes := schema
	if flags&rfBind != 0 {
		switch kind % 3 {
		case 0:
			last := claim[len(claim)-1]
			repl := byte('0')
			if last == '0' {
				repl = '1'
			}
			claim = claim[:len(claim)-1] + string(repl)
		case 1:
			bytes = schema + "# after the claim\n"
		default:
			claim = ""
		}
	}
	caps := map[string]string{}
	for k, v := range fx.caps {
		caps[k] = v
	}
	if flags&rfCaps != 0 {
		switch (kind / 3) % 3 {
		case 0:
			delete(caps, "mtu")
		case 1:
			caps["label"] = "Implemented"
		default:
			caps["zzz"] = "implemented"
		}
	}
	if flags&rfDotted != 0 {
		if v, ok := caps["label"]; ok {
			delete(caps, "label")
			caps["la.bel"] = v
		}
	}
	release := forms.ReleaseFixture{
		PrimitiveRelease: fx.values["primitive_release"], PrimitiveSourceCommit: fx.values["primitive_source_commit"], PrimitiveBuildHash: fx.values["primitive_build_hash"],
		GrammarSHA256: fx.values["grammar_sha256"], GrammarSchemaSHA256: fx.values["grammar_schema_sha256"],
	}
	if flags&rfBadHash != 0 {
		good := release.PrimitiveBuildHash
		switch (kind / 9) % 3 {
		case 0:
			release.PrimitiveBuildHash = "sha256:ABC"
		case 1: // a digest with ONE wrong character, at the end and at the start: a check that stops early or starts late is seen
			release.PrimitiveBuildHash = good[:len(good)-1] + "g"
		default:
			release.PrimitiveBuildHash = "sha256:G" + good[len("sha256:")+1:]
		}
	}
	engine := forms.EngineIdentity{Identity: receiptFuzzIdentity, Version: fx.values["engine_version"]}
	if flags&rfNoEngineVersion != 0 {
		engine.Version = ""
	}
	in := forms.SchemaInput{Bytes: []byte(bytes), SHA256: claim, Grammar: fx.values["grammar"], Surface: "config_surface"}
	return forms.ReceiptFlatFixture(in, doc, strings.Split(fx.values["access_keys"], ","), caps, release, engine)
}

// receiptFuzzMade is a receipt that was made, for the callers that go on with it (the replay hands it to the engine).
type receiptFuzzMade struct {
	rec      *forms.FlatReceipt
	defaults int
}

func receiptFuzzSame(a *forms.FlatReceipt, ea error, b *forms.FlatReceipt, eb error) bool {
	if (ea == nil) != (eb == nil) {
		return false
	}
	if ea != nil {
		return ea.Error() == eb.Error()
	}
	return a.Document == b.Document && a.Digest == b.Digest && a.AuthoredInput == b.AuthoredInput && mapsEqual(a.Binding, b.Binding)
}

func mapsEqual(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if w, ok := b[k]; !ok || w != v {
			return false
		}
	}
	return true
}

// receiptFuzzLeftReason says why an input is left to the parser, for the trace of the replay: short, not_utf8, "syntax: ..." with the parser's own message, or parsed for an
// input that is not left. The comparison with the Rust round uses the Rust reason; this one is for reading.
func receiptFuzzLeftReason(data []byte) string {
	if len(data) < 2 {
		return "short"
	}
	if !utf8.Valid(data[2:]) {
		return "not_utf8"
	}
	if _, err := forms.Parse(string(data[2:])); err != nil {
		return "syntax: " + strings.SplitN(err.Error(), "\n", 2)[0]
	}
	return "parsed"
}

// receiptFuzzCheck runs the property on one input and fails t, with the input in the message, when it does not hold.
func receiptFuzzCheck(t testing.TB, fx receiptFuzzFixture, data []byte) (receiptEnd, *receiptFuzzMade) {
	t.Helper()
	if len(data) < 2 {
		return endLeft, nil
	}
	flags, kind, rest := data[0], data[1], data[2:]
	if !utf8.Valid(rest) {
		return endLeft, nil
	}
	doc, err := forms.Parse(string(rest))
	if err != nil {
		return endLeft, nil
	}
	repeated := false
	if flags&rfDuplicate != 0 && doc.Kind == forms.KindMap && len(doc.Entries) > 0 {
		doc.Entries = append(doc.Entries, doc.Entries[0])
		repeated = true
	}
	before := forms.Emit(doc)
	r1, e1 := receiptFuzzMake(fx, flags, kind, doc)
	if forms.Emit(doc) != before {
		t.Fatalf("%q: the receipt changed its input", data)
	}
	r2, e2 := receiptFuzzMake(fx, flags, kind, doc)
	if !receiptFuzzSame(r1, e1, r2, e2) {
		t.Fatalf("%q: the same input gave another answer", data)
	}
	capsBroken := flags&rfCaps != 0
	layer := endDocument
	switch {
	case flags&rfBind != 0:
		layer = endIdentity
	case flags&rfNoVersion != 0:
		layer = endVersion
	case flags&rfBadHash != 0:
		layer = endRelease
	case flags&rfNoEngineVersion != 0:
		layer = endEngine
	}
	if e1 != nil {
		var re *forms.ReceiptError
		var me *forms.MaterializeError
		if !errors.As(e1, &re) {
			t.Fatalf("%q: an error outside the contract: %v", data, e1)
		}
		switch re.Reason {
		case forms.ReceiptIdentity:
			if layer != endIdentity {
				t.Fatalf("%q: an identity error without a broken binding", data)
			}
			return endIdentity, nil
		case forms.ReceiptSchemaVersionEmpty:
			if layer != endVersion || re.Field != "schema_version" {
				t.Fatalf("%q: %s %s", data, re.Reason, re.Field)
			}
			return endVersion, nil
		case forms.ReceiptReleaseField:
			if layer != endRelease || re.Field != "primitive_build_hash" {
				t.Fatalf("%q: %s %s", data, re.Reason, re.Field)
			}
			return endRelease, nil
		case forms.ReceiptEngineField:
			if layer != endEngine || re.Field != "engine_version" {
				t.Fatalf("%q: %s %s", data, re.Reason, re.Field)
			}
			return endEngine, nil
		case forms.ReceiptPathNotOneSegment:
			if layer != endDocument || flags&rfDotted == 0 || re.Field != "la.bel" {
				t.Fatalf("%q: %s %s", data, re.Reason, re.Field)
			}
			return endSegment, nil
		case forms.ReceiptMaterialize:
			if layer != endDocument || !errors.As(e1, &me) {
				t.Fatalf("%q: a materialization refusal out of its place: %v", data, e1)
			}
			doc := me.Reason == forms.MaterializeInvalidDocument || me.Reason == forms.MaterializeUnsupported || me.Reason == forms.MaterializeOmittedOptional
			caps := me.Reason == forms.MaterializeCapabilityMissing || me.Reason == forms.MaterializeCapabilityUnknown || me.Reason == forms.MaterializeCapabilityUndeclared
			if !(doc || (capsBroken && caps)) {
				t.Fatalf("%q: a materialization refusal the faults cannot cause: %s", data, me.Reason)
			}
			if caps {
				return endCapability, nil
			}
			return endDocument, nil
		case forms.ReceiptCanonical:
			if layer != endDocument || !repeated || re.Cause == nil || !strings.Contains(re.Cause.Error(), "duplicate_key") {
				t.Fatalf("%q: a canonical error without a repeated key: %v", data, e1)
			}
			return endRepeated, nil
		}
		t.Fatalf("%q: a refusal the faults cannot cause: %v", data, e1)
	}
	if layer != endDocument {
		t.Fatalf("%q: a receipt although a check should have refused", data)
	}
	if capsBroken || repeated || flags&rfDotted != 0 {
		t.Fatalf("%q: a receipt although the capability map or the document was broken", data)
	}
	// the receipt is exactly what the contract says
	text := r1.Document
	if !strings.HasPrefix(text, `{"materialization":{`) || !strings.HasSuffix(text, "}}") || strings.ContainsAny(text, "\n\r\t") {
		t.Fatalf("%q: the document is not one line of the contract: %s", data, text)
	}
	if r1.Digest != forms.CanonicalDigest([]byte(text)) {
		t.Fatalf("%q: the digest is not that of the document", data)
	}
	canon, err := forms.CanonicalBytes(doc)
	if err != nil || r1.AuthoredInput != string(canon) {
		t.Fatalf("%q: the authored input is not the canonical bytes (%v)", data, err)
	}
	has := func(s string) {
		if !strings.Contains(text, s) {
			t.Fatalf("%q: %s is missing from %s", data, s, text)
		}
	}
	has(`"input_digest":"` + forms.CanonicalDigest(canon) + `"`)
	has(`"output_digest":"` + r1.Materialized.OutputDigest + `"`)
	has(`"schema_digest":"sha256:` + forms.SHA256Hex([]byte(fx.schema)) + `"`)
	has(`"engine_identity":"` + receiptFuzzIdentity + `"`)
	has(`"receipt_schema_version":1`)
	// the values of the identity: the name is namespace:kind and the version is the one the schema writes, the engine version is the fixture's
	line := func(key string) string {
		for _, l := range strings.Split(fx.schema, "\n") {
			if v, ok := strings.CutPrefix(l, "    "+key+": "); ok {
				return strings.Trim(v, `"`)
			}
		}
		t.Fatalf("the schema has no %s", key)
		return ""
	}
	has(`"schema_name":"` + line("namespace") + ":" + line("kind") + `"`)
	has(`"schema_version":"` + line("version") + `"`)
	has(`"engine_version":"` + fx.values["engine_version"] + `"`)
	for _, key := range []string{"primitive_release", "primitive_source_commit", "primitive_build_hash", "grammar_sha256", "grammar_schema_sha256"} {
		has(`"` + key + `":"` + fx.values[key] + `"`)
	}
	has(`"derived_values":[]`)
	for _, name := range []string{"grammar_sha256", "grammar_schema_sha256", "primitive_release", "primitive_source_commit", "primitive_build_hash", "schema_name", "schema_version", "engine_version", "applied_defaults"} {
		if strings.Count(text, `"`+name+`":`) != 1 {
			t.Fatalf("%q: %s is not there exactly once", data, name)
		}
	}
	var keys []string
	for _, f := range r1.Materialized.Fields {
		keys = append(keys, f.Key)
	}
	if !slices.Equal(keys, fx.declare) || !slices.Equal(r1.Materialized.ExpectedKeys, fx.declare) {
		t.Fatalf("%q: the fields %v are not the declared names in byte order %v", data, keys, fx.declare)
	}
	written := map[string]*forms.Node{}
	if doc.Kind == forms.KindMap {
		for _, e := range doc.Entries {
			written[e.Key] = e.Value
		}
	}
	var entries []string
	binding := map[string]string{}
	for _, f := range r1.Materialized.Fields {
		if v, ok := written[f.Key]; ok {
			if f.Provenance != "authored" || !f.Value.Same(v) {
				t.Fatalf("%q: %s is not the authored value", data, f.Key)
			}
			continue
		}
		if f.Provenance != "schema_default" {
			t.Fatalf("%q: %s is %s", data, f.Key, f.Provenance)
		}
		vb, err := forms.CanonicalBytes(f.Value)
		if err != nil {
			t.Fatalf("%q: %v", data, err)
		}
		entries = append(entries, `{"path":"$.`+f.Key+`","rule":"schema-default","value_digest":"`+forms.CanonicalDigest(vb)+`"}`)
		binding["$."+f.Key] = f.Key
	}
	has(`"applied_defaults":[` + strings.Join(entries, ",") + `]`)
	if !mapsEqual(r1.Binding, binding) {
		t.Fatalf("%q: the binding %v is not %v", data, r1.Binding, binding)
	}
	again, err := forms.Parse(forms.Emit(doc))
	if err != nil {
		t.Fatalf("%q: an emitted document does not read again: %v", data, err)
	}
	second, e3 := receiptFuzzMake(fx, flags, kind, again)
	if e3 != nil || second.Document != r1.Document {
		t.Fatalf("%q: emit and parse changed the receipt (%v)", data, e3)
	}
	return endReceipt, &receiptFuzzMade{rec: r1, defaults: len(entries)}
}

func FuzzReceipt(f *testing.F) {
	fx := loadReceiptFuzzFixture(f)
	for _, seed := range receiptFuzzSeeds(f) {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		if os.Getenv("CIC_FUZZ_CANARY") != "" && strings.HasPrefix(string(data), "CANARY") {
			t.Fatal("canary: the fuzz job reports a failure")
		}
		receiptFuzzCheck(t, fx, data)
	})
}

// The deterministic mutation round, the same algorithm as in the Rust peer: xorshift64* from a fixed state, 1 to 4 mutations of a seed, a byte changed, a token of the
// dictionary inserted, a byte deleted, the flags or the kind byte set. It gives the property thousands of inputs in every `go test`, and the engine the receipts they made.
const (
	receiptFuzzRounds = 5000
	receiptFuzzDumped = 1500
	// the id of the receipt a mutation made is this plus its number in the round, so that the dumps of the two languages line up
	receiptFuzzMutationID = 1000000
)

var receiptFuzzDictionary = []string{"\n", ": ", "- ", "~", "\"", "\\", "{", "}", "[", "]", "type: t\n", "mtu: 1\n", "enabled: true\n", "label: x\n", "maybe: ~\n", "a: 1\n"}

type receiptFuzzRng uint64

func (r *receiptFuzzRng) next() uint64 {
	x := uint64(*r)
	x ^= x >> 12
	x ^= x << 25
	x ^= x >> 27
	*r = receiptFuzzRng(x)
	return x * 0x2545F4914F6CDD1D
}

func receiptFuzzMutated(seeds [][]byte, rng *receiptFuzzRng, n int) []byte {
	data := slices.Clone(seeds[n%len(seeds)])
	pos := func(length int) int {
		if length == 0 {
			return 0
		}
		return int(rng.next() % uint64(length))
	}
	mutations := 1 + rng.next()%4 // once: the loop condition must not draw again
	for i := uint64(0); i < mutations; i++ {
		switch rng.next() % 5 {
		case 0:
			at := pos(len(data))
			b := byte(rng.next() & 0xff)
			if at < len(data) {
				data[at] = b
			}
		case 1:
			at := min(pos(len(data)+1), len(data))
			token := receiptFuzzDictionary[rng.next()%uint64(len(receiptFuzzDictionary))]
			data = slices.Insert(data, at, []byte(token)...)
		case 2:
			if at := pos(len(data)); at < len(data) {
				data = slices.Delete(data, at, at+1)
			}
		case 3:
			flags := byte(rng.next() % 128)
			if len(data) > 0 {
				data[0] = flags
			}
		default:
			kind := byte(rng.next() % 27)
			if len(data) > 1 {
				data[1] = kind
			}
		}
	}
	return data
}

func TestReceiptFuzzSeeds(t *testing.T) {
	fx := loadReceiptFuzzFixture(t)
	ends := map[receiptEnd]int{}
	defaults := map[int]bool{}
	var out, in strings.Builder
	declared, err := json.Marshal(fx.declare)
	if err != nil {
		t.Fatal(err)
	}
	for id, seed := range receiptFuzzSeeds(t) {
		fmt.Fprintf(&in, "{\"id\":%d,\"keys\":%s}\n", id, declared)
		end, made := receiptFuzzCheck(t, fx, seed)
		ends[end]++
		var r []any
		if made != nil {
			defaults[made.defaults] = true
			var body map[string]json.RawMessage
			var doc map[string]json.RawMessage
			if err := json.Unmarshal([]byte(made.rec.Document), &doc); err != nil || len(doc) != 1 {
				t.Fatalf("seed %d: not JSON: %v", id, err)
			}
			if err := json.Unmarshal(doc["materialization"], &body); err != nil || len(body) != 15 {
				t.Fatalf("seed %d: the body has %d fields (%v)", id, len(body), err)
			}
			r = []any{"ok", made.rec.Document, made.rec.AuthoredInput, made.rec.Binding, made.rec.Materialized.ExpectedKeys, made.rec.Materialized.ProjectionJSON}
		} else {
			r = []any{"err", string(end), ""}
		}
		line, err := json.Marshal(map[string]any{"id": id, "r": r})
		if err != nil {
			t.Fatal(err)
		}
		out.Write(line)
		out.WriteByte('\n')
	}
	for _, end := range []receiptEnd{endIdentity, endVersion, endRelease, endEngine, endDocument, endCapability, endRepeated, endSegment, endReceipt} {
		if ends[end] == 0 {
			t.Errorf("no seed ends as %s: %v", end, ends)
		}
	}
	if ends[endLeft] != 0 {
		t.Errorf("a seed that is left: %v", ends)
	}
	if !defaults[0] || !defaults[1] {
		t.Errorf("the seeds make no receipt with no default, or with one: %v", defaults)
	}
	many := false
	for d := range defaults {
		many = many || d > 1
	}
	if !many {
		t.Errorf("the seeds make no receipt with several defaults: %v", defaults)
	}
	// the mutation round: the property holds on every input, and the receipts it makes are handed to the engine
	all := receiptFuzzSeeds(t)
	rng := receiptFuzzRng(0x9E3779B97F4A7C15)
	mutatedEnds := map[receiptEnd]int{}
	dumped := 0
	var trace strings.Builder
	for n := 0; n < receiptFuzzRounds; n++ {
		data := receiptFuzzMutated(all, &rng, n)
		end, made := receiptFuzzCheck(t, fx, data)
		reason := ""
		if end == endLeft {
			reason = receiptFuzzLeftReason(data)
		}
		fmt.Fprintf(&trace, "%d\t%s\t%x\t%s\n", n, end, data, reason)
		mutatedEnds[end]++
		if made != nil && dumped < receiptFuzzDumped {
			dumped++
			fmt.Fprintf(&in, "{\"id\":%d,\"keys\":%s}\n", receiptFuzzMutationID+n, declared)
			line, err := json.Marshal(map[string]any{"id": receiptFuzzMutationID + n, "r": []any{"ok", made.rec.Document, made.rec.AuthoredInput, made.rec.Binding, made.rec.Materialized.ExpectedKeys, made.rec.Materialized.ProjectionJSON}})
			if err != nil {
				t.Fatal(err)
			}
			out.Write(line)
			out.WriteByte('\n')
		}
	}
	for _, end := range []receiptEnd{endLeft, endIdentity, endVersion, endRelease, endEngine, endDocument, endCapability, endRepeated, endSegment, endReceipt} {
		if mutatedEnds[end] == 0 {
			t.Errorf("the mutation round never ends as %s: %v", end, mutatedEnds)
		}
	}
	if dir := os.Getenv("CIC_FUZZ_RECEIPT_DUMP_DIR"); dir != "" {
		if err := os.WriteFile(filepath.Join(dir, "go.jsonl"), []byte(out.String()), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "go.in.jsonl"), []byte(in.String()), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "go.trace"), []byte(trace.String()), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func TestWhatIsNotTextOrDoesNotParseIsLeftToTheParser(t *testing.T) {
	fx := loadReceiptFuzzFixture(t)
	for _, data := range [][]byte{nil, {0}, {0, 0, 0xff, 0xfe}, []byte("\x00\x00a: [\n")} {
		if end, _ := receiptFuzzCheck(t, fx, data); end != endLeft {
			t.Errorf("%q ended as %s", data, end)
		}
	}
}
