package genimpl

type Namer interface{ Name() string }

type S struct{}

func (S) Name() string { return "a" }

func NameOf[T Namer](x T) string { return x.Name() }

func Use() string { return NameOf(S{}) }
