# Implicit calls in Rust: which ones the graph follows

One crate in two states (`a`, `b`), eleven tests. In `b` the impl method behind an implicit call changes, every test goes from pass to fail,
and the question is whether the Merkle key of the test moves. `python3 lab/measure/repro_rust_dispatch.py SCIPDEPS TOOLCHAIN --expect ...`.

| Test | The implicit call | State before the fix |
|---|---|---|
| t_to_string | `Display::fmt` through `to_string()` | GREEN |
| t_dyn_display | `Display::fmt` through `Box<dyn Display>` | GREEN |
| t_impl_arg | `Display::fmt` through an `impl Display` argument | GREEN |
| t_question_from | `From::from` through `?` | GREEN |
| t_for_next | `Iterator::next` through `for` | GREEN |
| t_add | `Add::add` through `+` | GREEN |
| t_eq | `PartialEq::eq` through `==` | GREEN |
| t_index | `Index::index` through `[]` | GREEN |
| t_deref | `Deref::deref` through a method call | GREEN |
| t_sort_ord | `Ord::cmp` through `sort()` | GREEN |
| **t_drop** | `Drop::drop` at the end of a scope, for `impl Drop for D<'_>` | **RED** |

## The red case

`impl Drop for D<'_>` has the id ``impl#[`D<'_>`][Drop]drop().``: a self type with a parameter is quoted in backticks. The rule "a type
reaches the methods of its impl blocks" (`implSelf` in `lab/extractor/scipdeps/main.go`) reads only ``impl#[Name]``, so the node of `D`
has **no edge** to its `drop`. Any impl for a type with a lifetime or a type parameter is outside the rule.

On the crate itself: of 89 impl methods, 54 have such a self type (`Differ<'_>`, `Reader<'_>`, `Walker<'_>`, `State<'_, '_>`,
`Table<'a>`, `Walk<'_>`), and none of the types has an edge to any of them. All 54 are inherent methods, which a caller reaches through its
own direct edge when it names them, so the crate is not affected in practice today; the first trait impl for such a type (`Display`, `Drop`,
`Iterator`) would be. The state is latent, not harmless.
