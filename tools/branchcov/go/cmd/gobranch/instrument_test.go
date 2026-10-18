package main

import (
	"fmt"
	"go/parser"
	"go/token"
	"strings"
	"testing"
)

func edgesOf(t *testing.T, src string) ([]string, string, error) {
	t.Helper()
	fset := token.NewFileSet()
	next := 0
	var edges []Edge
	out, err := Instrument(fset, "x.go", []byte("package p\n"+src), &next, &edges)
	var got []string
	for _, e := range edges {
		got = append(got, fmt.Sprintf("%s/%s/%s", e.Category, e.Kind, e.Label))
	}
	return got, string(out), err
}

func TestEnumeration(t *testing.T) {
	cases := []struct {
		name, src string
		want      string
	}{
		{"if without else", "func f(x int) { if x > 0 { _ = x } }", "conditional/if/true conditional/if/false"},
		{"else if chain", "func f(x int) { if x > 0 { } else if x < 0 { } else { } }", "conditional/if/true conditional/if/false conditional/if/true conditional/if/false"},
		{"and chain", "func f(a, b, c bool) bool { return a && b && c }",
			strings.Repeat("short_circuit/operand/true short_circuit/operand/false ", 3)},
		{"if with and", "func f(a, b bool) { if a && b { } }",
			"conditional/if/true conditional/if/false short_circuit/operand/true short_circuit/operand/false short_circuit/operand/true short_circuit/operand/false"},
		{"not is one operand", "func f(a, b bool) bool { return !(a && b) }",
			"short_circuit/operand/true short_circuit/operand/false short_circuit/operand/true short_circuit/operand/false"},
		{"switch without default", "func f(x int) { switch x { case 1: case 2: } }", "match/switch/case match/switch/case match/switch/no-match"},
		{"switch with default", "func f(x int) { switch { case x > 1: default: } }", "match/switch/case match/switch/default"},
		{"type switch without default", "func f(v interface{}) { switch v.(type) { case int: case string: } }", "match/type-switch/case match/type-switch/case match/type-switch/no-match"},
		{"select without default", "func f(a, b chan int) { select { case <-a: case <-b: } }", "match/select/case match/select/case"},
		{"select with default", "func f(a chan int) { select { case <-a: default: } }", "match/select/case match/select/default"},
		{"for condition", "func f(n int) { for n > 0 { n-- } }", "loop/for/iterate loop/for/exit"},
		{"for clause", "func f(n int) { for i := 0; i < n; i++ { } }", "loop/for/iterate loop/for/exit"},
		{"unconditional for", "func f() { for { break } }", ""},
		{"for true", "func f() { for true { break } }", ""},
		{"range", "func f(v []int) { for range v { } }", "loop/range/iterate loop/range/exit"},
		{"closure", "func f() func(bool, bool) bool { return func(a, b bool) bool { return a || b } }",
			"short_circuit/operand/true short_circuit/operand/false short_circuit/operand/true short_circuit/operand/false"},
		{"constant expression stays constant", "const c = true && false\nfunc f() { const d = true || false\n _ = d }", ""},
		{"package level initializer", "var v = func(a, b bool) bool { return a && b }", "short_circuit/operand/true short_circuit/operand/false short_circuit/operand/true short_circuit/operand/false"},
	}
	for _, c := range cases {
		got, _, err := edgesOf(t, c.src)
		if err != nil {
			t.Errorf("%s: %v", c.name, err)
			continue
		}
		if g := strings.Join(got, " "); g != strings.TrimSpace(c.want) {
			t.Errorf("%s:\n got  %s\n want %s", c.name, g, c.want)
		}
	}
}

func TestOutputParses(t *testing.T) {
	srcs := []string{
		"func f(m [][]int) { outer: for _, r := range m { for _, v := range r { if v > 0 { break outer } } } }",
		"func f(x int) int { switch x { case 1: return 1; case 2: fallthrough; default: return 0 } }",
		"func f(ch chan int) { loop: for { select { case <-ch: break loop } } }",
	}
	for _, s := range srcs {
		_, out, err := edgesOf(t, s)
		if err != nil {
			t.Fatalf("%v", err)
		}
		if _, err := parser.ParseFile(token.NewFileSet(), "o.go", out, 0); err != nil {
			t.Errorf("the instrumented output does not parse: %v\n%s", err, out)
		}
	}
}

func TestRefusals(t *testing.T) {
	cases := map[string]string{
		"goto":                          "func f() { goto L\nL: }",
		"fallthrough into the no-match": "func f(x int) { switch x { case 1: fallthrough } }",
	}
	for name, src := range cases {
		if _, _, err := edgesOf(t, src); err == nil {
			t.Errorf("%s: must be refused", name)
		}
	}
}

func TestRangeExitIsNotCountedWhenBroken(t *testing.T) {
	_, out, err := edgesOf(t, "func f(v []int) { for range v { break } }")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "gobranchBrk1 = true") || !strings.Contains(out, "if !gobranchBrk1") {
		t.Errorf("the break must set the flag the exit edge depends on:\n%s", out)
	}
}
