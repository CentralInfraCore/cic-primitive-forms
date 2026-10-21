# P2: recursive expand and collapse

Status: **implemented in Rust and Go** (this PR), pending the architecture review that PHASES.md puts at the end of P2.
It follows the accepted ADR 0001 ("Round trip"), ADR 0003 (rules 5-6), ADR 0004 (symbolic `inherit`), ADR 0010 (the
conformance basis) and re-opens none of them.

## The invariant

> The form may change; the meaning the author declared, and where it came from, may not be lost.

`collapse(expand(authored))` gives back the authored document. Not as bytes (source preservation is P3b), as a value:
what was written short is short again, what was written expanded stays expanded, an explicit policy setting is never
dropped, `absent` stays absent and `null` stays `null`. The **origin** of each position is not recomputed from the
values: `expand` records it (`Provenance`) and `collapse` reads it back.

## What the code does

| Function | Rust | Go | What it does |
|---|---|---|---|
| one position, expand | `expand_position` | `ExpandPosition` | short -> the six keys with the symbolic defaults; expanded -> as authored; all of `A` plus a key -> `EXPANDED_WITH_EXTRA_KEY` |
| one position, collapse | `collapse_position` | `CollapsePosition` | the ADR 0001 law (below) |
| a document, expand | `expand` | `Expand` | every primitive position, recursively; returns the expanded document and the `Provenance` |
| a document, collapse | `collapse` | `Collapse` | the inverse, with the `Provenance` and an optional conformance basis per position |

**The symbolic defaults** are `access: inherit`, `modify: inherit`, `inherit` in the spelling of the **bound** access
grammar (`true` in grammar 1, the enum name in grammar 2: the caller binds the grammar, nothing is defaulted),
`default_injection: null`, `conformance: implemented`. `inherit` stays the symbol: resolving it is P3.

**Where it recurses** (ADR 0002): every field of a composite, and every field of a list element, with the element named by
its declared identity (ADR 0006). It does not recurse into a set, an opaque value or a scalar, and a list element is a
record, not a position. The recursion follows the schema, never the YAML shape.

**The law of collapse** (ADR 0001 + ADR 0010): a position becomes short only if all of these hold, and otherwise it stays
as it is (no explicit setting is ever dropped):

1. every policy field equals the symbolic default;
2. the position was not authored expanded (the provenance says, a position it does not name was not);
3. its conformance basis is not `binding_attested` (the caller gives it per position, by logical path);
4. the short form is recognized as short: the value is not a mapping holding all of `A`.

**Provenance** is a map from the canonical logical path of a position (`$.vlans[{vlan_id=100}].vlan_mode`) to whether it was
authored expanded. Keyed by the logical path it survives list reordering. Without it only the law decides, which is
deliberate and tested: a document that was never authored (a resolved object) collapses by the law alone.

## Boundaries

- Both expect a document that **validates**. A key of a composite the schema does not declare passes through untouched; a
  structure the schema does not allow (a non-record list element, an element without an identity, a non-mapping where a
  composite is declared) is `UNSUPPORTED_SYNTAX`, not a guess.
- The root of a document is a composite, not a position: it is never wrapped.
- Nothing is resolved, defaulted from a Role or normalized: that is P3. `default` of a field is not applied.
- The binding evidence itself (ADR 0010) is not produced here: `binding_attested` is an input.
- A position whose logical path cannot be written (an empty name, a control character) is `UNSUPPORTED_SYNTAX`: its
  provenance would have no key.

## Evidence

- **Corpus:** the 26 P2 cases (`collapse` 15, `roundtrip` 9, `expand` 1, `equality` 1) run in both languages, so the corpus
  accounting is 140 run and 207 not run.
- **Shared cases:** `testdata/p1/expand.json` (8 documents: all short, mixed with an explicit override, `null` and empty,
  an unknown field, `item_choice` elements named by identity, an expanded key value, an opaque value equal to `A`, `A`
  plus an extra key), each checked for the expanded text, the provenance, the round trip, the collapse without provenance
  and the `binding_attested` rule.
- **Properties** (`rapid` in Go, `proptest` in Rust): for every composite shape and every conforming document the round
  trip, the idempotence of `expand` and the provenance hold; an explicit policy setting is never lost, with or without
  provenance.
- **Fuzzing:** `FuzzExpandCollapse` (Go) and the `expand` target (Rust), with the canary proof of the other targets.
- **Seeded defects:** a dropped explicit setting, a shortened authored-expanded position, a shortened `binding_attested`
  position and a shortened value that would be read as expanded are all caught in both languages.
- **Mutation:** `cargo mutants` on `src/expand.rs`: 22 of 22 viable mutants caught.

## Questions for the architecture review (PHASES.md: the stop at the end of P2)

1. **Full expansion.** `expand` wraps *every* position, including nested ones, so an expanded document is verbose. Is that the
   intended normal form for P3, or should P3 expand on demand?
2. **Provenance as a side map** (keyed by logical path) against a marker inside the expanded node. A side map keeps the six
   keys of `A` exactly as ADR 0001 defines them; a marker would change `A`.
3. **The basis as a callback.** The conformance basis (ADR 0010) is a property of the position that only the caller knows. A
   function by path keeps this library free of the binding. Is that the right seam, or should the basis travel in the
   provenance?
4. **Undeclared keys pass through.** The alternative is to refuse them; validation already reports them as `UNKNOWN_FIELD`.
5. **List element identity as the provenance key.** If an element's key value is itself changed by an override, its
   provenance path changes: acceptable, because the identity is the meaning.
