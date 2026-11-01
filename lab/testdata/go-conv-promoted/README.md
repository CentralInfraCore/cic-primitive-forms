# Dynamic dispatch the graph did not follow (red cases, and their fix)

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

## The fix

One reachability function, `dispatch`, used by the conversion rule and by every place that instantiates a generic function or type
(`Info.Instances`): for a named type of the loaded packages, the whole method set of the type and of a pointer to it (promoted methods
included), then its type arguments, fields and elements; an interface or a type parameter adds nothing, because what is behind it
became an interface value where it was converted, or is a type argument where it was instantiated. Over-approximate on purpose. On
`go/`: 225 edges added on 117 declarations, none removed.

Two planted defects bring the old model back: `-planted-no-typeargs` (no instance rule) and `-planted-old-methodsof` (the conversion
rule looking only at declared methods). `repro_dispatch.py --expect vulnerable` with both shows nine RED again; `--expect fixed`
without them shows eleven GREEN.

What the instance rule does not model, said here so nobody reads more into it: a type argument of a type that is not in the loaded
packages (its methods are the toolchain's and go.sum's business, the environment key), a type argument that cannot be resolved (the
node is marked opaque, so the kernel refuses it), and anything `fmt` or another interface reaches by reflection beyond the
fields, elements and type arguments followed here.
