# Phases

Each phase ends with a capability that works identically in Rust and Go and is
closed by tests. Exit criteria matter; calendar time does not.
Within a phase the shared corpus leads, Rust follows, Go closes in the same phase.

## Quality gates

Wired into CI from P1, enforced in both languages, and they only ever tighten.
Every threshold is strictly greater than 90% and applies to all code, not only
a "critical" subset:

| Gate | Requirement |
|---|---|
| Line coverage (unit) | > 90% |
| Branch coverage | > 90% |
| Mutation score | > 90% |
| Rust / Go parity on the shared corpus | 100% of accepted vectors |
| Property tests | required for parse/emit, expand/collapse, normalize, diff/patch |
| Fuzzing | parser, expand/collapse, normalize, diff/patch; from P1 |

The branch-coverage row is governed by ADR 0009: one definition (`cic-branch/1`), one
threshold, one report format and a gate that fails closed. The measurement tools are
selected (`gobranch` and `rustbranch`, `coverage/tools.yaml`) and were qualified on
hand-counted samples. The gate on **product code** is mandatory in CI from P1, together
with the first Rust and Go implementation, and a missing measurement must then fail.

A gate that cannot fail proves nothing: each gate is accompanied by a seeded
fault or vector that demonstrably turns it red.

## Phases

| Phase | Content | Exit criterion | State |
|---|---|---|---|
| P0 | Contract, minimal schema model, shared corpus, the open decisions | No basic YAML construct is interpreted differently by the two implementations | done (ADRs accepted) |
| P1 | Semantic parse / emit / validate, resource limits, fuzzing | Stable on both languages with shared negative tests; validation changes no input byte | done: parse, recognize, emit, access-policy reading, schema-bound validation, list-element identity, path text |
| P2 | Recursive expand / collapse, symbolic defaults | Short, mixed and expanded forms round-trip; explicit policy overrides survive. **Architecture review stop.** | done and reviewed; post-merge evidence complete |
| P3 | Normalize: defaults, `inherit: true/false/0` | Same input and schema give the same fully resolved result in both languages | done: the four units (#44, #45, #49, #53) and the closing round (#55, #56, #57) are merged; `docs/P3-EXIT-CRITERIA.md` has the evidence, among it a full run of the whole gate on the merged main |
| P3b | Source-preserving editing (comments, style, anchors) | Edits keep untouched source bytes | **first unit closed (E1-E4), later units open.** Done and merged: the design (`docs/P3B-SOURCE-PRESERVING-EDIT.md`, #65), ADR 0012 (accepted, #67), the 72 hand-written vectors (#68), the read-only scanner and span (`source_span`, #72), the edit (`edit_scalar`, in Rust and Go, #76; the 44 `edit` vectors run, compared as bytes) and its proof (#79: a Rust-against-Go differential that checks each of the six guarantees on its own, a self-test that breaks it in 22 ways, 20 planted defects that turn it red, and 10 seeded defects of the internal assertion `EDIT_SPAN_MISMATCH`). The unit replaces **one existing, single-line scalar**. **Still outside the supported subset, and not started:** insertion of a key, deletion, renaming, reordering, adding a list element, changing a short position to expanded, block scalars, flow collections, multi-line scalars, anchors, aliases and tags (the parser refuses them), a batch of edits, writing to disk. P3b as a whole is therefore not closed; each of these is a later note and ADR |
| P4 | Diff and patch | `patch(A, diff(A, B))` equals B for every representable change; conflicts are explicit errors | design, vectors and the diff (`docs/P4-DIFF-PATCH.md`; ADR 0013 accepted; P4-1: 60 hand-written `patch` vectors that run in neither language): the diff contract is ADR 0008 (accepted), `diff` is implemented in Rust and Go (D1-D3), its 66 hand-derived vectors run in both, and a Rust/Go/model differential, 32 planted defects and fuzz targets prove it (D3b); `patch` (the first unit: `value_change` on existing scalars through `edit_scalar`) is implemented in Rust and Go (P4-2) and its 60 hand-written vectors run in both; a Rust/Go/model differential on generated documents, 24 self-test breaks, 14 planted defects and fuzz targets prove it (P4-3); the first patch unit is `value_change` on existing scalars through `edit_scalar`, with the invariant "the diff against B is empty within the context and scope of the diff, and only the changed scalars move" |
| P5 | Resolve (from supplied snapshot), materialize, receipt | Complete, validated object with provable input/output binding | not started |
| P6 | Engine / Relay integration | `intent -> materialized -> observed -> diff -> conformance` runs, library stays Git-free | not started |
| P7 | Hardening and release | API and receipt versioning, final gates on the whole system | not started |

Decisions fixed so far:

- `expand` fills symbolically (`access: inherit` stays); resolution is P3's job.
- Source-preserving editing is its own phase (P3b), before `patch`.
- The materializer adapter is a separate module; the core does not depend on the engine's types.
