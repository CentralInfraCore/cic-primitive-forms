# ADR 0010: Origin of `conformance` and the authority boundary

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Addresses ADR 0000 open point 9. It decides who
may state a device capability in a document and what the library preserves about it.
It does **not** change the six-key Access grammar (`value`, `access`, `modify`,
`inherit`, `default_injection`, `conformance`), and it does **not** prove that a
binding is authentic or enforce anything: verifying an attestation and rejecting writes
to a `not_implemented` field are the Relay's work (P5/P6). The library preserves the
value, the origin, the position-level provenance and the validation result.

## Principle

The value of `conformance` is a statement about a device, not about a document. Only a
trusted binding can make that statement credible. A user-authored document cannot
produce credible device capability, and **a representation produced by the library, a
compiler or a materializer is never the source of its own authority**: changing how a
value is written (expanding, collapsing, re-reading) must not change its evidential
force. `conformance: implemented` by itself proves nothing; it is also the symbolic
default that expansion fills in.

## The rule

| Input | Treatment of `conformance` |
|---|---|
| authored short form | expansion gives `implemented`, as a default; **not** capability evidence |
| authored long form, `implemented` | syntactically valid, an **unauthenticated claim** |
| authored long form, `not_implemented` or `deprecated` | validation error `CONFORMANCE_NOT_AUTHORABLE`: the author may not declare a device capability |
| materialized, position attested by the binding | all three enum values allowed; the value is binding data |
| materialized, position **not** attested | like a default: only `implemented`; any other value is `CONFORMANCE_NOT_AUTHORABLE` |
| no credible binding data | the capability is **unverified**; never taken to be `implemented` automatically |

Any value outside the closed enum `implemented | not_implemented | deprecated` is
`INVALID_CONFORMANCE_VALUE`, whatever the origin and even at an attested position; the
check is type-strict (`true`, `null`, `Implemented` are not enum values).

## Origin is a trusted context, not a YAML key

There is no seventh key and no marker in the document. A document is processed with a
**context** supplied by the caller, as the schema and the surface are:

| Context field | Meaning |
|---|---|
| `origin` | `authored` (the default) or `materialized` |
| `attestation` | an opaque reference to the binding data (source and authorization) |
| `attested_positions` | the logical positions (ADR 0006 paths, exact, no subtrees) for which the binding declared `conformance`; may be empty |

`attestation` and `attested_positions` are present **exactly when** the origin is
`materialized`; otherwise the context is contradictory and the result is
`ORIGIN_ATTESTATION_MISMATCH`. An attestation without positions grants nothing; a
position set without an attestation grants nothing. The set is the checked, position-level
provenance carried by the trusted context; a separate signature per field is not
required, a document-level attestation that covers a set of positions is enough. The
entries are resolved against the schema like the scope entries of ADR 0008 (an entry
that names no position fails with the path errors). The library never guesses the
origin or the positions from the content, and it keeps the attestation reference with
the result without interpreting or verifying it.

## The conformance basis follows the context, never the writing

For each position the result carries a typed companion value, `conformance_basis`:

| Basis | When |
|---|---|
| `default` | nothing states the capability: a short form of an authored document, and in a materialized document every position that is **not** in `attested_positions` (whether written short or long) |
| `authored_claim` | an authored long form with `implemented` |
| `binding_attested` | a position of a materialized document that is in `attested_positions` (whether written short or long) |

Only `binding_attested` establishes a capability. The basis is derived from the origin
and the attested positions, **not** from whether the value is written short or long.
Consequences:

- **Expansion creates no authority.** A materialized short `mtu: 1500` that the binding
  did not attest has the basis `default`. After expansion it is a long form with
  `conformance: implemented` (a symbolic default), and read again under the same
  context its basis is still `default`: the position is still not in
  `attested_positions`. Expansion changes the writing, not the evidence.
- **The same six fields can differ in evidence.** Two documents with identical long
  forms have different bases when one position is attested and the other is not
  (`long-form-from-the-binding` against `identical-long-form-from-the-compiler-default`).
- **A binding datum written short is still binding data**: the basis of an attested
  position does not depend on the form.
- The basis is companion data like the authored form of ADR 0008: not a seventh key, not
  part of the logical value. It is kept through expand, collapse and re-reading, and
  recognition (ADR 0001) does not look at it.

## Consequences for the other contracts

- **Collapse (ADR 0001).** The collapse law is unchanged, with one added input, the
  basis of the position: a position whose basis is `binding_attested` is never
  shortened, even when all six fields equal the defaults; shortening would turn binding
  data into a default. A `default` basis follows the unchanged rule; an authored long
  form stays long through the existing authored-expanded flag.
- **Diff (ADR 0008).** The diff compares the logical value, and `conformance` is one of
  the declaration fields it compares. A difference of basis alone is **not** a diff
  event, so an empty `changes` is never evidence that two views have the same
  capability. Decided at the P0 acceptance review: the basis is provenance
  companion data of the LogicalView and is neither a diff event nor part of the diff's
  canonical output; the later intent-observed adapter reads it from the views, separately.
- **Effective behaviour.** Rejecting a write to a `not_implemented` field and warning on
  `deprecated` are the Relay's.

## Errors

Three codes join the ADR 0007 registry, all in phase `validate`:

| Code | Anchor | Meaning |
|---|---|---|
| `INVALID_CONFORMANCE_VALUE` | the value | outside the closed enum |
| `CONFORMANCE_NOT_AUTHORABLE` | the value | a capability other than `implemented` where no trusted binding datum supports it: authored, or materialized at an unattested position |
| `ORIGIN_ATTESTATION_MISMATCH` | none | the context contradicts itself; no YAML node is at fault, so the source location is `not_applicable` even when text was given |

## Corpus and gate

`corpus/conformance/vectors.yaml` has 36 cases: single-node cases (enum and type
violations, the authored rows, positions in source text, the context errors), a new kind
`basis` (a document under a tree, the basis per position, `via: expand` to read the
document again after expansion), and collapse cases that take the position's basis as an
input. Every case about the reviewed scenario is present: the materialized short form
that stays `default` before and after expansion, the explicit binding datum, and the
identical six fields with different evidence. Expectations of the `basis` kind were
checked against a throwaway model of these rules that is not committed (0 mismatches);
same single-author caveat as before.

Structural rules, each with seeded faults: `ORIGIN_CONTEXT` (origin, attestation and
attested positions agree, positions are distinct canonical paths, and a context error
is expected exactly when they do not) and `BASIS` (the closed set, canonical position
paths in `bases`). A code whose registry anchor is `none` takes a `not_applicable`
source location. The gate checks structure only and decides nothing about authority.

## Not decided here

- Verification of the attestation, and what makes a binding trusted (P5/P6, Relay).
- Subtree entries in `attested_positions`, and per-field signatures.
- Merging a materialized document back into an authored one.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits:

- **P5/P6 boundary.** `binding_attested` means provenance asserted by the trusted input context. It does not mean that the library verified the evidence cryptographically; verifying an attestation and enforcing writes to a `not_implemented` field are the Relay's work.
- The basis is not a diff event (ADR 0008): an empty `changes` is no evidence of equal capability.
- Subtree entries in `attested_positions`, per-field signatures and merging a materialized document back into an authored one are not decided.
