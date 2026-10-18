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

| Phase | Content | Exit criterion |
|---|---|---|
| P0 | Contract, minimal schema model, shared corpus, the open decisions | No basic YAML construct is interpreted differently by the two implementations |
| P1 | Semantic parse / emit / validate, resource limits, fuzzing | Stable on both languages with shared negative tests; validation changes no input byte |
| P2 | Recursive expand / collapse, symbolic defaults | Short, mixed and expanded forms round-trip; explicit policy overrides survive. **Architecture review stop.** |
| P3 | Normalize: defaults, `inherit: true/false/0` | Same input and schema give the same fully resolved result in both languages |
| P3b | Source-preserving editing (comments, style, anchors) | Edits keep untouched source bytes |
| P4 | Diff and patch | `patch(A, diff(A, B))` equals B for every representable change; conflicts are explicit errors |
| P5 | Resolve (from supplied snapshot), materialize, receipt | Complete, validated object with provable input/output binding |
| P6 | Engine / Relay integration | `intent -> materialized -> observed -> diff -> conformance` runs, library stays Git-free |
| P7 | Hardening and release | API and receipt versioning, final gates on the whole system |

Decisions fixed so far:

- `expand` fills symbolically (`access: inherit` stays); resolution is P3's job.
- Source-preserving editing is its own phase (P3b), before `patch`.
- The materializer adapter is a separate module; the core does not depend on the engine's types.
