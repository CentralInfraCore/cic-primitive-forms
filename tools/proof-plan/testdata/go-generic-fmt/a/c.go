package genfmt

import "fmt"

type S struct{}

func (S) String() string { return "a" }

func Show[T any](x T) string { return fmt.Sprint(x) }

func Use() string { return Show(S{}) }
