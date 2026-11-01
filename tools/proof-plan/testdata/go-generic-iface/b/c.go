package geniface

import "fmt"

type S struct{}

func (S) String() string { return "b" }

func Show[T any](x T) string { return fmt.Sprint(x) }

func Use() string {
	var s fmt.Stringer = S{}
	return Show(s)
}
