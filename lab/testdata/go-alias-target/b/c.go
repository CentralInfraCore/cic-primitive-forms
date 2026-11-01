package aliastarget

type B struct{}

func (B) Name() string { return "a" }

type C struct{}

func (C) Name() string { return "c" }

type A = C

func Use() string { return A{}.Name() }
