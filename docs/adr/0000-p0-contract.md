# ADR 0000: P0 contract (draft)

Status: **draft, open decisions listed below.**

## Decision index (living)

This ADR stays `draft` until P0 closes. It is the index of the decisions; the text of a
decision lives in its ADR.

Status rule. `Proposed`: reviewed and versioned, not formally finalized. `Accepted`: the
normative decision inside the P0 scope was expressly accepted, dependencies and limits
documented. `Implemented/Verified`: tracked separately; an `Accepted` ADR proves no Rust
or Go conformance.

| ADR | Subject | Status | Issue #3 point |
|---|---|---|---|
| [0001](0001-recognition-rule.md) | recognition rule | accepted | 1, 7 |
| [0002](0002-minimal-schema-contract.md) | schema contract | accepted (grammar identity implemented and verified; `item_choice` profile temporary) | 4 |
| [0003](0003-presence-and-value-semantics.md) | presence and value semantics | accepted | 3 |
| [0004](0004-access-inheritance-vocabulary.md) | inheritance vocabulary | accepted, reading contract only; D-018 adoption recorded as an amendment | 8 (not ticked) |
| [0005](0005-conformance-corpus-contract.md) | corpus contract and gate | accepted | corpus skeleton |
| [0006](0006-logical-path-grammar.md) | logical paths | accepted | 2 |
| [0007](0007-error-contract-and-source-positions.md) | errors and positions | accepted | 6 |
| [0008](0008-deterministic-diff-contract.md) | deterministic diff | accepted | 5 |
| [0009](0009-branch-coverage-measurement-contract.md) | branch coverage measurement | accepted (tools selected and qualified on samples; product code is P1) | 10 |
| [0010](0010-conformance-origin-and-authority.md) | conformance origin | accepted | 9 |
| [0011](0011-defaults-by-role.md) | schema defaults by role (P3) | accepted (2026-10-21); closes the point ADR 0003 rule 4 left open | - |
| [0012](0012-source-preserving-edit.md) | source-preserving edit of one scalar (P3b, first unit) | accepted (2026-10-22) | - |
| [0013](0013-patch-first-unit.md) | patch, the first unit (P4) | accepted (2026-10-23) | - |

What still prevents P0 from closing (one item; the grammar identity and the coverage method are done):

1. the upstream type decision behind point 8 (cic-primitives #17).

Everything else is a P1 proof obligation or a later-phase item, recorded in each ADR's
acceptance record and in `docs/reviews/p0-acceptance-review.md`. Where each of those items went, and which are still open, is
mapped in [`docs/P0-CARRY-OVER.md`](../P0-CARRY-OVER.md). ADRs 0011 to 0013 were written after P0 and are listed above for completeness; they are not P0 decisions.

## Fixed

- Expanded form is recognized by the **complete key set declared by the
  schema** for the primitive (currently six for `Access`: `value`, `access`,
  `modify`, `inherit`, `default_injection`, `conformance`). The number is read
  from the schema, never hardcoded.
- Expansion recurses along **schema-declared primitive boundaries**, not along
  every YAML map key.
- The engine's internal types (`MaterializedField`, `Authored`, ...) do not
  appear in the YAML grammar.

## Open questions (historical)

This is the original question list as it stood when P0 started. It is kept as a record.
**The current status of each point is the Decision index above**, not the wording below;
points 1 to 7, 9 and 10 are decided and accepted; 8 is accepted only as a
reading contract, with the upstream type decision open.

1. **Partial expanded form.** A map with some but not all declared keys: an
   explicit error (current proposal), or accepted as a partial override?
   (`cic-primitives` D-010 says long form is needed only where an override is.)
    *Resolution: Decided in ADR 0001 (accepted): a partial expanded form is a short form and validation rejects it; it is never a partial override.*
2. **Path grammar.** Formal syntax for `a.b.c` and the escaping of a literal
   dot inside a key.
    *Resolution: Decided in ADR 0006 (accepted).*
3. **`null`, absent, intentionally empty.** Three distinct states; must agree
   with the engine's still-open B2 question.
    *Resolution: Decided in ADR 0003 (accepted); the engine's B2 mapping is P5/P6.*
4. **Schema contract.** What the caller supplies: which fields are primitives,
   the inheritance source, the identity key of lists (`key_fields` /
   `logical_id`).
    *Resolution: Decided in ADR 0002 (accepted): the contract, and a grammar identity that is pinned and reproduced from upstream in CI. The `item_choice` compatibility profile stays temporary.*
5. **Diff output.** Deterministic, logical-path based, byte-identical across
   Rust and Go.
    *Resolution: Decided in ADR 0008 (accepted).*
6. **Errors.** Machine-readable error codes and source position (line, column).
    *Resolution: Decided in ADR 0007 (accepted).*
7. **Ambiguity for `type: any` values.** A short-form map value that happens to
   contain the full key set: error, or schema-directed reading.
    *Resolution: Decided in ADR 0001 (accepted): the key-set rule applies at `opaque` positions too; a literal map equal to the key set is written under an explicit wrapper (an accepted cost).*
8. **Type of the symbolic `inherit`.** `access.yaml` types `access` and `modify`
   as `list<CertPattern>` with default `inherit`. A symbolic value in a list-typed
   field is a type conflict. Either the schema type becomes
   `list<CertPattern> | inherit`, or the expanded form carries the marker
   outside the field's type. Affects `validate` and `collapse`.
    *Resolution: ADR 0004 (accepted) as the library's **reading contract only**. The type decision itself stays **open upstream** (cic-primitives #17).*
9. **Origin of `conformance`.** The default `implemented` is part of the atom's
   description; actual device capability comes from the adapter binding
   (`not_implemented` is device-level and enforced by the Relay, per
   `access.yaml`). The default must not be treated as evidence of support.
   Decide whether `conformance` is user-writable in an authored document. There
   is one expanded-form grammar; what may be written by hand and what is filled
   from the binding is a property of the validation contract, not of a second
   grammar.
    *Resolution: Decided in ADR 0010 (accepted): origin is a trusted context, not a YAML key; only a trusted binding can state a capability.*
10. **Branch-coverage measurement.** The gate requires > 90% branch coverage in
    both languages. `go test -cover` measures statements, not branches, and Go
    has no native branch coverage; Rust branch coverage via `llvm-cov` should be
    verified for stability before relying on it. Name a measurement tool per
    language that enforces the same requirement. Replacing branch coverage with a
    weaker metric (for example statement coverage plus mutation score) lowers the
    gate and needs its own explicit, recorded decision.
    *Resolution: Decided in ADR 0009 (accepted): the contract, and the selected tools `gobranch` and `rustbranch`, qualified on hand-counted samples. Measuring product code is mandatory from P1.*

Points 1 and 7 describe the same recognition problem (a `type: any` value that
happens to carry the full key set) and were resolved together in ADR 0001.
