# ADR 0010: Origin of `conformance` and the authority boundary (draft)

Status: **proposed, not approved.** Addresses ADR 0000 open point 9. It decides who
may state a device capability in a document and what the library preserves about it.
It does **not** change the six-key Access grammar (`value`, `access`, `modify`,
`inherit`, `default_injection`, `conformance`), and it does **not** prove that a
binding is authentic or enforce anything: verifying a binding and rejecting writes to
a `not_implemented` field are the Relay's work (P5/P6). The library preserves the
value, the origin and the validation result.

## Principle

The value of `conformance` is a statement about a device, not about a document. Only a
trusted binding can make that statement credible; a user-authored document cannot
produce credible device capability. And `conformance: implemented` by itself proves
nothing: it is also the default that expansion fills in.

## The rule

| Input | Treatment of `conformance` |
|---|---|
| authored short form | expansion gives `implemented`, as a default; it is **not** capability evidence |
| authored long form, `implemented` | syntactically valid, an **unauthenticated claim** |
| authored long form, `not_implemented` or `deprecated` | validation error `CONFORMANCE_NOT_AUTHORABLE`: the author may not declare a device capability |
| trusted binding / materialization | all three enum values are allowed, with the source and the authorization attested |
| no credible binding data | the capability is **unverified**; it is never taken to be `implemented` automatically |

Any value outside the closed enum `implemented | not_implemented | deprecated` is
`INVALID_CONFORMANCE_VALUE`, whatever the origin; the check is type-strict, so `true`,
`null` and `Implemented` are not enum values.

## Origin is a trusted context, not a YAML key

There is no seventh key and no marker in the document. A document is processed with a
**context** supplied by the caller, as the schema and the surface are:

- `origin`: `authored` (the default) or `materialized`;
- `attestation`: an opaque reference to the binding data (source and authorization),
  present **exactly when** the origin is `materialized`.

The library never guesses the origin from the content. The same YAML in the two
contexts gives different results (the pair `authored-not-implemented-is-refused` and
`materialized-not-implemented-is-allowed` has identical input). An inconsistent
context, a materialized document without an attestation or an authored one with an
attestation, is `ORIGIN_ATTESTATION_MISMATCH`; a missing attestation therefore never
yields a quietly credible capability. The library keeps the attestation reference with
the result and does not interpret or verify it.

## What the library reports: the conformance basis

For each position the result carries a typed companion value, `conformance_basis`:

| Basis | When |
|---|---|
| `default` | a short-form position, in either origin: nothing states the capability |
| `authored_claim` | an authored long form with `implemented` |
| `binding_attested` | a long-form position of a `materialized` document |

Only `binding_attested` establishes a capability. In a materialized document a
position that the binding said nothing about stays a short form and has the basis
`default`; a long form there is a binding datum.

The basis is companion data, like the authored form of ADR 0008: it is not a seventh
key, not part of the logical value, and the YAML recognition of ADR 0001 does not look
at it (recognition does not depend on origin).

## Consequences for the other contracts

- **Collapse (ADR 0001).** The collapse law is unchanged for authored documents. For a
  materialized document a long position whose basis is `binding_attested` is never
  shortened, even when all six fields equal the defaults: shortening would turn
  attested data into a default.
- **Diff (ADR 0008).** The diff compares the logical value; `conformance` is one of the
  declaration fields it compares. A difference of basis alone is **not** a diff event.
  So an empty `changes` is never evidence that two views have the same capability.
  Whether the basis becomes part of the diff output is not decided here; the later
  intent-observed adapter reads the basis from the views.
- **Anything else.** Mapping a capability onto effective write behaviour (`not_implemented`
  rejects, `deprecated` warns) is the Relay's.

## Errors

Three codes join the ADR 0007 registry, all in phase `validate`:

| Code | Anchor | Meaning |
|---|---|---|
| `INVALID_CONFORMANCE_VALUE` | the value | outside the closed enum |
| `CONFORMANCE_NOT_AUTHORABLE` | the value | authored `not_implemented` or `deprecated` |
| `ORIGIN_ATTESTATION_MISMATCH` | none | the context contradicts itself; no YAML node is at fault, so the source location is `not_applicable` even when text was given |

## Corpus and gate

`corpus/conformance/vectors.yaml` has 22 cases: the authored and materialized rows of
the rule above (with identical-input pairs), values outside the enum and of the wrong
type under both origins, the two context errors, positions in source text, symbolic
expansion (default, not evidence), and collapse of attested data. Cases may carry
`origin` and `attestation`. New structural rules, with seeded faults: `ORIGIN_CONTEXT`
(origin and attestation agree, and a context error is expected exactly when they do
not) and `BASIS` (the closed set). A code whose registry anchor is `none` takes a
`not_applicable` source location. The gate checks structure only; it decides nothing
about authority.

## Not decided here

- Verification of the attestation, and what makes a binding trusted (P5/P6, Relay).
- Per-position or per-field attestations; one reference covers a document.
- The basis in the diff output.
- Merging a materialized document back into an authored one.
