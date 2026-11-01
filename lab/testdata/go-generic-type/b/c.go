package gentype

import "fmt"

type S struct{}

func (S) String() string { return "b" }

type Box[T any] struct{ v T }

func (b Box[T]) Str() string { return fmt.Sprint(b.v) }

func Use() string { return Box[S]{}.Str() }
