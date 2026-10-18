package covcomplex

import (
	"os"
	"testing"
)

// reduced selects the smaller test set (COV_REDUCED=1); the default runs the full one.
var reduced = os.Getenv("COV_REDUCED") == "1"

func TestElseIf(t *testing.T) {
	for n, want := range map[int]string{-1: "neg", 5: "small"} {
		if ElseIf(n) != want {
			t.Fatal(n)
		}
	}
	if !reduced && (ElseIf(0) != "zero" || ElseIf(50) != "big") {
		t.Fatal()
	}
}

func TestFind(t *testing.T) {
	m := [][]int{{1, 2}, {3}}
	if !Find(m, 3) {
		t.Fatal()
	}
	if !reduced && Find(m, 9) {
		t.Fatal()
	}
}

func TestFall(t *testing.T) {
	if Fall(2) != 2 || Fall(9) != -1 {
		t.Fatal()
	}
	if !reduced && Fall(1) != 3 {
		t.Fatal()
	}
}

func TestDrain(t *testing.T) {
	if reduced {
		t.Skip("the reduced set does not run Drain")
	}
	ch, done, res := make(chan int), make(chan struct{}), make(chan int)
	go func() { res <- Drain(ch, done) }()
	ch <- 1
	ch <- 2
	close(done)
	if got := <-res; got != 3 {
		t.Fatal(got)
	}
}

func TestClosure(t *testing.T) {
	if Closure([]int{2, 3}) != 1 {
		t.Fatal()
	}
	if !reduced && Closure([]int{-1, 0}) != 0 {
		t.Fatal()
	}
}

func TestNamed(t *testing.T) {
	if !Named(false, true) || !Named(true, false) {
		t.Fatal()
	}
	if !reduced && bool(Named(false, false)) {
		t.Fatal()
	}
}

func TestTypeSw(t *testing.T) {
	if TypeSw(4) != 4 || TypeSw(1.5) != 0 {
		t.Fatal()
	}
	if !reduced && TypeSw("abc") != 3 {
		t.Fatal()
	}
}

func TestForCondBreak(t *testing.T) {
	if ForCondBreak(10) != 3 {
		t.Fatal()
	}
	if !reduced && ForCondBreak(2) != 2 {
		t.Fatal()
	}
}

func TestParse(t *testing.T) {
	if Parse("5") != 5 {
		t.Fatal()
	}
	if !reduced && (Parse("x") != 0 || Parse("-3") != 0) {
		t.Fatal()
	}
}

func TestRangeInt(t *testing.T) {
	if RangeInt(3) != 3 {
		t.Fatal()
	}
}
