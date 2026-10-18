# ADR 0003: presence and value semantics

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Resolves issue #3 point 3. It does not decide the type of
the symbolic `inherit` (point 8), the origin of `conformance` (point 9), or how
defaults are applied (P3); it fixes what "absent", "null" and "empty" are, which
those later decisions depend on.

## Sources

| Source | Used for |
|---|---|
| `cic-primitives` `schemas/atomic/shape.yaml` | the presence axis (`mandatory` / `optional`), `nullable`, `default` |
| `cic-primitive-engine` `docs/BOUNDARY.md` | defaults follow Role; `missing`, `unknown`, `not_observed`, `not_implemented` and a defaulted value are five different statements |
| `cic-primitive-engine` `docs/MATERIALIZATION-SPEC.md` B1, B2 | three orthogonal axes; the representation of authored-absent versus an authored literal `null` is still open on the engine side |
| `cic-primitives` `schemas/atomic/access.yaml` | `value` and `default_injection` are separate keys |

## States

A field in an authored document is in exactly one state:

| YAML | State |
|---|---|
| key missing | **Absent** |
| `alma: null` | Present, value `null` |
| `alma:` (no value) | Present, value `null` (YAML 1.2 reads an empty node as null) |
| `alma: []` | Present, empty list |
| `alma: {}` | Present, empty map |
| `alma: ""` | Present, empty string |
| `alma: false`, `alma: 0` | Present, ordinary values |

"Empty" is not a separate state. `""`, `[]` and `{}` are values of the Shape
type; their validity is decided by that type and its contracts (for example a
list with `min_items: 1` rejects `[]`).

## Rules

1. **Absent is not `null`.** Absent is the fact that the key is not there; it is
   not a YAML value and gets no marker in the document. The library's object
   model keeps the two apart: a mapping without the key is different from a
   mapping whose key holds `Null`.
2. **The Shape decides validity.** Validation uses the existing `mandatory` /
   `optional`, `nullable` (default `false`), `default` and the type contracts:

   | Declared | Absent | `null` | Other value |
   |---|---|---|---|
   | `mandatory`, not nullable | error | error | by type and contracts |
   | `mandatory`, `nullable` | error | valid | by type and contracts |
   | `optional`, not nullable | valid | error | by type and contracts |
   | `optional`, `nullable` | valid | valid | by type and contracts |

3. **A default applies only to Absent.** Never to `null`, never to an empty
   value, never to `0` / `false`. A default is declared only on an `optional`
   field (`default` with `mandatory` is invalid) and `default: null` is invalid
   under `nullable`, since it would erase the distinction (both in `shape.yaml`).
   `null` is therefore never a request for the default.
4. **Whether a default may be applied depends on Role, not only on the field.**
   `BOUNDARY.md` allows a schema default for `authority: config`, forbids it for
   `authority: state` and `operational`, `lifecycle: volatile` and
   `structural: key`, treats `lifecycle: derived` as computation, and a
   `structural: reference` only under strict conditions. This ADR fixes only the
   trigger (rule 3). The eligibility rule and the application are P3.
5. **An absent optional field stays absent.** With no applicable default it does
   not become `null` or an empty value, in `expand`, `collapse` and
   `normalize` alike.
6. **`value` and `default_injection` are different keys.** In the expanded form
   `value: null` is data; `default_injection: null` is access behavior (the field
   does not appear for a requestor without access). They are never merged or
   inferred from one another.
7. **Authored values are not reconstructed from the default.** If the document
   wrote a value equal to the default, it stays authored; if it omitted the
   field, the default does not turn into an authored value (this builds on the
   intent being supplied to `collapse`, ADR 0001).
8. **`state_surface`.** A missing observation is never replaced by a schema
   default, and a missing `mandatory` field on `state_surface` is a **coverage
   fact**, not automatically an authoring-validation error. An absent field does
   not prove the field is not on the device. The engine's coverage values
   (`MATERIALIZATION-SPEC.md` B1, B2) stay separate:

   | Coverage | Meaning |
   |---|---|
   | `observed` | the observe call saw the field; its value is authoritative |
   | `unobserved` | the observe call did not cover the path |
   | `absent` | the observe call looked and authoritatively found the field not there |
   | `unknown` | the observe call saw the field and the **device itself** reported an indeterminate value (for example a sensor reporting a fault) |

   Missing coverage information is none of these: it cannot be classified into
   any of the states automatically and must not be called `absent`. A partial
   observation can be processed without inventing the missing field, but it does
   not thereby become a complete, conforming observation: completeness and
   conformance violations are the engine's check. The library knows only Absent
   and Present at the YAML level and does not produce the coverage values.

## Round trip

- Absent stays absent: `expand` creates no node for an absent field and
  `collapse` removes none.
- `null` stays `null`: an expanded node with `value: null` collapses to
  `alma: null` and expands back unchanged.
- `alma:` and `alma: null` are semantically one state. Telling them apart at the
  byte level is source-preserving editing (P3b), not this ADR.
- An empty value (`""`, `[]`, `{}`) is preserved as itself.

## Consequence: policy on an absent field

The expanded form requires the whole key set of ADR 0001, `value` included. A
field cannot therefore carry an explicit policy (`access`, `modify`, ...) and be
absent at the same time. This limit is accepted and no new YAML structure is
introduced for it: with no authored value there is no field instance to attach a
wrapper to, so a rule for an absent field is declared in the schema or at the
right level of the PolicySurface. `null` must not be used as a stand-in for
Absent only to attach a policy.

## Relation to the engine

The engine's open question is whether `null` is a value or an absence marker in
its canonical value representation (`MATERIALIZATION-SPEC.md`, B2's closing
paragraphs, and `Authored(Option<Value>)`). This ADR decides the YAML-level side
only: Absent and `null` are different. How the engine's authored-absent maps onto
this is agreed in the integration phase (P5, P6), not here.

## Vectors

Provisional vectors are in `corpus/presence/vectors.yaml`, in the format of
ADR 0005. The `shape` shorthand in the vectors is a fixture notation, not
a schema; the shared runner binds it to the real Shape members (`shape_type`,
`scalar_type`, `mandatory` / `optional`, `nullable`, `default`) and no parallel
primitive schema is defined. The vectors assert only that a default is a
*candidate* for an absent optional field; whether it is applied depends on Role
and belongs to separate P3 vectors. Round-trip expectations are typed values (`{type, value}`, with Absent as
`{type: absent}`; ADR 0005), so a value, a state and a serialized text are never
confused.

## Decisions recorded from the review of PR 6

- A missing `mandatory` field on `state_surface` is a coverage fact (rule 8).
- A policy on an absent field is not expressible; the limit is accepted and
  ADR 0001 is not changed.
- Error codes stay provisional until the common error contract (issue #3
  point 6); that is now ADR 0007, which also renamed `CONTRACT_VIOLATION` to
  `MIN_ITEMS_NOT_MET`.

## Open questions

1. Error codes (`MISSING_MANDATORY`, `NULL_NOT_ALLOWED`, ...) are reviewed in ADR 0007.
2. How the engine's authored-absent maps onto Absent (P5, P6).

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits (dependencies kept, none changes this contract):

- Default eligibility by Role is decided in P3; here a default is only a candidate.
- The mapping of the engine's authored-absent maps onto `Absent` is P5/P6.
- The engine coverage values are not derived from YAML structure.
