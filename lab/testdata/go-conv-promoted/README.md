# Dynamic dispatch the graph does not follow (known red cases, not fixed)

Eleven pairs of Go sources, `go-generic-*` and `go-conv-*`, one in which the graph is right and nine in which it is not. In each, a
method that `fmt` calls by dynamic dispatch changes ("a" -> "b"), the test goes from pass to fail, and the Merkle key of the test is
the same. Run: `python3 lab/measure/repro_dispatch.py BIN --expect vulnerable`.

| Case | What `fmt` receives | State before the fix |
|---|---|---|
| go-generic-impl | a type parameter whose constraint method is called | GREEN |
| go-generic-iface | a type argument that is an interface (`fmt.Stringer`) | GREEN |
| go-generic-fmt | a value of a type parameter, `fmt.Sprint(x)` | RED |
| go-generic-ptr | `&S{}` as the type argument, `func (*S) String` | RED |
| go-generic-promoted | `S` embedding `E`, `String` promoted from `E` | RED |
| go-generic-pass | the type parameter handed to another generic function | RED |
| go-generic-type | a generic type `Box[S]` that formats its field | RED |
| go-generic-slice | `[]S` as the type argument | RED |
| go-conv-promoted | **no generics**: `fmt.Sprint(S{})`, `String` promoted | RED |
| go-conv-slice | **no generics**: `fmt.Sprint([]S{{}})` | RED |
| go-conv-field | **no generics**: `fmt.Sprint(S{...})`, a field has `String` | RED |

Why (`lab/extractor/typeddeps/main.go`): the conversion rule gives a declaration an edge to the methods declared on a named type whose
value becomes an interface value. It does not look at methods promoted from embedded types, at the elements of a slice, map or array,
or at the fields; and it skips a type parameter (both as the destination of an argument and as the source of a value), so nothing
connects a generic function to the methods of its type argument.

The last three are not about generics: the base model has the same hole. A construct check cannot see it (a conversion is not a
construct), so the Go runs the kernel calls eligible may include some that rest on it; the measurement after the fix says how many.
