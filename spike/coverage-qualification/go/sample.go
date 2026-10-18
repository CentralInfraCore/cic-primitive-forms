// Package covsample is a branch coverage qualification sample (cic-branch/1). NOT production code.
// Hand-counted branches are in the table at the end of the file.
package covsample

import "strconv"

func Cond(x int) int {
	if x > 0 {
		return 1
	} else {
		return 2
	}
}

func CommaOk(m map[string]int, k string) int {
	if v, ok := m[k]; ok {
		return v
	}
	return 0
}

// SwitchNoDefault has two cases and an implicit no-match edge.
func SwitchNoDefault(n int) string {
	switch n {
	case 0:
		return "zero"
	case 1:
		return "one"
	}
	return "none"
}

// SwitchDefault is a tagless switch with a default: two edges, no implicit one.
func SwitchDefault(n int) string {
	switch {
	case n > 10:
		return "big"
	default:
		return "other"
	}
}

// TypeSwitch has two cases and an implicit no-match edge.
func TypeSwitch(v interface{}) string {
	switch v.(type) {
	case int:
		return "int"
	case string:
		return "string"
	}
	return "other"
}

// SelectDefault: one communication case and a default.
func SelectDefault(ch chan int) int {
	select {
	case x := <-ch:
		return x
	default:
		return -1
	}
}

// SelectNoDefault: two communication cases, no implicit edge (it blocks).
func SelectNoDefault(a, b chan int) int {
	select {
	case x := <-a:
		return x
	case y := <-b:
		return y
	}
}

func ForCond(n int) int {
	s := 0
	for n > 0 {
		s += n
		n--
	}
	return s
}

func ForClause(n int) int {
	s := 0
	for i := 0; i < n; i++ {
		s += i
	}
	return s
}

func ForRange(v []int) int {
	s := 0
	for _, x := range v {
		s += x
	}
	return s
}

// ForEver is unconditional: no entry decision; the break is covered by its if.
func ForEver() int {
	i := 0
	for {
		i++
		if i >= 3 {
			break
		}
	}
	return i
}

func And(a, b bool) bool {
	return a && b
}

func Or(a, b bool) bool {
	return a || b
}

// Atoi: `if err != nil` is a conditional in Go, not an error_path branch.
func Atoi(s string) (int, error) {
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0, err
	}
	return n + 1, nil
}

// Hand-counted branches (cic-branch/1): total / covered by the full tests / covered by the reduced tests
//   Cond            conditional 2 / 2 / 1
//   CommaOk         conditional 2 / 2 / 2
//   SwitchNoDefault match 3 (two cases + implicit no-match) / 3 / 2
//   SwitchDefault   match 2 / 2 / 1
//   TypeSwitch      match 3 / 3 / 2
//   SelectDefault   match 2 / 2 / 1
//   SelectNoDefault match 2 / 2 / 1
//   ForCond         loop 2 / 2 / 2
//   ForClause       loop 2 / 2 / 1
//   ForRange        loop 2 / 2 / 2
//   ForEver         conditional 2 / 2 / 2  (no loop branch)
//   And             short_circuit 4 / 4 / 3
//   Or              short_circuit 4 / 4 / 3
//   Atoi            conditional 2 / 2 / 1  (error_path is 0 for Go)
// Per category: conditional 8/8/6, match 12/12/7, loop 6/6/5, short_circuit 8/8/6, error_path 0/0/0.
