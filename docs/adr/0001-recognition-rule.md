# ADR 0001: recognizing short and expanded form (draft)

Status: **proposed.** Resolves issue #3 points 1 and 7 together. Not accepted
until reviewed; the open questions at the end need a decision.

## Problem

A map at a primitive position can be an expanded primitive (the `Access`
atom's key set) or the authored value itself. Key presence alone cannot always
tell them apart, so recognition must be defined, deterministic and
schema-directed, and it must survive a round trip.

## Terms

- `A`: the key set the schema declares for the expanded primitive. Currently
  six keys for `Access` (`value`, `access`, `modify`, `inherit`,
  `default_injection`, `conformance`). Read from the schema, never hardcoded.
- `K`: the key set of the map being classified.
- Position kind, given by the schema:
  - **scalar**: a scalar or list value, no child fields;
  - **structured**: a shape with declared child fields `C`;
  - **any**: `type: any`, the value is opaque to this library.

## The rule

Classification happens per primitive position, not per YAML map. A node that
is not a map (scalar, list, null) is always **Short**. For a map, apply the
first matching step:

1. `K == A`: **Expanded**, at every position kind.
2. Position **any**:
   - `K` non-empty and a proper subset of `A`: **Error** `AMBIGUOUS_ACCESS_KEYS`;
   - otherwise **Short**: an opaque map, including maps that only contain
     some Access key names next to foreign keys, or all of `A` plus extras.
3. Position **structured** with child fields `C`:
   - `K` a proper subset of `A` and `K ⊄ C`: **Error** `PARTIAL_EXPANDED`;
   - `K ∩ A` not a subset of `C`: **Error** `UNKNOWN_ACCESS_KEY`;
   - `K ⊆ C`: **Short** (the keys are child fields, even when a child is named
     like an Access key);
   - otherwise: **Error** `UNKNOWN_FIELD`.
4. Position **scalar** and any other map: **Error** `MAP_AT_SCALAR_POSITION`.

Consequences:

1. An **expanded** node has exactly the schema's key set. A map with all of `A`
   plus extra keys is not expanded: an error at scalar and structured
   positions, an opaque short map at any positions. There is no partial
   expanded form.
2. The content of `value` at an **any** position is **opaque**: it is never
   re-recognized, so a literal map inside it cannot be mistaken for a primitive.
3. A user who means a literal map equal to `A` at an any position must write the
   expanded wrapper explicitly, with that map under `value`. A short-form map
   whose key set equals `A` is by definition expanded.
4. A proper subset of `A` at an any position is rejected rather than guessed:
   `{value: 1}` could be a partial expanded form or user data, and the library
   does not guess.

## Round-trip invariant

`collapse` must never emit a short form that `recognize` would classify
differently:

`recognize(collapse(n)) == n` for every expanded node `n`.

So at an any position an expanded node whose `value` is a map equal to `A` or a
proper subset of `A` stays expanded under `collapse`: emitting it as a short
form would be re-recognized as expanded or rejected.

## Errors

Each error carries a stable code, the logical path and the source position:
`PARTIAL_EXPANDED`, `AMBIGUOUS_ACCESS_KEYS`, `UNKNOWN_ACCESS_KEY`,
`UNKNOWN_FIELD`, `MAP_AT_SCALAR_POSITION`. The full error contract is issue #3 point 6.

## Vectors

Provisional vectors live in `corpus/recognition/vectors.yaml`. The corpus file
format itself is still a P0 item; these vectors fix the behaviour, not the
format.

## Open questions

- Rule 4 rejects a proper subset of `A` at an any position. Is a stricter
  alternative (always Short for `any`) acceptable given that it silently reads a
  likely partial override as data?
- `UNKNOWN_FIELD` at structured positions overlaps with ordinary schema
  validation. Keep it here, or leave it to `validate`?
- Whether the recognizer is aware of `inherit: 0` / `false` is not needed here,
  but confirm that recognition never looks at values, only at keys.
