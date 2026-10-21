# ADR 0011: which Role takes a schema default

Status: **proposed**. It closes the point ADR 0003 rule 4 left open ("whether a default may be applied depends on Role, not only on
the field") and is a precondition of the P3 implementation (`docs/P3-NORMALIZE-DESIGN.md`). It is not accepted until the repository
accepts it; no P3 code is written before.

## Context

ADR 0003 fixes that a default applies only to an **absent** field (never to `null`, `""`, `0`, `false`, `[]`, `{}`), that it is declared
only on an optional field, and that for `state_surface` a missing observation is a coverage fact. It leaves which Roles take a default
to P3. The Role atom (`schemas/atomic/role.yaml`, pinned) has **three orthogonal axes**, not a flat list: *authority* (exactly one of
`config`, `state`, `operational`; default `config`), *structural* (zero or more of `key`, `reference`) and *lifecycle* (zero or one of
`derived`, `volatile`). A node carries one value from each axis it uses.

## Decision

A schema default is applied to an absent field **if and only if all of these hold**:

1. the authority is `config` (written, or the axis default when none is written);
2. the field is optional and declares a `default` (as ADR 0003 already requires);
3. the node has no structural value (`key`, `reference`) and no lifecycle value (`derived`, `volatile`).

| Role value | Takes a schema default? | Why |
|---|---|---|
| `config` | **yes**, if optional and a default is declared | desired state the management plane writes |
| `state` | no | a missing observation is not a default state: there may simply be no observation (ADR 0003 rule 8) |
| `operational` | no | computed or collected at runtime, not configured |
| `derived` | no | the derivation creates the value, not a schema default |
| `volatile` | no | short-lived, not persisted desired state |
| `key` | no | an identity: normalization creates or changes no identity (ADR 0006); a key is also mandatory |
| `reference` | no (**conservative; not decided by the review**) | a default pointer to another node would invent a referential dependency |

An explicit `null`, `0`, `false`, empty string, list or mapping is never replaced by a default, in any Role.

### Compile-time rules

- A schema that declares a `default` on a node whose Role does not take one is **`SCHEMA_INVALID`** at compile time (path of the node):
  the default is data that would be silently ignored otherwise.
- An **unknown** Role value is `SCHEMA_INVALID`; it is never read as `config`. An absent authority axis is the only way to get `config`
  without writing it.
- A `default` on a mandatory field stays `SCHEMA_INVALID` (P1).

## Consequences

- The registry schemas that write `default` on a node with no role (for example `vlan_mode` of `network-interface`) are
  `config` by the axis default and are unchanged. A schema that writes `role: state` and a `default` is refused: that is the intended
  signal, found by the compiler instead of a silent no-op at normalization.
- ADR 0003 rule 4 becomes decidable: the answer depends on the three axes, not on a flat list.
- The compiler (P1) gains the role check; the check is a P3 task together with the normalizer, in both languages, with shared
  cases for each row of the table and for the refusals above.

## Not decided here

How a node whose **only** role value is `reference` is read when a default is declared (the table says: not applicable, hence
`SCHEMA_INVALID`); the review did not decide it, and it is the first thing to confirm when this ADR is accepted.
