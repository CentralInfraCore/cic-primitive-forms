# ADR 0014: Materialize and receipt - the first units

Status: **proposed** (2026-10-30). It is step P5-0 of `docs/P5-RESOLVE-MATERIALIZE-RECEIPT.md`, which holds the reasoning, the places where the contract and this library do not meet, and the
questions that are the owner's. It re-opens none of ADR 0001-0013, changes none of their APIs, and changes nothing in `cic-primitive-engine`. Nothing here is evidence that a producer exists.

## Context

The sibling repository `cic-primitive-engine` has decided the object that crosses into a module (`MaterializedObject`), its canonical bytes and digests (section A of its
`docs/MATERIALIZATION-SPEC.md`), and the materialization receipt (`docs/RECEIPT-SCHEMA.md`, 15 required fields), and it has a verifier of the receipt in Rust and Go with a shared
corpus. It states that it never builds `Parse`, `Normalize`, `Resolve` or `Validate` ("permanently the environment's job"), and that no receipt producer exists anywhere in the ecosystem.
This library is that environment for parse, validate and normalize (P1-P3), and keeps the origin of every value (`value_origins`). The row P5 of `PHASES.md` has no contract of its own.

## Decision

### 1. The role

This library is the **producer** of the candidate materialized object and of the receipt. The engine's contract, its canonical form and its verifier are the specification and the second reader;
this ADR defines no second contract and no second byte form. Where the engine's contract is silent, this library asks the engine side and does not fill the silence.

### 2. The order of work

P5-1 the canonical byte form and the digest of a processed value (engine section A, A1-A7), in Rust and Go; P5-2 `materialize` of a document that has no reference, with its evidence;
P5-3 the receipt; P5-4 Resolve from a supplied snapshot. Each unit is a pull request with its own proof (section 4). The first units are P5-1 to P5-3.

### 3. What the first units do not do

They resolve no reference and define no snapshot; compute no `derived` value (`derived_values` is `[]`: no derivation rule exists in this library); sign nothing (a signature, if ever wanted,
is a separate envelope, as the engine's schema decides); verify no attestation (ADR 0010: the caller's); and decide no request.

### 4. The proof

* The receipt is accepted by the **engine's own verifier**, run in this repository's CI against a pinned engine commit, and the tamper cases (a changed input or output digest, an entry out of order or
  repeated, a wrong version, a missing field) are seen to be refused. The canonical bytes and digests reproduce the engine's `canonicalize` and `digest` vectors byte for byte.
* A Rust/Go differential on generated documents with a self-test that breaks it; planted defects; fuzz targets on the new inputs; line, branch and mutation above 90% in both languages.
* A difference between the two languages, or between this library and the engine, is decided before it is fixed (which side is wrong, or the contract is unclear). It is never put on a list of known differences.

### 5. Identity of the producer

`engine_identity` and `engine_version` of a receipt name the implementation that materialized the data: this library, one identity per language. Two correct receipts of one input therefore
differ in these two fields and in nothing else.

## Not decided here

The `PathBinding` between a receipt path and an object key, and how a position of a tree (a composite member, a list element) becomes a key; the accepted path form of a list element in a receipt;
the mapping from this library's grammar pin (a manifest of ten files) to the engine's `grammar_*` and `primitive_*` identifiers; what a snapshot is and how a reference is written (P5-4);
derivation rules; coverage (observation side); Unicode normalization of strings (a named gap of the engine's A4). The proposals for the first three are in section 8 of the design note.

## Consequences

* This library gains a dependency on a pinned commit of `cic-primitive-engine` for its CI, as it has one on `cic-primitives`; a change of the engine's section A moves digests here and is a migration.
* The proof of P5 is stronger than a proof against this library's own reading of the contract, and slower to obtain: it needs a harness around the engine's verifier, which the engine does not offer as a command.
* If the mapping of a tree to flat keys has no faithful answer for list elements, that is a finding about the contract, taken back to the engine side.

## Acceptance record

Proposed. Accepted when the owner merges it, with the decisions A-F of section 8 of the design note answered or deferred in the record.
