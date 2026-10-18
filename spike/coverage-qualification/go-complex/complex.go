// Package covcomplex is a second qualification sample for the Go measurement: nested and compound constructs.
// NOT production code. The hand-counted branches are at the end of the file.
package covcomplex

import "strconv"

func ElseIf(n int) string {
	if n < 0 {
		return "neg"
	} else if n == 0 {
		return "zero"
	} else if n < 10 {
		return "small"
	}
	return "big"
}

// Find leaves both range loops with a labeled break: the outer loop then has no exhaustion exit.
func Find(m [][]int, t int) bool {
	found := false
outer:
	for _, row := range m {
		for _, v := range row {
			if v == t {
				found = true
				break outer
			}
		}
	}
	return found
}

// Fall has a default, so there is no implicit no-match edge, and a fallthrough from the first clause.
func Fall(n int) int {
	r := 0
	switch n {
	case 1:
		r++
		fallthrough
	case 2:
		r += 2
	default:
		r = -1
	}
	return r
}

// FallVia: the second clause is selected directly only by 2; for 1 its body runs through the fallthrough, which is
// not a selection of that clause (cic-branch/1).
func FallVia(n int) int {
	r := 0
	switch n {
	case 1:
		r++
		fallthrough
	case 2:
		r += 2
	default:
		r = -1
	}
	return r
}

// Drain: a select inside an unconditional loop, left by a labeled break; no default, no implicit edge.
func Drain(ch chan int, done chan struct{}) int {
	s := 0
loop:
	for {
		select {
		case v := <-ch:
			s += v
		case <-done:
			break loop
		}
	}
	return s
}

// Closure: boolean operators inside a function literal, and a range loop with a condition in its body.
func Closure(xs []int) int {
	f := func(x int) bool { return x > 0 && x%2 == 0 }
	n := 0
	for _, x := range xs {
		if f(x) {
			n++
		}
	}
	return n
}

type MyBool bool

// Named uses a named boolean type: the wrappers must keep the type.
func Named(a, b MyBool) MyBool {
	if a || b {
		return true
	}
	return false
}

func TypeSw(v interface{}) int {
	switch x := v.(type) {
	case int:
		return x
	case string:
		return len(x)
	default:
		return 0
	}
}

// ForCondBreak: the condition's false edge is taken only when the loop is not left by the break.
func ForCondBreak(n int) int {
	c := 0
	for i := 0; i < n; i++ {
		if i == 3 {
			break
		}
		c++
	}
	return c
}

// Parse: an if with an initializer and a compound condition.
func Parse(s string) int {
	if v, err := strconv.Atoi(s); err == nil && v > 0 {
		return v
	}
	return 0
}

func RangeInt(n int) int {
	s := 0
	for i := range n {
		s += i
	}
	return s
}

// Hand-counted branches (cic-branch/1): total / covered by the full tests / covered by the reduced tests
//   ElseIf       conditional 6 / 6 / 4
//   Find         loop 4 + conditional 2 = 6 / 6 / 5
//   Fall         match 3 (no implicit edge) / 3 / 2
//   FallVia      match 3 (no implicit edge) / 3 / 2   (the reduced tests reach case 2 only through the fallthrough)
//   Drain        match 2 / 2 / 0
//   Closure      short_circuit 4 + loop 2 + conditional 2 = 8 / 8 / 7
//   Named        conditional 2 + short_circuit 4 = 6 / 6 / 4
//   TypeSw       match 3 / 3 / 2
//   ForCondBreak loop 2 + conditional 2 = 4 / 4 / 3
//   Parse        conditional 2 + short_circuit 4 = 6 / 6 / 3
//   RangeInt     loop 2 / 2 / 2
// Per category: conditional 16/16/12, match 11/11/6, loop 10/10/8, short_circuit 12/12/8; total 49 / 49 / 34.
