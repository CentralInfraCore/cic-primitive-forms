// Checks the shared flat-materialization cases against the engine (Go): for each accepted case the engine's NewMaterializedObject accepts the fields of the
// expected projection under the expected key set, and MaterializedObjectDigest gives the expected output_digest. The expected values are computed by
// tools/p5/materialize_cases.py. The negative checks make a green run mean something: a key set with one key missing, or one key more, must be refused, and
// a changed value, capability or provenance must change the digest. The Rust peer is tools/p5/engine-harness-rust.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/CentralInfraCore/cic-primitive-engine/go/digestprojection"
	"github.com/CentralInfraCore/cic-primitive-engine/go/materialized"
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

func main() {
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
	if checked != 6 {
		fail("checked %d accepted cases, want 6", checked)
	}
	fmt.Printf("engine (go): %d accepted cases agree with the engine's NewMaterializedObject and digest\n", checked)
}
