package convslice

import "fmt"

type S struct{}

func (S) String() string { return "b" }

func Use() string { return fmt.Sprint([]S{{}}) }
