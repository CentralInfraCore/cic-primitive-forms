# P3: normalize — design proposal

Status: **design, no code**. It follows the accepted ADR 0001, 0003, 0004, 0006, 0010 and the pinned `access-policy/1` grammar
(cic-primitives D-018, `P0-4-ACCESS-GRAMMAR.md`); it re-opens none of them. The four open decisions of the first version were taken in the
architecture review of this PR (section 8); the one that needs an ADR is written as `docs/adr/0011-defaults-by-role.md`, **accepted**
2026-10-21 (with the default ban for `reference`). The P2 post-merge evidence is complete, so the P3 implementation may start, in small
steps (the ADR's acceptance record: the schema compiler first, default application after).

P3 in `PHASES.md`: *Normalize: defaults, `inherit: true/false/0`. Exit: the same input and schema give the same fully resolved
result in both languages.* Resolving a reference from a supplied snapshot and materializing is **P5**; this document does not.

## 1. The boundary: what "resolved" means here

The Forms library **does not decide authorization** (architecture review of #40). An effective permission needs a requestor, an
authenticated channel and a trusted owner, all of which come from the processing context and never from the document, and
verifying them is the Relay's. So P3 resolves **what the document and the schema determine without any requestor**:

| Resolved in P3 (document + schema only) | Not in P3 |
|---|---|
| the value of an absent field that has a default and may take it | allow / deny for a subject |
| the `InheritanceMode` of every carrier and object, in the bound grammar | matching a CertPattern against an identity |
| for each position and each operation (`access`, `modify`): the **effective rule** and the layer it comes from | the owner's trust, the system default as a decision |
| the origin of every value (authored or default) and of every rule (its layer) | verifying an attestation |

An **effective rule** is data, not a decision: `list<CertPattern>`, `open`, `owner`, `none` (a `reset` barrier with no local rule: the
resolver will deny, `RESET_NO_LOCAL_RULE`), or `unknown` (a policy the walk must read is `unknown`: `POLICY_CONTEXT_UNKNOWN`). Each
carries its **layer** (`field`, `surface_rule`, `object_default`, `field_inherited`, `policy_inherited`, `system`) and the logical path
of the position or object that declared it. A later resolver (Relay) takes that output and a request context and decides.

## 2. What the input is, what the output is

Input: an expanded document (P2, with its `Provenance`), the compiled schema (the `config_surface` nodes **and** the `policy_surface` and
`identity.base` declarations, section 4), the bound access grammar. Output: one `NormalizationResult` produced by one deterministic
operation, bound to that document and that schema:

```
NormalizationResult
  normalized_object            the tree of primitive positions, values and their origin
    <position>
      value                    unchanged where it was authored or has no default
      value_origin             authored | default          (never "inherited": see below)
      policy                   the six Access fields as written; explicit ones untouched
      provenance               the P2 entry, carried through unchanged
  policy_resolution            keyed by logical path, separate from the value tree
    <logical_path>
      access: { rule, layer, source_path }
      modify: { rule, layer, source_path }
```

- `value_origin` is `authored` or `default` and nothing else. Inheritance here concerns **policy rules**, not copying values down a tree, so
  a value is never "inherited".
- The effective rules are **not** part of the value tree: a consumer processes the values without taking resolved policy data for fields the
  configuration declares. They live only in `policy_resolution`.
- Normalization is **idempotent** (`normalize(normalize(x)) == normalize(x)`) and **never changes an authored value or an explicit
  policy setting**; it only adds what was absent and annotates.

## 3. Defaults (ADR 0003 rules 3-4)

- A default applies **only to an absent field**. Never to `null`, `""`, `0`, `false`, `[]`, `{}` (rule 3, already tested in P1 as
  *candidates*).
- Whether it may apply depends on **Role** (rule 4). The rule is ADR 0011 (accepted): a schema default applies to an absent field only if
  the node is `config` (the authority axis; written or by its default), optional, and has no `key`, `reference`, `derived` or `volatile`
  role; `state`, `operational`, `derived`, `volatile` and `key` never take one. A schema that declares a default on a node whose Role
  does not take one is `SCHEMA_INVALID` at compile time, and an unknown Role value is never read as `config`.
- A field that declares a default but is **mandatory** is a schema defect already refused in P1 (`SCHEMA_INVALID`).
- `default_injection` stays separate from the permission decision and from `value` (ADR 0003 rule 6).

## 4. Inheritance (D-018 sections 2-4)

`InheritanceMode` has three states with three behaviours; `false` and `0` are never equal and a round trip keeps the spelling.

- **Carrier scope** (`Access.inherit` of the field that holds an object): `inherit` passes the carrier's explicit list down to a
  sub-object that has no rule of its own; `no-inherit` does not pass it, the chain of PolicySurfaces still applies; `reset` cuts the
  carrier's rule, the ancestors' rules and the system default.
- **Object scope** (`PolicySurface.inherit_policy`): the same three for what an object takes from its ancestors.
- **Evaluation order** (one contract, written down once, tested once): the first rule found wins — the target field's explicit
  list; the object's `surface_rules` for the surface; the object's declared defaults; then the rule passed down by carriers (nearest
  explicit wins, a `no-inherit` carrier stops everything above it, a `reset` carrier stops field rules and the chain); then the
  ancestors' PolicySurface; then the system default (`open`/`owner`), **not** after a `reset` barrier. A found list is terminal: an
  explicit `[]` is "nobody" and no parent's `open` rescues it.
- The system defaults `open`/`owner` are **never materialized** into the instance (they would shadow an ancestor's rule); they appear
  only as the effective rule's layer `system`.
- A policy of an object that is `absent` or `{}` is transparent; a missing object, a cycle, or an `unknown` policy the walk must read
  gives `unknown`, never an invented rule.

- **None and unknown are kept.** A `none` rule (a `reset` barrier with no local rule) and an `unknown` one (a policy the walk must read is not
  known) are never turned into `open` or into any assumed permission, and an empty rule list never goes looking for a more permissive
  parent. A policy that is absent or declared empty (`{}`) is a legal, transparent state; a policy that is invalid or cannot be resolved is a
  different state (`unknown`) and never yields a more permissive rule.
- **The compiled schema reads the policy.** P3 extends `compile_schema` to read `policy_surface` (`default_access`, `default_modify`,
  `surface_rules`, `inherit_policy`) and `identity.base`, and nothing more: no authentication, no certificate check, no external snapshot
  (that is P5). Until then P1 compiled only the nodes of `config_surface`.

The pinned `access_policy.py` and its golden vectors (`access-vectors/`) are the executable reference of this order; P3 ports it and
adds the layer and the source path to its output. Where Forms and that reference disagree, the reference wins until D-018 changes.

## 5. Three notions that stay separate

| Notion | Answers | Lives in | Derived from |
|---|---|---|---|
| authored provenance | how was this position written (short/expanded)? | P2 `Provenance` | the authored document |
| resolution origin | where did this value or rule come from (authored/default; field/surface/inherited/system)? | P3 `value_origin`, `policy_resolution.*.layer` | the document and the schema |
| authenticated basis | is the capability the binding's own statement? | ADR 0010 `Context` | a trusted context |

None is computed from another. In particular a `default` or an inherited rule is never evidence of a capability.

## 6. List elements

A list element's declared key is its identity (ADR 0006) and **normalization must not change it**: no default and no inherited value
may alter a key field, and a key field is never defaulted. `$.vlans[{vlan_id=100}]` becoming `$.vlans[{vlan_id=200}]` is not a
normalization; if a later layer needs it, it is an explicit identity change (diff/patch, P4). Reordering a list changes no result,
because every result is keyed by the logical path. Inheritance inside lists runs along the element's logical path, so an element's
fields inherit from its list field and its object, not from a sibling.

## 7. Same result in Rust and Go

- A new corpus kind `normalize` (ADR 0005 notation: schema tree with roles and defaults, a policy tree, a document, the expected
  normalized object as typed values) run by both languages; accounting stays exact.
- Shared negative and edge cases first: `false` vs `0` vs `true` at every scope; absent vs explicit-equal-to-default; `null`, `[]`,
  `""` never request a default; each of `inherit`/`no-inherit`/`reset` on a carrier and on an object; `reset` with and without a local
  rule; an explicit `[]` under an `open` parent; `unknown` policy on and off the walk; the grammar-1 and grammar-2 spellings (no
  coercion); inheritance through several levels and through list elements; a default that equals an explicit value is still `default`.
- Properties (idempotence, no authored value or explicit setting changed, the result independent of list order and mapping order), a
  fuzz target, seeded defects, mutation, branch coverage: the same gates as P1/P2, no relaxation.
- The golden vectors of `access-vectors/` run against the Forms port in both languages (they are the cross-check of the semantics).

## 8. Decisions (taken in the architecture review of this PR)

| # | Question | Decision |
|---|---|---|
| 1 | Role and defaults | `config` takes a default (optional, declared); `state`, `operational`, `derived`, `volatile`, `key` do not. Written as ADR 0011 (**accepted** 2026-10-21). |
| 2 | Where the effective rule goes | A separate `policy_resolution` keyed by logical path (section 2). |
| 3 | How far the resolver port goes | It stops at the rule, its layer and its source; the decision (a request, an authenticated requestor, a trusted owner) is the Relay's. `none` and `unknown` are kept. |
| 4 | Is `policy_surface` compiled in P3 | Yes (section 4), still P3 and not P5. |

Closed at the acceptance of ADR 0011: a node whose role is **only** `reference` takes no default (a declared one is `SCHEMA_INVALID`); explicit references stay valid.

## 9. Not in P3

Snapshots, references and materialization (P5); deciding a request; verifying an attestation or an owner binding; diff and patch of
identities (P4); source-preserving edits (P3b); `state_surface` and `operation_surface` documents.
