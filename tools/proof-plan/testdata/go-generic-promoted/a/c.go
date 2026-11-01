package genprom

import "fmt"

type E struct{}

func (E) String() string { return "a" }

type S struct{ E }

func Show[T any](x T) string { return fmt.Sprint(x) }

func Use() string { return Show(S{}) }
