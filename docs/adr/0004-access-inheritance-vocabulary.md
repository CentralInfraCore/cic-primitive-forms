# ADR 0004: Access inheritance vocabulary (draft)

Status: **proposed.** Addresses issue #3 point 8 (the type of the symbolic
`inherit`) and fixes how the library reads the three-valued `inherit` key. It
does **not** define how inheritance is resolved: the normative semantics are
open upstream (see "Upstream status"), and resolution is P3.

## The word `inherit` is used in three places

| Place | Values | Meaning |
|---|---|---|
| `access` / `modify` value | the symbol `inherit`, or a `CertPattern` list | this field takes its rule from the parent's PolicySurface rules (`access.yaml`, default `inherit`) |
| Access key `inherit` | `true` (default), `false`, `0` | whether sub-objects inherit this field's rule (`access.yaml`, D-010) |
| `PolicySurface.inherit_policy` | `true` (default), `false`, `0` | the same three states at object level (`policy-surface.yaml`) |

The library keeps the three apart. They share a spelling, not a type.

## Sources

| Source | Used for |
|---|---|
| `cic-primitives` `schemas/atomic/access.yaml` | `access` / `modify` typed `list<CertPattern>` with default `inherit`; `inherit` typed `boolean \| 0`; `cert_pattern` syntax |
| `cic-primitives` `schemas/aggregate/policy-surface.yaml` | `inherit_policy`, evaluation order |
| `cic-primitives` `ai/DECISIONS.md` D-010 | the three meanings of `inherit` |
| `cic-primitives` `proposals/atom-grammar/README.md` (P0.4) | the upstream statement that the three-state `inherit` is not a stable boolean |

## Upstream status

The grammar document states that the three-state `inherit` (`true` / `false` /
`0`) is not a stable boolean type, that a real enum is needed (`inherit` /
`no-inherit` / `reset`), and that it belongs to the full policy grammar of
`access` (P0.4), "not separately". P0.4 is explicitly outside the grammar as
written.

Consequences for this library:

- It does not invent the enum or its semantics.
- The sources do not sharply separate `false` from `0`: D-010 says `false` gives
  the sub-object "its default rules" and `0` makes it "recompute completely";
  `policy-surface.yaml` says "built from scratch"; the prompt map says "parent
  rules do not apply". The model exists; the bridge breaks at the normative
  difference between `false` and `0`. Resolution cannot be specified until
  upstream decides it.

## What the library does until then

1. **`inherit` key.** The accepted values are exactly three, told apart by YAML
   scalar type: boolean `true`, boolean `false`, integer `0`. Anything else
   (integer `1`, the strings `"true"` / `"0"`, `null`, a list) is a validation
   error. `false` and `0` are never coerced into one another. An implementation
   must not use loose equality (in several languages `0 == false`); it maps the
   parsed scalar type to an internal three-valued type at the YAML boundary.
2. **Round trip keeps the spelling.** `0` is emitted as `0`, `false` as `false`.
   The mapping from the three spellings to the internal type is a bijection, so
   nothing is lost.
3. **`access` / `modify`.** The accepted values are the scalar symbol `inherit`
   or a list of `CertPattern`. A `CertPattern` is `field=value` pairs
   (`OU=operators,O=acme-corp`, wildcard `*` allowed), so the bare string
   `inherit` is not a valid pattern: `access: inherit` (symbol) and
   `access: [inherit]` (a list holding an invalid pattern) cannot be confused.
   The empty list `[]` means *nobody* (`access.yaml`), it is **not** `inherit`
   and never turns into it (ADR 0003: empty is a value, not a request for the
   default).
4. **Type conflict of the symbol.** `access.yaml` types the key `list<CertPattern>`
   and defaults it to `inherit`, which is a symbol in a list-typed key. The
   library accepts the symbol exactly because the schema declares it as the
   default, and proposes upstream that the type be written as the union
   `list<CertPattern> | inherit`. Until that is changed the library's rule is the
   one in item 3.
5. **Expand** fills `access: inherit`, `modify: inherit`, `inherit: true`
   symbolically (PHASES, ADR 0001). It does not resolve anything.
6. **Recognition (ADR 0001) is unaffected.** It looks at keys only, never at the
   values of `inherit`.
7. **`inherit_policy`** follows the same vocabulary rule as the `inherit` key.

## Not decided here

- The meaning of `false` versus `0`, and the evaluation of inheritance. The
  documented evaluation order (`policy-surface.yaml`: field-level Access, then
  `surface_rules`, then `default_access` / `default_modify`, then the parent's
  PolicySurface when `inherit_policy` is true) is recorded for P3, not
  re-specified.
- Whether the internal enum uses the upstream names (`inherit` / `no-inherit` /
  `reset`); that is an implementation detail until upstream decides.
- What "sub-object" covers given ADR 0002 (a nested composite, the record of a
  list element, a case of a choice).

## Vectors

Provisional vectors are in `corpus/inherit/vectors.yaml`. The corpus format is
still a P0 item.

## Open questions

1. Upstream: the normative difference between `false` and `0`, and the final
   enum (P0.4). Blocks the resolution part of P3.
2. Upstream: write the `access` / `modify` type as a union. A `cic-primitives`
   issue is the place; not filed by this ADR.
3. What a "sub-object" is for the purposes of `inherit`, in terms of the Shape
   structure of ADR 0002.
