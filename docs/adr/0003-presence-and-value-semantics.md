# ADR 0003: presence and value semantics (draft)

Status: **proposed.** Resolves issue #3 point 3. It does not decide the type of
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
   default. At the YAML level the library knows only Absent and Present; the
   coverage vocabulary (`missing`, `unknown`, `not_observed`) is the engine's and
   is not produced here.

## Round trip

- Absent stays absent: `expand` creates no node for an absent field and
  `collapse` removes none.
- `null` stays `null`: an expanded node with `value: null` collapses to
  `alma: null` and expands back unchanged.
- `alma:` and `alma: null` are semantically one state. Telling them apart at the
  byte level is source-preserving editing (P3b), not this ADR.
- An empty value (`""`, `[]`, `{}`) is preserved as itself.

## Consequence: policy on an absent field cannot be written

The expanded form requires the whole key set of ADR 0001, `value` included. A
field cannot therefore carry an explicit policy (`access`, `modify`, ...) and be
absent at the same time; the only way to attach a policy is to give it a
value, which for an optional field means `null` (if `nullable`) or a real
value. This is a limitation of the chosen recognition rule, recorded here, not
solved here.

## Relation to the engine

The engine's open question is whether `null` is a value or an absence marker in
its canonical value representation (`MATERIALIZATION-SPEC.md`, B2's closing
paragraphs, and `Authored(Option<Value>)`). This ADR decides the YAML-level side
only: Absent and `null` are different. How the engine's authored-absent maps onto
this is agreed in the integration phase (P5, P6), not here.

## Vectors

Provisional vectors are in `corpus/presence/vectors.yaml`. The corpus format is
still a P0 item.

## Open questions

1. Is `mandatory` an error for an absent field on `state_surface`, or is absence
   there a coverage fact (rule 8) and not a validation error?
2. The policy-without-value limitation above: accept it, or allow a policy on an
   absent field in some other way (this would change ADR 0001)?
3. Error codes (`MISSING_MANDATORY`, `NULL_NOT_ALLOWED`, ...) are provisional
   until the error contract (issue #3 point 6).
