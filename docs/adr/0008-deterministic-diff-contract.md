# ADR 0008: Deterministic diff contract

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Addresses issue #3 point 5. It defines what a
diff compares, what it returns, in which order, and the exact bytes of the canonical
serialization. It does **not** define patch (`patch(A, diff(A, B)) == B` is P4: it
needs the semantics of applying operations, preconditions, conflicts and
authorization), the interpretation of a difference as drift or as an authorized
change (a separate integration contract), or inheritance resolution. Nothing here is
evidence that a Rust or Go implementation exists.

## What a diff is

```
diff(LogicalView A, LogicalView B, SchemaContext [, Scope]) -> DeterministicDiff
```

The result is **descriptive evidence of a difference**, from A to B. It is not an
instruction to execute, and a difference does not by itself mean drift or an
authorized modification.

A `LogicalView` is a document read under one schema, grammar and surface. It has three
parts:

- the **logical content**: the fully expanded form of ADR 0001 (every primitive
  position carries its value and its five declaration fields; short-form positions
  get the symbolic defaults), its lists keyed by the identity of ADR 0006, its sets
  unordered, its opaque values atomic. Equality of views, and so every event of
  `changes`, is defined on this part alone;
- the **authored form**: for each primitive position, `short` or `expanded`, the
  recognition class (ADR 0001) of the node as it was written. It is typed
  companion data, it takes no part in equality, and it is read only to produce
  `representation`. A view that was not built from an authored document has no
  authored form, and then no `representation` entry can be produced for it;
- the **conformance basis** (ADR 0010): `default`, `authored_claim` or `binding_attested`
  per position, derived from the trusted input context. It is typed companion data like
  the authored form: it takes no part in equality, it is never a `changes` event, it is
  not part of the canonical output, and the intent-observed adapter reads it from the
  views separately. An empty `changes` therefore means logical equality only.

Consequently none of these makes a difference: YAML mapping order, list order, flow
versus block style, quoting, short versus expanded writing of the same declaration.
The last of them is still reported, in `representation`, because the authored form is
kept. A difference of conformance basis alone is not reported at all.

`SchemaContext` is the schema identity and digest, the grammar commit and digest, and
the surface. Two views read under different contexts are never compared silently:
the result is `DIFF_CONTEXT_MISMATCH`. Comparing them needs an explicit compatibility
or mapping contract, which does not exist.

## Result

```
{"version":1,"changes":[ event... ],"representation":[ entry... ]}
```

`changes` holds the differences; `representation` holds writing differences that are
**not** differences. The views are identical exactly when `changes` is empty.

### Events

| Kind | Fields | Meaning |
|---|---|---|
| `added` | `path`, `next` | the node exists only in B |
| `removed` | `path`, `previous` | the node exists only in A |
| `value_change` | `path`, `previous`, `next` | the logical value of a scalar or opaque position differs |
| `policy_declaration_change` | `path`, `changed` | one or more of `access`, `modify`, `inherit`, `default_injection`, `conformance` differ; `changed` maps each such field to `{previous, next}` |
| `set_change` | `path`, `added`, `removed` | the elements of a set differ |

`path` is the ADR 0006 canonical path. Values are YAML-typed, so `Absent`, `null`,
`""`, `[]`, `{}`, `0` and `false` stay different: Absent to `null` is `added`, `null`
to Absent is `removed`, `0` to `false` is a `value_change`, and `0` or `false` to
Absent is `removed`, never "no value".

### Access: three kinds of difference

| Difference | Reported as |
|---|---|
| the logical value changed | `value_change` |
| an Access declaration field changed | `policy_declaration_change` |
| only short versus expanded writing changed | an entry of `representation` |

- Declaration fields are compared type-strictly (`inherit: false` is not `inherit: 0`;
  an empty `access` list, meaning nobody, is not the symbol `inherit`). A short
  position has the symbolic defaults, so a short node and an expanded node that spells
  the defaults have the same declaration.
- An explicit override written in the expanded form is a declaration change. It is
  never reported as a representation change, even when the form also changed: both
  are reported.
- A `policy_declaration_change` says that the declaration changed. It does **not** say
  that an effective permission changed: resolving inheritance is an open upstream
  decision (cic-primitives #17) and this ADR invents none.
- A `representation` entry is `{kind: representation_change, path, previous, next}`
  with `previous` and `next` in `short`, `expanded`. It is listed for a position
  present in both views whose recognition class (ADR 0001) differs, next to any
  event at the same path, and it is never a value difference.

### Structure

- **Lists** are paired by identity (ADR 0006), never by index. A matched pair is
  compared field by field; an element only in B is `added`, only in A `removed`, at
  the path of the element. A changed identity (any key component, in a composite key
  too, or a different `item_choice` case) is the removal of one identity and the
  addition of another. No rename is detected or invented.
- **Sets** have no addressable elements, so a set difference is one `set_change` on
  the set field, with the added and removed elements sorted (the order below; the gate
  checks it on the `set_change` lists, not on set values inside fragments, which would
  need the schema).
- **Opaque** values are atomic: one `value_change` with both whole values, no event
  for the interior. Map key order inside an opaque value does not matter; list order
  does.
- **No redundant events.** A node that appears or disappears is one `added` or
  `removed` event at its top; there is no event for its children or its parent. A
  child change gives no event for the parent.
- `added` and `removed` carry the **expanded logical fragment** of the node: for a
  position, a map with `value` and the five declaration fields; for a composite, the
  `value` is a map of the children's fragments; for a list, a list of element records
  (a map of field name to fragment) in identity order; for a set, the elements
  sorted. This keeps the evidence lossless, including non-default declarations inside
  an appearing subtree.
- Element order inside payloads: identities by declared component order, strings by
  UTF-8 bytes, integers numerically; set elements `null`, booleans, integers, strings
  in that rank, then by value. (Payload order of items from different `item_choice`
  cases is by case declaration order, then components; no vector covers it.)

## Scope: observed is not the same as absent

Absence of an observation is not absence of a value. `enabled = true` against an
unobserved `enabled` does not show that `enabled` is missing or different; it only
means the comparison cannot be decided from that observation.

The diff therefore never invents coverage from the structure of a document. A caller
that knows what was observed passes a **scope**: a list of canonical logical paths,
each a subtree that is comparable. Nothing outside the scope is compared and no event
is produced there, so a path that is not in B but also not in the scope is not a
removal. Inside the scope, absence is absence.

**The scope is applied before any merging of parent and child events.** Each scope
entry is compared on its own subtree, and a parent that appears or disappears is
reported only through the scope entries below it: with `scope: [$.ip.gateway]`, a
parent `ip` that exists only in B gives `added $.ip.gateway` and nothing else; there
is no `added $.ip`, and a sibling outside the scope (say `blob`) appears neither as an
event nor inside a fragment, so the output says nothing about it and leaks none of it.
An entry that is itself the appearing node (`scope: [$.ip]`) reports the whole node.
The existence of a parent whose subtree is only partly in scope is not reported.
Entries nested in another entry add nothing; a scope path must resolve against the schema (`UNKNOWN_FIELD` in phase
`path_resolve` otherwise). The coverage states of ADR 0003 (`observed`, `absent`,
`unobserved`, `unknown`) belong to the later intent-observed adapter, which turns them
into a scope. This ADR defines no coverage state and no drift.

## Order

`changes` are sorted by the segment-wise path order of ADR 0007, then by `kind`
bytewise (`policy_declaration_change` before `value_change` at one path). `representation`
is sorted by path. There is at most one event per `(path, kind)`. Inner maps are
sorted by key in the serialization. The order and the bytes do not depend on the
traversal order or the map type of an implementation.

## Errors

Reading a view can fail with the errors of the earlier phases (a duplicate element
identity is `PATH_DUPLICATE_IDENTITY` in phase `path_of`); nothing is compared then.
This ADR adds the phase **`diff`** to ADR 0007, with two codes:

- `DIFF_CONTEXT_MISMATCH` (stable): the contexts of the operands differ.
- `DIFF_UNSUPPORTED_VALUE` (provisional): a float, or an integer outside signed 64
  bits, occurs anywhere in an operand. It is refused whether or not it would take part
  in a difference, so the error does not depend on the data being compared, until a
  numeric contract exists.

It also adds the optional error field **`operand`** (`a` or `b`), present when the
error concerns one view: required for errors found while reading a view (phases
`recognize`, `validate`, `path_of`), allowed for `diff` errors about one operand,
forbidden otherwise. There is still one error per `diff` response.

## Canonical serialization

The bytes of a diff are fixed here and are not left to a JSON or YAML library:

- UTF-8 text, no BOM, no trailing newline, no whitespace outside strings.
- Objects: keys sorted by their UTF-8 bytes (so `changes`, `representation`,
  `version`), no duplicate keys.
- Strings: double-quoted; `\"` and `\\`; `\b \f \n \r \t`; other control characters
  below U+0020 as `\u00xx` in lowercase hex; everything else literal UTF-8 (no `\/`,
  no escaped non-ASCII, U+007F literal). This is the rule of ADR 0006 for key values.
- Integers: `0` or `-?[1-9][0-9]*`, within signed 64 bits. **No floats.** The gate
  rejects an integer outside `-9223372036854775808..9223372036854775807` and any float
  in the expectations and in the canonical string.
- `true`, `false`, `null`, arrays in the order defined above.
- Absence is expressed by the event kind and by omitting the field, never by a
  placeholder value.

Every positive corpus case carries the exact string (`expect.canonical`).

## Corpus and gate

New kinds `diff` and `diff_stability` (ADR 0005 amendment). `corpus/diff/vectors.yaml`
holds the cases of the acceptance frame (including partial scopes over appearing and
disappearing parents): same logical value written short and
expanded; value, declaration and representation changes separately and together
(including `false` versus `0`, `[]` versus `inherit`, an explicit override that is not
hidden); `Absent` versus `null`, `0` versus `false`, empty values; list reordering,
added and removed elements, a changed composite key, an `item_choice` identity change;
opaque atomicity; sets; appearing and disappearing parents as one event; scope; the
errors above; and four `diff_stability` cases whose operands differ in YAML mapping
order, list and set order, flow versus block style and key quoting but must give the
same events and bytes.

New gate rules, each with seeded faults: `DIFF_INPUT` (operands, scope, context,
stability variants that differ), `DIFF_EVENT` (event fields, typed values, an event
that describes no change, redundant events below an appearing node), `DIFF_ORDER`
(the order above, set elements included), `DIFF_CANONICAL` (the bytes parse as strict JSON without floats, are
the canonical writing, and say the same as the structured events). The gate checks
structure; it computes no diff, and a green run proves nothing about Rust or Go.

The expectations were derived by hand and cross-checked against a throwaway model
that is not committed (0 mismatches). Same single-author caveat as before: the P1
runners are the independent check.

## Not decided here

- Patch and its invariant (P4); the interpretation of a difference as drift (an
  integration contract); inheritance resolution.
- Floats, other `number` and `bytes` values, and integers beyond 64 bits.
- A context compatibility or mapping contract.
- Diffs through a plain `choice` (no real schema uses one).
- Whether a runner may stream events; only the result is fixed.
- An error response's canonical bytes (only the diff result is serialized here).

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits:

- `conformance_basis` is provenance companion data of the LogicalView, like the authored form. It is not a `changes` event and not part of logical equality; the intent-observed adapter reads it separately. **`changes == []` means logical equality only.** It asserts nothing about provenance, attestation or device capability equivalence.
- Patch and its invariant are P4; the interpretation of a difference as drift is a separate integration contract.
- Floats and integers beyond 64 bits are refused (`DIFF_UNSUPPORTED_VALUE`, provisional); the schema-dependent order of set values inside fragments and the payload order across `item_choice` cases are P1 obligations.
