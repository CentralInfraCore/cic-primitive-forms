# ADR 0000: P0 contract (draft)

Status: **draft, open decisions listed below.**

## Fixed

- Expanded form is recognized by the **complete key set declared by the
  schema** for the primitive (currently six for `Access`: `value`, `access`,
  `modify`, `inherit`, `default_injection`, `conformance`). The number is read
  from the schema, never hardcoded.
- Expansion recurses along **schema-declared primitive boundaries**, not along
  every YAML map key.
- The engine's internal types (`MaterializedField`, `Authored`, ...) do not
  appear in the YAML grammar.

## Open

1. **Partial expanded form.** A map with some but not all declared keys: an
   explicit error (current proposal), or accepted as a partial override?
   (`cic-primitives` D-010 says long form is needed only where an override is.)
2. **Path grammar.** Formal syntax for `a.b.c` and the escaping of a literal
   dot inside a key.
3. **`null`, absent, intentionally empty.** Three distinct states; must agree
   with the engine's still-open B2 question.
4. **Schema contract.** What the caller supplies: which fields are primitives,
   the inheritance source, the identity key of lists (`key_fields` /
   `logical_id`).
5. **Diff output.** Deterministic, logical-path based, byte-identical across
   Rust and Go.
6. **Errors.** Machine-readable error codes and source position (line, column).
7. **Ambiguity for `type: any` values.** A short-form map value that happens to
   contain the full key set: error, or schema-directed reading.
8. **Type of the symbolic `inherit`.** `access.yaml` types `access` and `modify`
   as `list<CertPattern>` with default `inherit`. A symbolic value in a list-typed
   field is a type conflict. Either the schema type becomes
   `list<CertPattern> | inherit`, or the expanded form carries the marker
   outside the field's type. Affects `validate` and `collapse`.
9. **Origin of `conformance`.** The default `implemented` is part of the atom's
   description; actual device capability comes from the adapter binding
   (`not_implemented` is device-level and enforced by the Relay, per
   `access.yaml`). The default must not be treated as evidence of support.
   Decide whether `conformance` is user-writable in an authored document. There
   is one expanded-form grammar; what may be written by hand and what is filled
   from the binding is a property of the validation contract, not of a second
   grammar.
    *Status: proposed in ADR 0010 (only a trusted binding can state a capability;
    origin is a trusted context, not a YAML key).*
10. **Branch-coverage measurement.** The gate requires > 90% branch coverage in
    both languages. `go test -cover` measures statements, not branches, and Go
    has no native branch coverage; Rust branch coverage via `llvm-cov` should be
    verified for stability before relying on it. Name a measurement tool per
    language that enforces the same requirement. Replacing branch coverage with a
    weaker metric (for example statement coverage plus mutation score) lowers the
    gate and needs its own explicit, recorded decision.
    *Status: the contract is ADR 0009; the tool per language is selected on P1
    sample evidence, so this point stays open until then.*

Points 1 and 7 describe the same recognition problem (a `type: any` value that
happens to carry the full key set) and must be resolved together in P0.
