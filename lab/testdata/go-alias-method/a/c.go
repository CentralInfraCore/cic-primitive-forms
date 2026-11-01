package aliasmethod

type B struct{}

func (B) Name() string { return "a" }

type A = B

func Use() string { return A{}.Name() }
