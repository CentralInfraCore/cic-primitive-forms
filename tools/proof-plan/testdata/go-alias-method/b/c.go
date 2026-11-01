package aliasmethod

type B struct{}

func (B) Name() string { return "b" }

type A = B

func Use() string { return A{}.Name() }
