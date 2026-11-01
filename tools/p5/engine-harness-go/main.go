// Checks the shared flat-materialization cases against the engine (Go): for each accepted case the engine's NewMaterializedObject accepts the fields of the
// expected projection under the expected key set, and MaterializedObjectDigest gives the expected output_digest. The expected values are computed by
// tools/p5/materialize_cases.py. The negative checks make a green run mean something: a key set with one key missing, or one key more, must be refused, and
// a changed value, capability or provenance must change the digest. The Rust peer is tools/p5/engine-harness-rust.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/CentralInfraCore/cic-primitive-engine/go/digestprojection"
	"github.com/CentralInfraCore/cic-primitive-engine/go/materialized"
	"github.com/CentralInfraCore/cic-primitive-engine/go/receipt"
)

type projField struct {
	Key        string      `json:"key"`
	Capability string      `json:"capability"`
	Provenance string      `json:"provenance"`
	Value      interface{} `json:"value"`
}

func fieldsOf(fs []projField) map[string]materialized.MaterializedField {
	out := map[string]materialized.MaterializedField{}
	for _, f := range fs {
		var v interface{} = f.Value
		switch n := f.Value.(type) {
		case float64:
			v = int64(n)
		case json.Number:
			i, err := n.Int64()
			if err != nil {
				panic(err)
			}
			v = i
		}
		var ie materialized.IntentEvidence
		switch f.Provenance {
		case "authored":
			ie = materialized.NewAuthoredIntentEvidence(v, true)
		case "schema_default":
			ie = materialized.NewSchemaDefaultIntentEvidence(v)
		default:
			panic("provenance " + f.Provenance)
		}
		out[f.Key] = materialized.MaterializedField{Capability: materialized.Capability(f.Capability), Evidence: materialized.NewIntentFieldEvidence(ie)}
	}
	return out
}

func keySet(keys []string) map[string]struct{} {
	m := map[string]struct{}{}
	for _, k := range keys {
		m[k] = struct{}{}
	}
	return m
}

func digestOf(fs []projField, keys []string) (string, error) {
	obj, err := materialized.NewMaterializedObject(fieldsOf(fs), keySet(keys))
	if err != nil {
		return "", err
	}
	return digestprojection.MaterializedObjectDigest(obj)
}

func fail(format string, a ...interface{}) {
	fmt.Fprintf(os.Stderr, format+"\n", a...)
	os.Exit(1)
}

// differential is the generated differential (tools/parity/gen_materialize.py): for every case a language accepted, the engine builds its own object from the
// projection that language wrote, under the key set the GENERATOR declared (not the one the language reports), and its digest must be the one the language gave.
// The numbers are read as json.Number: an integer of 64 bits must not pass through a float.
func differential(input, output string) {
	answers := map[json.Number][]interface{}{}
	out, err := os.ReadFile(output)
	if err != nil {
		fail("%v", err)
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		var a struct {
			ID json.Number   `json:"id"`
			R  []interface{} `json:"r"`
		}
		if err := json.Unmarshal([]byte(line), &a); err != nil {
			fail("%v", err)
		}
		answers[a.ID] = a.R
	}
	in, err := os.ReadFile(input)
	if err != nil {
		fail("%v", err)
	}
	checked := 0
	for _, line := range strings.Split(strings.TrimSpace(string(in)), "\n") {
		var c struct {
			ID   json.Number `json:"id"`
			Keys []string    `json:"keys"`
		}
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			fail("%v", err)
		}
		r := answers[c.ID]
		if len(r) == 0 || r[0] != "ok" {
			continue
		}
		var projection struct {
			Fields []projField `json:"fields"`
		}
		dec := json.NewDecoder(strings.NewReader(r[2].(string)))
		dec.UseNumber()
		if err := dec.Decode(&projection); err != nil {
			fail("#%s: %v", c.ID, err)
		}
		got, err := digestOf(projection.Fields, c.Keys)
		if err != nil || got != r[3].(string) {
			fail("#%s: the engine's digest %q (%v) of what the library wrote, want %v", c.ID, got, err, r[3])
		}
		if len(c.Keys) > 0 {
			if _, err := digestOf(projection.Fields, c.Keys[1:]); err == nil {
				fail("#%s: one key less was accepted", c.ID)
			}
		}
		checked++
	}
	if checked == 0 {
		fail("no accepted case was checked")
	}
	fmt.Printf("engine (go): %d generated accepted cases agree with the engine's NewMaterializedObject and digest\n", checked)
}

// receiptDifferential is the generated receipt differential (tools/parity/compare_receipt.py): the engine is the third reader of the RECEIPTS each language wrote
// for the generated cases. For every accepted case the engine builds its own object from the projection the language wrote, under the key set the GENERATOR
// declared, and receipt.Verify has to accept the receipt, the authored input and the binding the language gave, with the engine identity of that language. Then the
// receipt is altered in ways that the engine's contract refuses, and each has to be refused with the code and the rule written here: the input digest, the output
// digest, the schema version, the evidence value, an evidence entry removed, and a path with no binding. The Rust peer is receipt_differential of
// tools/p5/engine-harness-rust.
func receiptDifferential(input, output, identity string) {
	answers := map[json.Number][]interface{}{}
	out, err := os.ReadFile(output)
	if err != nil {
		fail("%v", err)
	}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		var a struct {
			ID json.Number   `json:"id"`
			R  []interface{} `json:"r"`
		}
		if err := json.Unmarshal([]byte(line), &a); err != nil {
			fail("%v", err)
		}
		answers[a.ID] = a.R
	}
	in, err := os.ReadFile(input)
	if err != nil {
		fail("%v", err)
	}
	type want struct{ code, rule string }
	accepted, refused := 0, 0
	for _, line := range strings.Split(strings.TrimSpace(string(in)), "\n") {
		var c struct {
			ID   json.Number `json:"id"`
			Keys []string    `json:"keys"`
		}
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			fail("%v", err)
		}
		r := answers[c.ID]
		if len(r) == 0 || r[0] != "ok" {
			continue
		}
		var projection struct {
			Fields []projField `json:"fields"`
		}
		pdec := json.NewDecoder(strings.NewReader(r[5].(string)))
		pdec.UseNumber() // an integer of 64 bits must not pass through a float
		if err := pdec.Decode(&projection); err != nil {
			fail("#%s: %v", c.ID, err)
		}
		doc := decode(r[1].(string))
		authored := decode(r[2].(string))
		binding := map[string]string{}
		for k, v := range r[3].(map[string]interface{}) {
			binding[k] = v.(string)
		}
		body := func(d interface{}) map[string]interface{} { return obj(obj(d)["materialization"]) }
		if got := body(doc)["engine_identity"]; got != identity {
			fail("#%s: the engine identity is %v, want %s", c.ID, got, identity)
		}
		verify := func(d interface{}, b map[string]string) *want {
			o, err := materialized.NewMaterializedObject(fieldsOf(projection.Fields), keySet(c.Keys))
			if err != nil {
				fail("#%s: the object: %v", c.ID, err)
			}
			rc, err := receipt.Parse(d)
			if err == nil {
				err = receipt.Verify(rc, o, authored, receipt.PathBinding(b))
			}
			if err == nil {
				return nil
			}
			var re *receipt.Error
			if !errors.As(err, &re) {
				fail("#%s: not an engine error: %v", c.ID, err)
			}
			return &want{re.Code, re.Rule}
		}
		if w := verify(doc, binding); w != nil {
			fail("#%s: the engine verifier refused the receipt the language wrote: %v", c.ID, *w)
		}
		accepted++
		expect := func(what string, got *want, w want) {
			if got == nil || *got != w {
				fail("#%s: %s: got %v, want %v", c.ID, what, got, w)
			}
			refused++
		}
		d := clone(doc)
		body(d)["input_digest"] = zeroDigest()
		expect("a changed input digest", verify(d, binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-INPUT"})
		d = clone(doc)
		body(d)["output_digest"] = zeroDigest()
		expect("a changed output digest", verify(d, binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		d = clone(doc)
		body(d)["schema_version"] = ""
		expect("an empty schema version", verify(d, binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-SHAPE"})
		list := func(d interface{}) []interface{} { return body(d)["applied_defaults"].([]interface{}) }
		if len(list(doc)) == 0 {
			continue
		}
		d = clone(doc)
		obj(list(d)[0])["value_digest"] = zeroDigest()
		expect("a changed value digest", verify(d, binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-VALUE"})
		d = clone(doc)
		body(d)["applied_defaults"] = list(d)[1:]
		expect("an evidence entry removed", verify(d, binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-MEMBERSHIP"})
		b := map[string]string{}
		first := ""
		for k, v := range binding {
			if first == "" || k < first {
				first = k
			}
			b[k] = v
		}
		delete(b, first)
		expect("a path with no binding", verify(doc, b), want{receipt.CodeReceiptUnbound, "R-RECEIPT-PATH-BINDING"})
	}
	if accepted == 0 {
		fail("no accepted receipt was checked")
	}
	fmt.Printf("engine (go): %d generated receipts of %s accepted by receipt.Verify, %d altered ones refused with the engine's code and rule\n", accepted, identity, refused)
}

// decode reads JSON with integers kept as json.Number, the form the engine's receipt.Parse and canonical.IsValue take.
func decode(text string) interface{} {
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	var v interface{}
	if err := dec.Decode(&v); err != nil {
		fail("%v", err)
	}
	return v
}

func clone(v interface{}) interface{} {
	b, err := json.Marshal(v)
	if err != nil {
		fail("%v", err)
	}
	return decode(string(b))
}

func obj(v interface{}) map[string]interface{} { return v.(map[string]interface{}) }

func zeroDigest() string { return "sha256:" + strings.Repeat("0", 64) }

// receipts: the engine's verifier is the gate of the receipts of this library (P5-3). For every accepted flat case the receipt in testdata/p5/receipt.json,
// with the identity of the Go implementation in its place, is accepted by receipt.Verify against the object built from the shared projection and the authored
// input; and each deliberate alteration of it, of the object, of the input or of the binding is refused with the code and the rule the engine gives it. The
// expected refusals are written here, apart from the library, from the engine's contract (docs/RECEIPT-SCHEMA.md, docs/RECEIPT-VERIFIER.md). The Rust peer is
// the receipts function of tools/p5/engine-harness-rust.
// pathCases are the cases that receipt-paths.json has to carry, each once: a table that lost one, repeated one or grew one the harness does not know is a failure,
// not a shorter run (the count of the table is not a check of the table).
var pathCases = []string{
	"plain-bound",
	"nested-bound",
	"selector-bound",
	"selector-unbound",
	"selector-with-dot-bound",
	"empty-segment-in-the-middle",
	"empty-last-segment",
	"root-only",
	"no-root",
	"dollar-without-dot",
	"trailing-space-bound",
	"path-names-another-key",
	"bound-to-a-missing-key",
	"bound-to-an-authored-key",
	"swapped-binding-of-two-defaults",
	"control-binding-of-four-defaults",
}

func checkPathCaseIDs(ids []string) error {
	known := map[string]bool{}
	for _, w := range pathCases {
		known[w] = true
	}
	seen := map[string]bool{}
	for _, id := range ids {
		if seen[id] {
			return fmt.Errorf("the path case %s is repeated", id)
		}
		if !known[id] {
			return fmt.Errorf("the path case %s is not one the harness knows", id)
		}
		seen[id] = true
	}
	for _, w := range pathCases {
		if !seen[w] {
			return fmt.Errorf("the path case %s is missing", w)
		}
	}
	return nil
}

func receipts(root string) {
	read := func(name string) []byte {
		b, err := os.ReadFile(filepath.Join(root, "testdata", "p5", name))
		if err != nil {
			fail("%v", err)
		}
		return b
	}
	var materialize struct {
		Cases []struct {
			ID     string `json:"id"`
			Expect struct {
				ExpectedKeys []string `json:"expected_keys"`
				Projection   string   `json:"projection"`
			} `json:"expect"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(read("materialize.json"), &materialize); err != nil {
		fail("%v", err)
	}
	var file struct {
		Token string `json:"engine_identity_token"`
		Cases []struct {
			ID            string            `json:"id"`
			AuthoredInput string            `json:"authored_input"`
			Document      string            `json:"document"`
			Binding       map[string]string `json:"binding"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(read("receipt.json"), &file); err != nil {
		fail("%v", err)
	}
	type want struct{ code, rule string }
	verify := func(doc, authored interface{}, fs []projField, keys []string, binding map[string]string) *want {
		o, err := materialized.NewMaterializedObject(fieldsOf(fs), keySet(keys))
		if err != nil {
			fail("the object: %v", err)
		}
		r, err := receipt.Parse(doc)
		if err == nil {
			err = receipt.Verify(r, o, authored, receipt.PathBinding(binding))
		}
		if err == nil {
			return nil
		}
		var re *receipt.Error
		if !errors.As(err, &re) {
			fail("not an engine error: %v", err)
		}
		return &want{re.Code, re.Rule}
	}
	accepted, refused := 0, 0
	for _, c := range file.Cases {
		var keys []string
		var projText string
		for _, m := range materialize.Cases {
			if m.ID == c.ID {
				keys, projText = m.Expect.ExpectedKeys, m.Expect.Projection
			}
		}
		var projection struct {
			Fields []projField `json:"fields"`
		}
		if err := json.Unmarshal([]byte(projText), &projection); err != nil {
			fail("%s: %v", c.ID, err)
		}
		doc := decode(strings.ReplaceAll(c.Document, file.Token, "cic-primitive-forms-go"))
		authored := decode(c.AuthoredInput)
		if w := verify(doc, authored, projection.Fields, keys, c.Binding); w != nil {
			fail("%s: the engine verifier refused the receipt of the library: %v", c.ID, *w)
		}
		accepted++
		expect := func(what string, got *want, w want) {
			if got == nil || *got != w {
				fail("%s: %s: got %v, want %v", c.ID, what, got, w)
			}
			refused++
		}
		body := func(d interface{}) map[string]interface{} { return obj(obj(d)["materialization"]) }
		d := clone(doc)
		body(d)["receipt_schema_version"] = json.Number("2")
		expect("a version the verifier does not support", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-VERSION"})
		d = clone(doc)
		body(d)["input_digest"] = zeroDigest()
		expect("a changed input digest", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-INPUT"})
		a := clone(authored)
		obj(a)["type"] = "another"
		expect("another authored input", verify(doc, a, projection.Fields, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-INPUT"})
		d = clone(doc)
		body(d)["output_digest"] = zeroDigest()
		expect("a changed output digest", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		changed := append([]projField{}, projection.Fields...)
		changed[0].Value = "tampered"
		expect("another object", verify(doc, authored, changed, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		d = clone(doc)
		body(d)["schema_version"] = ""
		expect("an empty schema version", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-SHAPE"})
		d = clone(doc)
		body(d)["schema_digest"] = "sha256:ABC"
		expect("a schema digest of a wrong form", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-DIGEST-FORMAT"})
		d = clone(doc)
		delete(body(d), "engine_version")
		expect("a missing field", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-SHAPE"})

		n := len(body(doc)["applied_defaults"].([]interface{}))
		if n == 0 {
			continue
		}
		list := func(d interface{}) []interface{} { return body(d)["applied_defaults"].([]interface{}) }
		d = clone(doc)
		obj(list(d)[0])["value_digest"] = zeroDigest()
		expect("a changed value digest", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-VALUE"})
		d = clone(doc)
		obj(list(d)[0])["rule"] = "other"
		expect("a rule that is not the literal", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-EVIDENCE-FORM"})
		d = clone(doc)
		body(d)["applied_defaults"] = list(d)[1:]
		expect("an evidence entry removed", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-MEMBERSHIP"})
		d = clone(doc)
		body(d)["applied_defaults"] = append(list(d), list(d)[0])
		expect("an evidence entry repeated", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-ORDER"})
		if n > 1 {
			d = clone(doc)
			l := list(d)
			l[0], l[1] = l[1], l[0]
			expect("two evidence entries swapped", verify(d, authored, projection.Fields, keys, c.Binding), want{receipt.CodeInvalidReceipt, "R-RECEIPT-ORDER"})
		}
		if _, ok := c.Binding["$.enabled"]; ok {
			b := map[string]string{}
			for k, v := range c.Binding {
				if k != "$.enabled" {
					b[k] = v
				}
			}
			expect("a path with no binding", verify(doc, authored, projection.Fields, keys, b), want{receipt.CodeReceiptUnbound, "R-RECEIPT-PATH-BINDING"})
		}
		plain := append([]projField{}, projection.Fields...)
		for i := range plain {
			if plain[i].Provenance == "schema_default" {
				plain[i].Provenance = "authored"
			}
		}
		// the object then says that no field was defaulted, and its digest no longer matches the receipt: the output binding is what is seen first
		expect("an object whose provenance was changed", verify(doc, authored, plain, keys, c.Binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
	}
	if accepted != 7 {
		fail("%d receipts accepted, want 7", accepted)
	}
	fmt.Printf("engine (go): %d receipts accepted by receipt.Verify, %d altered ones refused with the engine's code and rule\n", accepted, refused)

	// The path of an evidence entry and its binding (testdata/p5/receipt-paths.json): what the verifier says is measured here, not designed.
	var paths struct {
		Cases []struct {
			ID     string            `json:"id"`
			Base   string            `json:"base"`
			Paths  []string          `json:"paths"`
			Bind   map[string]string `json:"bind"`
			Expect json.RawMessage   `json:"expect"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(read("receipt-paths.json"), &paths); err != nil {
		fail("%v", err)
	}
	listed := make([]string, 0, len(paths.Cases))
	for _, pc := range paths.Cases {
		listed = append(listed, pc.ID)
	}
	if err := checkPathCaseIDs(listed); err != nil {
		fail("receipt-paths.json: %v", err)
	}
	for _, pc := range paths.Cases {
		var keys []string
		var projText string
		for _, m := range materialize.Cases {
			if m.ID == pc.Base {
				keys, projText = m.Expect.ExpectedKeys, m.Expect.Projection
			}
		}
		var projection struct {
			Fields []projField `json:"fields"`
		}
		if err := json.Unmarshal([]byte(projText), &projection); err != nil {
			fail("%s: %v", pc.ID, err)
		}
		found := false
		for _, c := range file.Cases {
			if c.ID != pc.Base {
				continue
			}
			found = true
			doc := decode(strings.ReplaceAll(c.Document, file.Token, "cic-primitive-forms-go"))
			authored := decode(c.AuthoredInput)
			list := obj(obj(doc)["materialization"])["applied_defaults"].([]interface{})
			for i, p := range pc.Paths {
				obj(list[i])["path"] = p
			}
			got := verify(doc, authored, projection.Fields, keys, pc.Bind)
			var verdict string
			if json.Unmarshal(pc.Expect, &verdict) == nil && verdict == "accept" {
				if got != nil {
					fail("%s: the path case: refused %v, want accepted", pc.ID, *got)
				}
				continue
			}
			var w []string
			if err := json.Unmarshal(pc.Expect, &w); err != nil || len(w) != 2 {
				fail("%s: the expectation is neither accept nor [code, rule]", pc.ID)
			}
			if got == nil || *got != (want{w[0], w[1]}) {
				fail("%s: the path case: got %v, want %v", pc.ID, got, w)
			}
		}
		if !found {
			fail("%s: no base case %s", pc.ID, pc.Base)
		}
	}
	fmt.Printf("engine (go): %d path and binding cases give the verdict written in receipt-paths.json\n", len(paths.Cases))
}

// e2eRun is one run of the chain as the language dumped it (tools/parity/check_receipt_e2e.py).
type e2eRun struct {
	Standalone struct {
		Projection   string   `json:"projection"`
		ExpectedKeys []string `json:"expected_keys"`
	} `json:"standalone"`
	Receipt struct {
		Document      string            `json:"document"`
		AuthoredInput string            `json:"authored_input"`
		Binding       map[string]string `json:"binding"`
	} `json:"receipt"`
}

// endToEnd is the end-to-end proof of P5-3 (docs section 21): the engine's verifier is the independent reader of the records one language's chain made. For each
// case and each of its two runs (the capability map as given, and with one capability changed) the OBJECT is the one the standalone materialization step made, the
// receipt, the authored input and the binding are the ones the receipt step made, all of the same execution: the verifier has to accept. Then the artefacts of the
// two runs are mixed, and every alteration the engine can see is made: each has to be refused with the code and the rule written here. What the engine cannot see
// (the schema source) is the identity step's and the checker's. And the swap of the bindings of two defaulted fields: refused when their values differ, ACCEPTED when
// they are equal (that acceptance is the measured limit, and it is why the Python reference compares the binding itself). The Rust peer is tools/p5/engine-harness-rust.
func endToEnd(dump, identity string) {
	type want struct{ code, rule string }
	text, err := os.ReadFile(dump)
	if err != nil {
		fail("%v", err)
	}
	accepted, refused, swapAccepted, swapRefused := 0, 0, 0, 0
	for _, line := range strings.Split(strings.TrimSpace(string(text)), "\n") {
		var c struct {
			ID   string `json:"id"`
			Base e2eRun `json:"base"`
			Alt  e2eRun `json:"alt"`
		}
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			fail("%v", err)
		}
		type prepared struct {
			doc, authored interface{}
			fields        []projField
			keys          []string
			binding       map[string]string
		}
		prepare := func(r e2eRun) prepared {
			var projection struct {
				Fields []projField `json:"fields"`
			}
			pdec := json.NewDecoder(strings.NewReader(r.Standalone.Projection))
			pdec.UseNumber() // an integer of 64 bits must not pass through a float
			if err := pdec.Decode(&projection); err != nil {
				fail("%s: %v", c.ID, err)
			}
			return prepared{decode(r.Receipt.Document), decode(r.Receipt.AuthoredInput), projection.Fields, r.Standalone.ExpectedKeys, r.Receipt.Binding}
		}
		verify := func(doc, authored interface{}, fs []projField, keys []string, binding map[string]string) *want {
			o, err := materialized.NewMaterializedObject(fieldsOf(fs), keySet(keys))
			if err != nil {
				fail("%s: the object: %v", c.ID, err)
			}
			rc, err := receipt.Parse(doc)
			if err == nil {
				err = receipt.Verify(rc, o, authored, receipt.PathBinding(binding))
			}
			if err == nil {
				return nil
			}
			var re *receipt.Error
			if !errors.As(err, &re) {
				fail("%s: not an engine error: %v", c.ID, err)
			}
			return &want{re.Code, re.Rule}
		}
		base, alt := prepare(c.Base), prepare(c.Alt)
		body := func(d interface{}) map[string]interface{} { return obj(obj(d)["materialization"]) }
		for _, r := range []struct {
			which string
			p     prepared
		}{{"base", base}, {"alt", alt}} {
			if got := body(r.p.doc)["engine_identity"]; got != identity {
				fail("%s %s: the engine identity is %v, want %s", c.ID, r.which, got, identity)
			}
			if w := verify(r.p.doc, r.p.authored, r.p.fields, r.p.keys, r.p.binding); w != nil {
				fail("%s %s: the engine verifier refused the chain of the language: %v", c.ID, r.which, *w)
			}
			accepted++
		}
		expect := func(what string, got *want, w want) {
			if got == nil || *got != w {
				fail("%s: %s: got %v, want %v", c.ID, what, got, w)
			}
			refused++
		}
		// the receipt of one run does not describe the object of the other: a capability moves the object, and the receipt that was made for the old one is refused
		expect("the receipt of the base run against the object of the alt run", verify(base.doc, base.authored, alt.fields, alt.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		expect("the receipt of the alt run against the object of the base run", verify(alt.doc, alt.authored, base.fields, base.keys, alt.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		d := clone(base.doc)
		body(d)["input_digest"] = zeroDigest()
		expect("a changed input digest", verify(d, base.authored, base.fields, base.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-INPUT"})
		d = clone(base.doc)
		body(d)["output_digest"] = zeroDigest()
		expect("a changed output digest", verify(d, base.authored, base.fields, base.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-OUTPUT"})
		a := clone(base.authored)
		obj(a)["type"] = "another"
		expect("another authored input", verify(base.doc, a, base.fields, base.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-BINDS-INPUT"})
		list := func(d interface{}) []interface{} { return body(d)["applied_defaults"].([]interface{}) }
		evidence := list(base.doc)
		if len(evidence) == 0 {
			continue
		}
		d = clone(base.doc)
		obj(list(d)[0])["value_digest"] = zeroDigest()
		expect("a changed default evidence", verify(d, base.authored, base.fields, base.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-VALUE"})
		d = clone(base.doc)
		body(d)["applied_defaults"] = list(d)[1:]
		expect("a default evidence removed", verify(d, base.authored, base.fields, base.keys, base.binding), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-MEMBERSHIP"})
		first := obj(evidence[0])["path"].(string)
		b := map[string]string{}
		for k, v := range base.binding {
			if k != first {
				b[k] = v
			}
		}
		expect("a path with no binding", verify(base.doc, base.authored, base.fields, base.keys, b), want{receipt.CodeReceiptUnbound, "R-RECEIPT-PATH-BINDING"})
		b = map[string]string{}
		for k, v := range base.binding {
			b[k] = v
		}
		b[first] = "nope"
		expect("a path bound to a key the object does not have", verify(base.doc, base.authored, base.fields, base.keys, b), want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-MEMBERSHIP"})
		// the swap of the bindings of two defaulted fields
		for i := 0; i < len(evidence); i++ {
			for j := i + 1; j < len(evidence); j++ {
				pi, pj := obj(evidence[i])["path"].(string), obj(evidence[j])["path"].(string)
				b := map[string]string{}
				for k, v := range base.binding {
					b[k] = v
				}
				b[pi], b[pj] = b[pj], b[pi]
				got := verify(base.doc, base.authored, base.fields, base.keys, b)
				if obj(evidence[i])["value_digest"] == obj(evidence[j])["value_digest"] {
					if got != nil {
						fail("%s: two defaults of one value, bindings swapped: the engine refused %v", c.ID, *got)
					}
					swapAccepted++
				} else {
					if got == nil || *got != (want{receipt.CodeReceiptMismatch, "R-RECEIPT-EVIDENCE-VALUE"}) {
						fail("%s: two defaults of two values, bindings swapped: got %v", c.ID, got)
					}
					swapRefused++
				}
			}
		}
	}
	if accepted < 8 || swapAccepted < 1 || swapRefused < 1 {
		fail("the chains accepted %d, swaps of equal values %d, of different values %d", accepted, swapAccepted, swapRefused)
	}
	fmt.Printf("engine (go): %d chains of %s accepted by receipt.Verify, %d altered or mixed ones refused; swapped bindings: %d of equal values accepted (the engine cannot tell), %d of different values refused\n", accepted, identity, refused, swapAccepted, swapRefused)
}

func main() {
	if len(os.Args) == 4 && os.Args[1] == "end-to-end" {
		endToEnd(os.Args[2], os.Args[3])
		return
	}
	if len(os.Args) == 5 && os.Args[1] == "receipt-differential" {
		receiptDifferential(os.Args[2], os.Args[3], os.Args[4])
		return
	}
	if len(os.Args) == 4 && os.Args[1] == "differential" {
		differential(os.Args[2], os.Args[3])
		return
	}
	if len(os.Args) != 2 {
		fail("usage: engineharness <forms root> | differential <in> <out>")
	}
	text, err := os.ReadFile(filepath.Join(os.Args[1], "testdata", "p5", "materialize.json"))
	if err != nil {
		fail("%v", err)
	}
	var file struct {
		Cases []struct {
			ID     string `json:"id"`
			Expect struct {
				ExpectedKeys []string `json:"expected_keys"`
				Projection   string   `json:"projection"`
				OutputDigest string   `json:"output_digest"`
				Refused      []string `json:"refused"`
			} `json:"expect"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(text, &file); err != nil {
		fail("%v", err)
	}
	checked := 0
	for _, c := range file.Cases {
		if c.Expect.Refused != nil {
			continue
		}
		var projection struct {
			Fields []projField `json:"fields"`
		}
		if err := json.Unmarshal([]byte(c.Expect.Projection), &projection); err != nil {
			fail("%s: %v", c.ID, err)
		}
		keys := c.Expect.ExpectedKeys
		got, err := digestOf(projection.Fields, keys)
		if err != nil || got != c.Expect.OutputDigest {
			fail("%s: the engine's digest %q (%v), want %s", c.ID, got, err, c.Expect.OutputDigest)
		}
		if _, err := digestOf(projection.Fields, append(append([]string{}, keys...), "~extra")); err == nil {
			fail("%s: one key more was accepted", c.ID)
		}
		if _, err := digestOf(projection.Fields, keys[1:]); err == nil {
			fail("%s: one key less was accepted", c.ID)
		}
		for _, tamper := range []func(*projField){
			func(f *projField) { f.Value = "tampered" },
			func(f *projField) {
				f.Capability = map[bool]string{true: "implemented", false: "deprecated"}[f.Capability == "deprecated"]
			},
			func(f *projField) {
				f.Provenance = map[bool]string{true: "schema_default", false: "authored"}[f.Provenance == "authored"]
			},
		} {
			changed := append([]projField{}, projection.Fields...)
			tamper(&changed[0])
			if d, err := digestOf(changed, keys); err != nil || d == c.Expect.OutputDigest {
				fail("%s: a tampered field kept the digest (%v)", c.ID, err)
			}
		}
		checked++
	}
	if checked != 7 {
		fail("checked %d accepted cases, want 7", checked)
	}
	fmt.Printf("engine (go): %d accepted cases agree with the engine's NewMaterializedObject and digest\n", checked)
	receipts(os.Args[1])
}
