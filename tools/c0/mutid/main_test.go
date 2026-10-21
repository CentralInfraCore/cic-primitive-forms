package main

import (
	"strings"
	"testing"
)

const orig = `package p

func a(x int) int {
	if x > 1 {
		return x + 1
	}
	return x + 1
}

func b(y int) int { return y * 2 }
`

func ids(t *testing.T, src string, mutated string) (fn, from, to, body string) {
	t.Helper()
	funcs, err := functions([]byte(src))
	if err != nil {
		t.Fatal(err)
	}
	m, err := derive("p.go", []byte(src), []byte(mutated), funcs)
	if err != nil {
		t.Fatal(err)
	}
	return m.fn, m.from, m.to, m.body
}

func TestTheChangeIsNamedByTokensAndTheFunction(t *testing.T) {
	fn, from, to, _ := ids(t, orig, strings.Replace(orig, "x > 1", "x >= 1", 1))
	if fn != "a" || from != ">" || to != ">=" {
		t.Fatalf("got %q %q %q", fn, from, to)
	}
}

func TestALineShiftAndComments(t *testing.T) {
	shifted := "package p\n\n// a comment\n\n\n" + strings.TrimPrefix(orig, "package p\n")
	fn1, from1, to1, body1 := ids(t, orig, strings.Replace(orig, "x > 1", "x >= 1", 1))
	fn2, from2, to2, body2 := ids(t, shifted, strings.Replace(shifted, "x > 1", "x >= 1", 1))
	if fn1 != fn2 || from1 != from2 || to1 != to2 || body1 != body2 {
		t.Fatalf("the id moved with the lines: %q/%q vs %q/%q", fn1, body1, fn2, body2)
	}
}

func TestAnEditOutsideTheFunctionKeepsTheBodyDigestAndAnEditInsideMovesIt(t *testing.T) {
	_, _, _, base := ids(t, orig, strings.Replace(orig, "x > 1", "x >= 1", 1))
	other := strings.Replace(orig, "y * 2", "y * 3", 1)
	_, _, _, outside := ids(t, other, strings.Replace(other, "x > 1", "x >= 1", 1))
	if base != outside {
		t.Fatal("a change in b moved the digest of a")
	}
	inside := strings.Replace(orig, "return x + 1\n\t}", "return x + 2\n\t}", 1)
	_, _, _, moved := ids(t, inside, strings.Replace(inside, "x > 1", "x >= 1", 1))
	if base == moved {
		t.Fatal("a change in a did not move the digest of a")
	}
}

func TestTwoEqualChangesInOneFunctionAreToldApartByTheirPosition(t *testing.T) {
	m1 := strings.Replace(orig, "return x + 1\n\t}", "return x - 1\n\t}", 1)
	m2 := strings.Replace(orig, "\treturn x + 1\n}", "\treturn x - 1\n}", 1)
	funcs, _ := functions([]byte(orig))
	a, err := derive("p.go", []byte(orig), []byte(m1), funcs)
	if err != nil {
		t.Fatal(err)
	}
	b, err := derive("p.go", []byte(orig), []byte(m2), funcs)
	if err != nil {
		t.Fatal(err)
	}
	if a.from != b.from || a.to != b.to || a.fn != b.fn {
		t.Fatalf("not the same kind of change: %+v %+v", a, b)
	}
	if a.at == b.at {
		t.Fatal("the position in the token stream does not tell them apart")
	}
}

func TestAnIdenticalTokenStreamIsRefused(t *testing.T) {
	funcs, _ := functions([]byte(orig))
	if _, err := derive("p.go", []byte(orig), []byte(orig+"\n\n// c\n"), funcs); err == nil {
		t.Fatal("a mutant that changes no token was given an id")
	}
}
