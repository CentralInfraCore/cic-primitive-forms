# cic-primitive-forms

Schema-driven processing of CIC primitive documents, with two independent
implementations (Rust and Go) held to one shared conformance corpus.

> **Status: design phase.** No code yet. The scope and the phase plan are
> fixed in [`docs/PHASES.md`](docs/PHASES.md); the open contract decisions are
> collected in [`docs/adr/0000-p0-contract.md`](docs/adr/0000-p0-contract.md).

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
- validate short, mixed and expanded documents, never modifying the input (`validate`)
- convert between short and expanded form without losing authored meaning (`expand`, `collapse`)
- normalize: defaults and inheritance (`normalize`)
- structural and semantic diff, targeted patch (`diff`, `patch`)
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
structural check only; implementation conformance is not run until the P1 runners
exist. Format: [ADR 0005](docs/adr/0005-conformance-corpus-contract.md).
`make coverage` proves that the branch coverage gate fails when it must
([ADR 0009](docs/adr/0009-branch-coverage-measurement-contract.md)); no code is measured yet.

## What does not belong here

- Conformance evaluation, verdicts, canonical digests: that is
  [`cic-primitive-engine`](https://github.com/CentralInfraCore/cic-primitive-engine).
  This library may produce input for the engine; it must not become a second engine.
- Defining primitives or schemas: `cic-primitives` and `cic-schema-registry`.
- Git handling, authorization, execution: the Relay.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
