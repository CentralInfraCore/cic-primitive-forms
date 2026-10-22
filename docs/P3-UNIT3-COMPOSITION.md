# P3, third unit: normalize = expand + defaults, with the origin carried

Status: **design, no code.** It builds on `ApplyDefaults` of the second unit (PR 45, to be merged first), on the accepted
ADR 0001, 0003 (rule 6), 0010, 0011 and on `docs/P2-EXPAND-COLLAPSE.md`, and re-opens none of them. It decides the shape of
the composition and what must be proved before any code is written; the points that are the owner's are in section 6.

## 1. The problem

`ApplyDefaults` works on an authored document in the P1 short form (`Validate` refuses an expanded Access node). `Expand`
(P2) works on short, expanded and mixed documents and records how each position was written (`Provenance`). A normalized
object needs both: the fully expanded tree, the defaults of the absent fields, and for every value **who made it**:
the author or the normalization. Section 5 of `P3-NORMALIZE-DESIGN.md` keeps three notions apart; this unit must not blur
the first two:

| Notion | Answers | Stays in |
|---|---|---|
| authored provenance | how did the author write this position (short / expanded)? | P2 `Provenance` |
| resolution origin | did the author give this value, or did the normalization? | P3 `ValueOrigin` (`authored` / `default`) |

## 2. Order: expand first, then insert the defaults into the expanded tree

Two orders were considered.

- **A: defaults, then expand.** `ApplyDefaults` inserts a bare value, `Expand` wraps it. Rejected: `ApplyDefaults` validates with
  the P1 validator, which refuses the expanded atoms an author may have written for an explicit policy, so a document with one
  explicit policy setting could not be normalized; and the inserted value would then get a P2 provenance entry ("written
  short"), a claim about the author that is false.
- **B: expand, then defaults (chosen).** `Expand` turns every position into the six-key atom (symbolic policy, in the bound
  grammar) and records the `Provenance`. The defaults are then inserted into the **expanded** tree: for each absent optional
  field that carries a default, a full atom with `value: <the default>` and the symbolic policy of any position, in
  schema order after the existing entries. No `Provenance` entry is made for it, because the author did not write it.

The decisions of the second unit stay: nothing is inserted into an absent composite or list, a default is a whole value, the
walk goes through present composites and declared-list records named by their identity, and a present `null`, `""`, `0`,
`false`, `[]` or `{}` is never replaced.

## 3. The inserted atom (ADR 0003, rule 6)

`default_injection` is what a requestor *without access* receives, not the schema default. An inserted atom therefore has
`default_injection: null` (the symbolic value), `conformance: implemented`, `access: inherit`, `modify: inherit` and `inherit`
in the bound spelling: exactly what `Expand` gives a short position, with `value` set to the default. The default is never
written into `default_injection`, and the policy of the inserted position is **not** computed here (that is the effective
rule, a later unit).

## 4. The result and the carry of the origin

```
NormalizationResult
  object          the expanded tree, defaults inserted
  provenance      P2, only for positions the author wrote (an inserted position has none)
  value_origin    logical path -> authored | default, for every declared position (as in ApplyDefaults)
```

- **The origin is never re-derived from the value tree.** `value_origin` is produced at the one place the default is inserted,
  and it travels with the object. A normalized object without its `value_origin` is a different thing (a bare expanded
  document); the type says so, and an API that takes "an object" and returns "a result" cannot silently produce `authored`
  for a value the normalization made.
- **Idempotence is stated on the pair.** `normalize(object, value_origin)` returns the same object and the same
  `value_origin`. Applied to a bare expanded document (no `value_origin` supplied) every present position is `authored`: that is
  true (it is written in that document) and it is the visible loss the pair exists to avoid.
- **Round trip.** `denormalize(result)` removes the positions whose origin is `default`, then collapses with the `provenance`
  and the trusted `Context`. The invariant of P2 extends to: `denormalize(normalize(x)) == x` as a value, for every valid
  authored `x`, including documents that mix short and expanded positions and explicit policy settings. This is the property
  that proves the system knows what the author gave and what the normalization created.

## 5. What must be proved before the code is merged

1. Shared cases in both languages (`testdata/p1/`): the P2 cases still pass; an explicit policy on a defaulted neighbour; an
   expanded-authored position beside an inserted one; every grammar spelling (1 and 2); a list element reordered (the result
   keyed by logical path does not change); the attested positions of a materialized document are not shortened and are never
   given a default.
2. Properties: `denormalize(normalize(x)) == x`, **including** an authored value that equals the default of its field (it
   stays `authored` and is not dropped), a document that mixes short and expanded positions, and list elements in another order
   (the result is keyed by logical path, so reordering changes nothing); `normalize` is idempotent on the **pair** (object and
   `value_origin`), not on the object alone; no authored value or explicit policy changes; the `default` set of `value_origin`
   equals the default candidates of the validation of `x`; the result is independent of list and mapping order.
3. Seeded defects for: an inserted position that gets a provenance entry, a `default_injection` that carries the default, an
   origin re-derived from the values, a default inserted into an absent composite.
4. The same gates as before: branch coverage, mutation, fuzz with a canary, parity of the two languages.

## 6. Decisions that are the owner's

1. **Chosen order B** (expand, then defaults). A is described above only to show why it was not taken.
2. **`value_origin` as a side map in the result**, not a marker on the node and not part of the P2 `Provenance` map. This keeps
   the six keys of the atom untouched and the two notions separate.
3. **`denormalize` as part of the contract** (the inverse that proves the origin was carried), not only a test helper.
4. Whether the unit also fixes the **P1 validator's refusal of expanded atoms** for `ApplyDefaults` (the second unit's entry
   point) or leaves `ApplyDefaults` as the short-form function and makes `normalize` the only entry for mixed documents. The
   second is smaller and is what this design assumes.
