# ADR 0001: recognizing short and expanded form

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Resolves issue #3 points 1 and 7 together.

## Origin of the rule

The decision of record is: when all the mandatory keys of the primitive are
present the node is expanded, otherwise it is short. An earlier draft of this
ADR added partial-set errors and per-position cases that departed from that
decision; this version returns to it and only adds what is needed to make it
total (the extra-key case).

## Terms

- `A`: the key set the schema declares for the expanded primitive. Currently
  six keys for `Access` (`value`, `access`, `modify`, `inherit`,
  `default_injection`, `conformance`). Read from the schema, never hardcoded.
- `K`: the key set of the map being classified.
- A *primitive position* is a place the schema marks as holding a primitive.
  Recognition happens per primitive position, not per YAML map.

## The rule

A node that is not a map (scalar, list, null) is **Short**. For a map:

| Condition | Result |
|---|---|
| `K == A` | **Expanded** |
| `A ⊂ K` (all of `A` plus extra keys) | **Error** `EXPANDED_WITH_EXTRA_KEY` |
| `A ⊄ K` | **Short** |

The recognizer looks only at the keys and at the fact that the position is a
primitive position. It never looks at values, at `inherit: true/false/0`, or at
the type of the position. Whether a Short node is acceptable at its position
(a map at a scalar position, an unknown field, a value of the wrong type) is the
job of `validate`, not of `recognize`.

Consequences:

1. **Missing keys mean Short.** `{value: 1, access: inherit}` is a short-form map
   value, not a partial override. At a scalar or structured position `validate`
   rejects it; at an `any` position it is data. This is the accepted cost of the
   simple rule, and it is silent at `any` positions.
2. **No guessing.** All of `A` plus an extra key is never read as data.
3. **`any` values are opaque.** The content of `value` at an `any` position is not
   re-recognized, so a literal map inside it is never mistaken for a primitive.
4. **Access keys take precedence in structured shapes.** If a structured shape's
   child fields include all of `A` (`C ⊇ A`), a map with exactly `A` is still
   Expanded. Literal structured data with that key set must be written under an
   explicit expanded wrapper, with the data under `value`.
5. **A literal map equal to `A`** at an `any` position is written the same way:
   as the `value` of an expanded wrapper.

## Round trip

Preserving the recognition class is necessary but not sufficient. The law is

`expand(collapse(L, I, S), S) ≡ L`

for an expanded primitive `L`, the authored intent `I` and the fixed schema `S`,
where `≡` covers the value and the policy semantics (`access`, `modify`,
`inherit`, `default_injection`, `conformance`), not YAML bytes.

`collapse` may produce a Short form for `L` only if all of these hold:

- every policy field of `L` equals the symbolic default `expand` would fill
  from `S` (`access: inherit`, `modify: inherit`, `inherit: true`,
  `default_injection: null`, `conformance: implemented`);
- `I`, when present, did not author this node in expanded form (an authored
  expanded node stays expanded, even when equal to the defaults);
- the Short form is recognized as Short: `value` is not a map whose key set is
  `⊇ A`.

Otherwise `collapse` leaves the node expanded. No explicit policy setting is
ever dropped.

## Errors

Recognition has one error, `EXPANDED_WITH_EXTRA_KEY`, with logical path and
source position. Errors from `validate` (map at scalar position, unknown field,
unknown key) and the structure of every error are defined in ADR 0007. A
duplicate key is a `parse`-phase error.

## Vectors

Provisional vectors are in `corpus/recognition/vectors.yaml`, in the format of
ADR 0005. The `any` position of the original vectors is now `opaque` (ADR 0002);
the case ids keep their historical `any-` spelling because ids are stable.

## Open questions

- List elements: whether and how an element of a list is a primitive position
  depends on the schema contract (issue #3 point 4); the list vector is
  provisional.
- The silent `any` case of consequence 1 is accepted. If a warning channel
  exists in the error contract, it is a candidate there, not here.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits:

- **Cross-reference to ADR 0010.** The collapse law takes one more input, the conformance basis of the position: a position whose basis is `binding_attested` is never shortened, even when all six fields equal the defaults. For every other basis the law above is unchanged.
- The key set `A` is read from the schema; its identity depends on the grammar identity of ADR 0002.
- The silent `opaque` case (a short map equal to `A`) is an accepted cost; a warning channel is not defined.
