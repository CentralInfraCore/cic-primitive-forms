# P2: recursive expand and collapse

Status: **implemented in Rust and Go**; the architecture review that PHASES.md puts at the end of P2 is done (see "Review decisions"), subject to the two contract corrections made here.
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
| a document, expand | `expand` | `Expand` | every primitive position, recursively; returns the expanded document and the `Provenance`; checks the trusted `Context` |
| a document, collapse | `collapse` | `Collapse` | the inverse, with the `Provenance` and the trusted `Context` (ADR 0010) |

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
3. its conformance basis is not `binding_attested` (derived from the trusted `Context`, below);
4. the short form is recognized as short: the value is not a mapping holding all of `A`.

**Provenance** is a map from the canonical logical path of a position (`$.vlans[{vlan_id=100}].vlan_mode`) to whether it was
authored expanded. Keyed by the logical path it survives list reordering. Without it only the law decides, which is
deliberate and tested: a document that was never authored (a resolved object) collapses by the law alone.

## The trusted context (ADR 0010)

The basis of a position follows the **context the caller supplies**, never the writing: `Context{origin, attestation,
attested_positions}`. The library does not verify the attestation (that is the Relay's, P5/P6); it only stops the
representation change from losing the authentication context.

| Document | Context | Result |
|---|---|---|
| authored | none (the zero `Context`), or `origin: authored` alone | allowed; the absence of a basis is **not** an attestation |
| materialized | `attestation` **and** `attested_positions` (which may be empty: an attestation that grants nothing) | positions in the set are `binding_attested` and never shortened; all others follow the unchanged rule |
| anything else | a materialized document without its attestation or its positions; an authored one with either; an unknown origin; a repeated position; the root or a list element named as a position | `ORIGIN_ATTESTATION_MISMATCH` (phase `validate`, source location `not_applicable`, no logical path); **nothing is expanded or collapsed** |
| an entry that is not a position of the schema | a text that is not a canonical path (`PATH_SYNTAX`, `PATH_INDEX_SELECTOR`, ...), or that the schema does not resolve (`UNKNOWN_FIELD`, `PATH_NO_CHILDREN`, `PATH_SELECTOR_MISMATCH`, ...) | that path error, phase `path_parse` or `path_resolve`, source location `not_applicable`, and for `path_parse` its `path_offset` (present, `0` included; absent for every other phase); **nothing is expanded or collapsed** |

**The attested positions are resolved against the schema** (ADR 0010): each entry is parsed as a canonical logical path and
resolved with `resolve_path`; it must name a primitive position. A binding is therefore never credited with a position that
does not exist, and nothing is `binding_attested` merely because it appears in a list of strings.

There is no callback and no default string: a materialized document cannot be shortened without saying which positions the
binding attested. `expand` checks the same context (it creates no authority, so it does not change the result).

## Boundaries

- Both expect a document that **validates** and **fail closed** otherwise. A key the schema does not declare is
  `UNKNOWN_FIELD` (the first in document order, with its logical path and the position of the key): nothing validated or
  normalized can be made of an undeclared key. The raw document remains the caller's own input; a lossless pass-through
  would be a separate, explicitly unvalidated mode whose result must not be given the validated status. A structure the schema
  does not allow (a non-record list element, an element without an identity, a non-mapping where a composite is declared) is
  `UNSUPPORTED_SYNTAX`, not a guess.
- The root of a document is a composite, not a position: it is never wrapped.
- Nothing is resolved, defaulted from a Role or normalized here: that is P3, and it has since been added on top of this phase without changing it (`apply_defaults` and `normalize`, `docs/P3-UNIT3-COMPOSITION.md`). `expand` and `collapse` themselves do not apply a `default`.
- The binding evidence itself (ADR 0010) is an input: not produced, not verified here.
- A position whose logical path cannot be written (an empty name, a control character) is `UNSUPPORTED_SYNTAX`: its
  provenance would have no key.
- An entry of `attested_positions` that is a valid position of the schema but that this particular document does not contain
  (an optional field left out) attests nothing; the check is against the schema, not against the document.

## Evidence

- **Corpus:** the 26 P2 cases (`collapse` 15, `roundtrip` 9, `expand` 1, `equality` 1) run in both languages, so the corpus
  accounting is 240 run and 107 not run (the same PR also runs the 100 path-text cases, which the attested positions need).
- **Shared cases:** `testdata/p1/expand.json` (9 documents and 12 context cases: all short, mixed with an explicit override, `null` and empty,
  an unknown field, `item_choice` elements named by identity, an expanded key value, an opaque value equal to `A`, `A`
  plus an extra key), each checked for the expanded text, the provenance, the round trip, the collapse without provenance
  and the `binding_attested` rule.
- **Properties** (`rapid` in Go, `proptest` in Rust): for every composite shape and every conforming document the round
  trip, the idempotence of `expand` and the provenance hold; an explicit policy setting is never lost, with or without
  provenance.
- **Fuzzing:** `FuzzExpandCollapse` (Go) and the `expand` target (Rust), with the canary proof of the other targets.
- **Seeded defects:** a dropped explicit setting, a shortened authored-expanded position, a shortened `binding_attested`
  position, a shortened value that would be read as expanded, an undeclared key passed through, a contradictory context
  collapsed anyway and a position outside the attested set treated as attested are all caught in both languages.
- **Mutation:** `cargo mutants` on `src/expand.rs`: 22 of 22 viable mutants caught.

## Review decisions (the stop at the end of P2)

| Question | Decision | Why |
|---|---|---|
| 1. Full expansion | Complete, recursive expansion is the normal input of P3; the authored form stays alongside | one representation is easier to normalize deterministically |
| 2. Where provenance lives | A separate side map | the six keys of the Access atom stay as defined; metadata is not mixed into the primitive's value |
| 3. The conformance basis | A separate, typed trusted context, not a callback | the origin of an authored form and the origin of a credible device capability are different things |
| 4. Undeclared keys | Refused by default in the schema-bound path | an undeclared authored key cannot become a checked, normalizable intent |
| 5. A list element's key changes | No silent provenance re-binding | a changed key is a change of identity, not a reordering |

Two corrections to this PR came out of the review and are done: undeclared keys fail closed (`UNKNOWN_FIELD`) in `expand`
and `collapse`, and the conformance basis comes from the typed trusted `Context`, with a structured error for a missing or
contradictory one. Both have shared negative cases in Rust and Go (`testdata/p1/expand.json`) and seeded defects.

**Consequence for P3 (a rule, not an implementation).** Resolving defaults and inheritance must never change the declared key
of a list element. `$.vlans[{vlan_id=100}]` becoming `$.vlans[{vlan_id=200}]` is not a value normalization: it is an identity
change, which the later diff/patch layer must treat explicitly, keeping both the original and the target identity. A
reordering stays harmless, because provenance is bound to the logical identifier and not to a list index.

**Three separate notions** that must stay separate: the *authored provenance* (how a position was written, this PR), the
*resolution origin* (where a resolved value came from, P3) and the *authenticated binding basis* (what the trusted context
attests, ADR 0010). None of them is derived from another.
