# P3, fourth unit: PolicyResolution - the effective access and modify rule of every position

Status: **design, no code.** It builds on the accepted ADR 0004 (access vocabulary), ADR 0010 and the pinned `access-policy/1`
grammar (cic-primitives D-018: `P0-4-ACCESS-GRAMMAR.md`, section 4, and the reference model `access_policy.py` with its golden
vectors `access-vectors/resolution.yaml`), on `docs/P3-NORMALIZE-DESIGN.md` section 4 and on the merged `Normalize` (unit 3). It
re-opens none of them and changes nothing of `normalize` / `denormalize`. The points that are the owner's are in section 9.

## 1. What it is, and what it is not

For every position of a normalized object it says **which rule applies to `access` and to `modify`, in which layer it was found,
and where it was declared**. That is data. It is not a decision: nothing here knows a requestor, a certificate, an owner or a
channel, and nothing says allow or deny. The Relay takes this output and a request context and decides (P5/P6). So the unit is the
port of the reference `resolve` **up to, and not including, `_decide`**.

## 2. The model: objects, carriers, policies

The reference resolves over an object graph. A normalized document maps onto it like this:

| Reference | In a normalized object |
|---|---|
| object | the root, the value of a composite position, a list element (a record named by its declared identity, ADR 0006) |
| `parent` | the object that holds the field (composite value: the object around it; list element: the object that holds the list field) |
| `carrier` (the Access of the parent field holding the object) | the Access wrapper of the composite position; for a list element, the wrapper of the list field (an element inherits from its list field and its object, never from a sibling) |
| `policy` | the root: the compiled `policy_surface` (`absent` when the schema declares none, a PolicySurface when it does, also `{}`); every other object: `absent` (the schema declares one PolicySurface, at the root) |
| `target.surface` | `config_surface`, the surface the schema was compiled for |
| `target.field` | the Access wrapper of the position being resolved |
| `target.object` | the object that **contains** the position |

The target of a resolution is every declared position of the object (`value_origins` lists them): the primitive fields, the
composite and list positions themselves, and the fields of every list element. A position inside an inserted default value is not a
declared position of its own (a default is a whole value, unit 2) and has no entry.

## 3. The result

```
PolicyResolution            keyed by the canonical logical path of the position
  <logical_path>
    access: EffectiveRule
    modify: EffectiveRule

EffectiveRule
  rule         list<CertPattern> | open | owner | none | unknown
  layer        field | surface_rule | object_default | field_inherited | policy_inherited | system | none
  source_path  logical path of what declared it: the position (field, field_inherited), the object (surface_rule, object_default,
               policy_inherited), "" for system and none
```

`none` is a `reset` barrier with no local rule (the reference's `RESET_NO_LOCAL_RULE`); `unknown` is a policy or object the walk
had to read and could not (`POLICY_CONTEXT_UNKNOWN`, `CONTEXT_INCOMPLETE`). Neither is ever turned into `open`, `owner` or a
list, and an empty list `[]` is a rule like any other (terminal, section 5). The reference's layer names `surface_rules` and
`default` are `surface_rule` and `object_default` here (the P3 design names).

It is a separate structure beside the normalized object, as decided in unit 3: the effective rules are not part of the value
tree, and `ValueOrigins` and the P2 `Provenance` are not read or changed. An inserted default has the symbolic policy of any
position (`inherit`), so its rule always comes from inheritance, never from the fact that it was inserted.

## 4. The algorithm (the reference, without the decision)

For one position and one operation, the first rule found wins:

1. **field.** The wrapper of the position holds a list for the operation: that list, layer `field`. The symbol `inherit` declares
   nothing.
2. **surface_rule.** The containing object's `surface_rules` for `(config_surface, operation)`: the list, layer `surface_rule`. If
   the containing object's policy is `unknown`: `unknown`.
3. **object_default.** The containing object's declared `default_access` / `default_modify` (a list, or the symbol the author wrote:
   `open` / `owner`): layer `object_default`.
4. **a. field_inherited.** Walk from the containing object up through its carriers:
   - an object whose own policy has `inherit_policy: reset` ends the walk with the **barrier** set (it cuts what the object takes from
     above, carrier rule included; this is checked first at every step);
   - a carrier with mode `reset` sets the barrier and closes the field rules; `no-inherit` closes the field rules without a barrier;
     `inherit` while the field rules are open and the carrier holds a list for the operation: that list, layer `field_inherited`,
     source the carrier;
   - the next step goes to the parent; a missing parent object or a cycle is `unknown`.
5. **b. policy_inherited.** Walk up the objects again: `inherit_policy: reset` (barrier) and `no-inherit` end it, a `reset`
   carrier sets the barrier; at each ancestor its `surface_rules`, then its declared defaults: layer `policy_inherited`, source the
   ancestor; an ancestor with an `unknown` policy that the walk must read: `unknown`.
6. **system.** `open` for access, `owner` for modify, layer `system`, **unless the barrier is set**: then `none`.

The two scopes keep their difference (`P0-4-ACCESS-GRAMMAR.md`, section 2): on a **carrier** `no-inherit` stops the carrier's rule
but the chain of PolicySurfaces still applies, `reset` cuts the rule, the chain and the system default; on an **object**
`no-inherit` stops the ancestors' PolicySurface and keeps the local rules and the system default, `reset` cuts the same and
leaves no system default. The mode is read in the bound access grammar (unit 1's `ReadMode`): grammar 1 `true` / `false` / `0`,
grammar 2 the enum names, never mixed or coerced; `false` and `0` stay different.

## 5. Properties the design guarantees (and the tests prove)

- **Terminal.** A found list ends the walk, also an explicit `[]`; no parent's `open` rescues it.
- **Never more permissive than known.** `none` and `unknown` are never `open`, `owner` or a list; an empty or `{}` policy is transparent and
  hides no ancestor's rule; an `absent` policy is transparent.
- **Local rules are never cut.** The position's own list (1) and the containing object's own rules (2, 3) apply even under `reset`.
- **Order-free.** Positions are keyed by logical path: reordering list elements or mapping entries changes no entry. An element's rule
  never depends on a sibling.
- **Deterministic.** Same normalized object, same compiled schema, same grammar: same output; no clock, no I/O.
- **Total.** Every declared position gets both operations; an object the schema cannot describe is an error of the caller's input,
  not a default.

## 6. Inputs and the place of `unknown`

`PolicyResolution(normalized, compiled schema, grammar, [unknown objects])`. In a compiled single schema every policy is known, so
`unknown` appears only for objects the **caller** marks as not known (a P5 snapshot-reference not resolved, an aggregate whose policy
is not available): an optional set of object paths. Without it nothing is unknown. A malformed wrapper (an access value that is
neither a list nor `inherit`) cannot occur in a document that was validated and expanded; if it does, it is an error of the input
(`Unsupported`), never a guess.

## 7. How it is tested

1. **The golden vectors, as the cross-check.** The reference vectors are request-bound (`allow` / `deny` / `error`). The unit's
   resolver is run on each vector's abstract object graph and gives an `EffectiveRule`; a **test-only** port of the reference
   `_decide` (not part of the library) applies the vector's request to it, and the outcome, code and layer must equal the vector's
   expectation. So the semantics are checked end to end, while the library still decides nothing. Both languages run the same vectors.
2. **Shared cases for the Forms mapping** (`testdata/p1/`): nested composites, list elements by identity (reordered), `inherit` /
   `no-inherit` / `reset` on a carrier and on the root policy, grammar 1 and 2, an inserted default resolving by inheritance, a
   schema with no `policy_surface`, one with `{}`, `[]` at each layer.
3. **Properties:** order-freedom, termination, `none` / `unknown` never permissive, the local layers never cut, and that
   `normalize` / `denormalize` of the same document are unchanged by running the resolution.
4. The same gates as the other units: seeded defects (a swapped layer order, `reset` read as `no-inherit`, a barrier ignored, an empty
   list treated as inherit, a sibling's rule used), branch coverage, mutation, fuzz with a canary, Rust-Go parity.

## 8. What it does not do

No allow or deny; no matching of a CertPattern; no owner trust or identity; no authentication; no snapshot-based reference
resolution (P5); it does not change the schema compiler's PolicySurface reading, `normalize`, `denormalize` or the P2 forms.

## 9. Decisions that are the owner's

1. **The vectors are not in the grammar pin.** `access_policy.py` and `access-vectors/*.yaml` are at the pinned commit
   (`73ae469`) but not in the pin's file set (`corpus/pins.yaml`), so they are not digest-bound. Using them as the cross-check means
   adding them to the pin: the manifest hash, hence the pin name (`cic-primitives-73ae469-<new 8>`), changes, and every `grammar:`
   reference is migrated on purpose (the documented procedure). Proposed: yes, in the first PR of the unit, as a pin-only change.
2. **Carrier of a list element** = the list field's wrapper (section 2). The reference does not say; the P3 design says "along the
   element's logical path, from its list field and its object". Proposed as written.
3. **Which positions are targets:** all declared positions, composites and lists included (section 2), or only primitive
   fields. Proposed: all, because the carriers' own rule is what its sub-objects inherit and a consumer may ask for either.
4. **`unknown` as a caller input** (section 6), not derived. Proposed as written; P5 will fill it.
5. **Naming:** `surface_rule`, `object_default`, `policy_inherited`, `field_inherited` (the P3 design names) against the reference's
   `surface_rules`, `default`. Proposed: the P3 names, with the mapping stated once in the code docs.
