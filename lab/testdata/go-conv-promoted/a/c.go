package convprom

import "fmt"

type E struct{}

func (E) String() string { return "a" }

type S struct{ E }

func Use() string { return fmt.Sprint(S{}) }
