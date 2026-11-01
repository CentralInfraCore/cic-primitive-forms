package aliasconv

import "fmt"

type S struct{}

func (S) String() string { return "b" }

type A = S

func Use() string { return fmt.Sprint(A{}) }
