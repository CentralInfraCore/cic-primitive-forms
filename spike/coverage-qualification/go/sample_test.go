package covsample

import (
	"os"
	"testing"
)

// reduced selects the smaller test set (COV_REDUCED=1); the default runs the full one.
var reduced = os.Getenv("COV_REDUCED") == "1"

func TestCond(t *testing.T) {
	if Cond(1) != 1 {
		t.Fatal()
	}
	if !reduced && Cond(-1) != 2 {
		t.Fatal()
	}
}

func TestCommaOk(t *testing.T) {
	m := map[string]int{"a": 4}
	if CommaOk(m, "a") != 4 || CommaOk(m, "b") != 0 {
		t.Fatal()
	}
}

func TestSwitchNoDefault(t *testing.T) {
	if SwitchNoDefault(0) != "zero" || SwitchNoDefault(9) != "none" {
		t.Fatal()
	}
	if !reduced && SwitchNoDefault(1) != "one" {
		t.Fatal()
	}
}

func TestSwitchDefault(t *testing.T) {
	if SwitchDefault(1) != "other" {
		t.Fatal()
	}
	if !reduced && SwitchDefault(11) != "big" {
		t.Fatal()
	}
}

func TestTypeSwitch(t *testing.T) {
	if TypeSwitch(1) != "int" || TypeSwitch(1.5) != "other" {
		t.Fatal()
	}
	if !reduced && TypeSwitch("s") != "string" {
		t.Fatal()
	}
}

func TestSelectDefault(t *testing.T) {
	ch := make(chan int, 1)
	if SelectDefault(ch) != -1 {
		t.Fatal()
	}
	if !reduced {
		ch <- 7
		if SelectDefault(ch) != 7 {
			t.Fatal()
		}
	}
}

func TestSelectNoDefault(t *testing.T) {
	a, b := make(chan int, 1), make(chan int, 1)
	a <- 1
	if SelectNoDefault(a, b) != 1 {
		t.Fatal()
	}
	if !reduced {
		b <- 2
		if SelectNoDefault(a, b) != 2 {
			t.Fatal()
		}
	}
}

func TestForCond(t *testing.T) {
	if ForCond(2) != 3 {
		t.Fatal()
	}
}

func TestForClause(t *testing.T) {
	if ForClause(0) != 0 {
		t.Fatal()
	}
	if !reduced && ForClause(3) != 3 {
		t.Fatal()
	}
}

func TestForRange(t *testing.T) {
	if ForRange([]int{1, 2}) != 3 || ForRange(nil) != 0 {
		t.Fatal()
	}
}

func TestForEver(t *testing.T) {
	if ForEver() != 3 {
		t.Fatal()
	}
}

func TestAnd(t *testing.T) {
	if !And(true, true) || And(false, true) {
		t.Fatal()
	}
	if !reduced && And(true, false) {
		t.Fatal()
	}
}

func TestOr(t *testing.T) {
	if !Or(false, true) || !Or(true, false) {
		t.Fatal()
	}
	if !reduced && Or(false, false) {
		t.Fatal()
	}
}

func TestAtoi(t *testing.T) {
	if n, err := Atoi("5"); err != nil || n != 6 {
		t.Fatal()
	}
	if !reduced {
		if _, err := Atoi("x"); err == nil {
			t.Fatal()
		}
	}
}
