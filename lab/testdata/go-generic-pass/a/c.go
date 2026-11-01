package genpass

import "fmt"

type S struct{}

func (S) String() string { return "a" }

func Show[T any](x T) string { return fmt.Sprint(x) }

func Show2[T any](x T) string { return Show(x) }

func Use() string { return Show2(S{}) }
