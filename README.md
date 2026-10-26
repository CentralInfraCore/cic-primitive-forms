# cic-primitive-forms

Schema-driven processing of CIC primitive documents, with two independent
implementations (Rust and Go) held to one shared conformance corpus.

> **Status: P0, P1, P2 and P3 are done; P3b (source-preserving editing) has its design, its accepted ADR 0012 and 72 vectors (step E1); `source_span`, the read-only span of a scalar (step E2), and `edit_scalar`, the replacement of one scalar that leaves every other byte alone (step E3), are implemented in Rust and Go, with a proof of the six guarantees of the edit (step E4). The first unit of P3b is closed; its later units (insertion, deletion, block and flow scalars, a batch of edits) are open.** The scope and the phase plan are fixed in
> [`docs/PHASES.md`](docs/PHASES.md) (with the state of each phase); the contract decisions are indexed in
> [`docs/adr/0000-p0-contract.md`](docs/adr/0000-p0-contract.md). What is implemented in Rust (`rust/`) and Go (`go/`) and what
> is not: [`docs/P1-SUPPORTED-SUBSET.md`](docs/P1-SUPPORTED-SUBSET.md). In short: parse, emit, recognize, schema-bound validation
> (a pinned schema, list elements by identity, paths); `expand` / `collapse`; from P3 the schema's policy and `identity.base`, the
> schema defaults (ADR 0011), `normalize` / `denormalize` with the origin of every value kept, and `PolicyResolution` (the effective
> access and modify rule of every position, as data; `docs/P3-UNIT4-POLICY-RESOLUTION.md`), also in one bound call (`normalize_with_policy`). The evidence of P3
> is `docs/P3-EXIT-CRITERIA.md`. **Not started:** the later units of P3b (insertion, deletion, block and flow scalars, a batch of edits), the later units of patch (the `diff` exists since P4 D1-D3 and the first unit of `patch` since P4-2, see `docs/P4-DIFF-PATCH.md`), reference resolution and materialization.

## What it does

A CIC primitive value has two equivalent spellings (see the `Access` atom in
`cic-primitives`):

```yaml
cpu_cores: 4                 # short form
cpu_cores:                   # expanded form
  value: 4
  access: inherit
  modify: inherit
  inherit: true
  default_injection: null
  conformance: implemented
```

This library works on both, using the schema as the only authority on what a
primitive is:

- parse and emit YAML (`parse`, `emit`)
- validate short documents against a pinned schema, never modifying the input (`validate`, `compile_schema`) - **implemented**
- convert between short and expanded form without losing authored meaning (`expand`, `collapse`) - **implemented**
- normalize: the declared defaults of absent fields, with the origin (authored or default) of every value kept, and the way back
  (`apply_defaults`, `normalize`, `renormalize`, `denormalize`) - **implemented**; the effective access and modify rule of every position
  (`PolicyResolution`, `resolve_rule`) - **implemented**, cross-checked against the 86 pinned reference vectors
- structural and semantic diff (`diff`, ADR 0008) - **implemented** in both languages, all 66 corpus vectors run, plus a Rust/Go/model differential, planted defects and fuzzing (P4 D3b); targeted patch (`patch`, ADR 0013, the first unit: `value_change` on existing scalars) - **implemented** in both languages, its 60 vectors run (its proof pass P4-3 is open); other events - later
- later: resolve references from a supplied snapshot, and materialize for the engine

## Principles

- **The schema defines meaning.** The library never invents what a primitive
  is; it interprets, checks, converts and compares.
- **Authored intent is never guessed back from values.** An explicitly written
  value equal to the default differs from an omitted one. The original authored
  document is an input; conversion back uses it.
- **A pure library.** No Git, no network, no filesystem policy, no execution
  and no authorization decisions. Callers hand in values (authored document,
  schema, snapshots) and receive a result or a structured error. Git custody
  (intent and state branches, commits, signing) belongs to the CIC Relay.
- **Two implementations, one corpus.** The shared test vectors are normative;
  neither implementation is the reference for the other.
- **Quality gates above 90%.** Line, branch and mutation score are each
  required to exceed 90% in both languages, from the first code phase on.
- **A rejected or ambiguous input is an explicit error**, never silently
  reinterpreted.

## Corpus check

`make corpus` (needs `pip install -r tools/requirements.txt`) checks the structure of
every fixture and proves with seeded faults that the check can fail. It is a
structural check only. Implementation conformance is run by the P1 runners
(`rust/tests/corpus.rs`, `go/corpus_test.go`, workflow `p1`), for the kinds listed in
`docs/P1-SUPPORTED-SUBSET.md`. Format: [ADR 0005](docs/adr/0005-conformance-corpus-contract.md).
`make grammar-selftest` and the CI step that reproduces the pinned grammar identity from the
upstream commit are described in [ADR 0002](docs/adr/0002-minimal-schema-contract.md).
`make coverage` proves that the branch coverage gate fails when it must
([ADR 0009](docs/adr/0009-branch-coverage-measurement-contract.md)); the product code is measured by workflow `p1`.

## What does not belong here

- Conformance evaluation, verdicts, canonical digests: that is
  [`cic-primitive-engine`](https://github.com/CentralInfraCore/cic-primitive-engine).
  This library may produce input for the engine; it must not become a second engine.
- Defining primitives or schemas: `cic-primitives` and `cic-schema-registry`.
- Git handling, authorization, execution: the Relay.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
