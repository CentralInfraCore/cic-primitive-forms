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
