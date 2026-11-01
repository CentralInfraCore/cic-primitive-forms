# Generic function that formats its argument: a reproduced unsafe reuse (known red case, not fixed)

`Show[T any](x T) string { return fmt.Sprint(x) }` is called with `S{}`; `fmt` calls `S.String` by dynamic dispatch. `S.String` changes
("a" -> "b") and the test goes from pass to fail, while the Merkle key of the test is the same.

Why the graph misses it (`lab/extractor/typeddeps/main.go`, `conversions`): the conversion rule gives a declaration an edge to every
method of a named type whose value becomes an interface value. The argument of `fmt.Sprint` is a value of the type parameter `T`, and
`types.IsInterface` is true for a type parameter, so the check returns before the method set is looked at; at the call site `Show(S{})`
the destination is the type parameter, which the rule skips on purpose (`if _, isParam := dst.(*types.TypeParam)`).

Seen by `lab/measure/qualify.py` (the probe `go.func.generic`, second of two), so `go.func.generic` is not qualified, and the kernel
answers UNVERIFIABLE for any test whose closure holds a generic function (`lab/measure/test_qualify_go.py`, `test_red_*`).
The extractor is not changed here.
