package convfield

import "fmt"

type E struct{}

func (E) String() string { return "b" }

type S struct{ E E }

func Use() string { return fmt.Sprint(S{}) }
