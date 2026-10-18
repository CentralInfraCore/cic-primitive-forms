# ADR 0002: minimal schema contract (draft)

Status: **proposed.** Closes issue #3 point 4 once accepted. It does not decide
the other points of #3 (`null` / absent / empty, `inherit` typing, origin of
`conformance`, diff output, error codes, path grammar, coverage measurement); it
only states which schema information those decisions can rely on.

## Principle

The schema defines the meaning of a primitive. This library receives a
preprocessed, version-pinned schema and reads from it what it needs. It defines
no parallel semantics, it never looks for a newer schema version and it never
fetches a dependency (the caller, i.e. the Relay, resolves and pins them).

This contract is derived from the existing CIC schemas, not from a data
structure invented for Rust or Go:

| Source | Used for |
|---|---|
| `cic-primitives` `proposals/atom-grammar/README.md` §2 | normative form of the five Shape types, list key rule |
| `cic-primitives` `schemas/atomic/shape.yaml` | meaning of Shape, presence, nullable, default |
| `cic-primitives` `schemas/atomic/access.yaml` | the expanded key set `A`, symbolic defaults |
| `cic-primitives` `schemas/atomic/role.yaml` | Role axes |
| `cic-schema-registry` `general/network/network-interface/network-interface.yaml` | a real composition using all of the above |

## Findings that shape the contract

1. **The Shape types are closed: five.** `scalar`, `composite`, `collection`,
   `choice`, `opaque` (`shape_type`). `position: any` in ADR 0001's vectors is a
   test abstraction, not a sixth Shape type. `Access.value: any` takes its real
   value type from the enclosing Shape (see the mapping below).
2. **List identity is `role: key` / `item_key`, not `key_fields`.** The grammar
   (§2.3) defines the list key by `role: key` on `item_fields` and an `item_key`
   list for a composite key. `key_fields` is a different concept
   (`binding_surface.addresses[].key_fields`, the Address key components) and
   must not be reused for list identity.
3. **The released grammar lacks `item_choice`.** `item_choice` (collection
   element as a discriminated union) is present on `cic-primitives` `devel`
   (`5a26bd8`) and absent both from `main` (`411ce80`) and from the release tag
   `primitives/@v0.2.0` (`960ee0f`; the tag is an ancestor of neither branch, so
   `main` does not carry it). The registry's `network-interface.yaml` refers to
   atoms as `cic:core:*@v0.2.0` yet already uses `item_choice`. The contract below
   is written against the `devel` grammar, and the grammar version is part of the
   schema identity.
4. **No real schema in the registry uses a plain `choice`.** A search of
   `cic-schema-registry` found no `shape_type: choice`; the real mechanism in use
   is `item_choice`. A `choice` fixture can therefore only be synthetic, taken
   from the grammar.
5. **No instance-document specification was found.** The existing material
   specifies schemas (compositions of Shape fields). A search of
   `cic-primitives`, `cic-schema-registry` and the engine docs found no rule
   that says what an authored intent document looks like for a given schema.
   The mapping in "Instance documents" below is therefore a proposal, not a
   citation. The model exists; the bridge to a documented instance form breaks
   here.

## What the library receives

### Schema identity

A schema input carries, as values supplied by the caller:

- the composition's identity (`kind`, `namespace`, `version`);
- a digest of the exact schema bytes;
- the grammar version it conforms to (see finding 3);
- its resolved, pinned dependencies (the `atomic_ref` / `aggregate_ref` values
  such as `cic:core:Shape@v0.2.0`, and `base`), each with its own digest.

An input with an unresolved or unpinned reference is rejected, not resolved.

### Nodes

Field descriptors live in the surfaces' `nodes` lists (`config_surface`,
`state_surface`, and so on). The contract needs, per node:

| Information | From | Notes |
|---|---|---|
| `name` | node | field name |
| `shape_type` | node | one of the five |
| `role` | node | see Role below |
| presence | `mandatory` / `optional` | one axis; neither written means mandatory |
| `nullable` | node | default `false` |
| `default` | node | invalid with `mandatory` (shape.yaml) |
| contracts | `contract` | passed through; not evaluated here |

### Structure per Shape type

| `shape_type` | Required companion | Forbidden companion | What the library needs |
|---|---|---|---|
| `scalar` | `scalar_type` (`string`, `integer`, `boolean`, `number`, `bytes`) | `fields`, `item_fields`, `cases` | value domain |
| `composite` | `fields` (≥ 1) | `scalar_type`, `item_fields`, `cases` | child field descriptors |
| `collection` / `list` | `item_fields` (≥ 1) **or** `item_choice.cases` | `fields`, `cases` | element fields, key, `ordered`, `min_items`, `max_items` |
| `collection` / `set` | `item_scalar_type` | key, `item_fields` | element scalar type |
| `choice` | `cases` (≥ 2) | `scalar_type`, `fields`, `item_fields` | case names and their fields |
| `opaque` | none | every structural companion | nothing below this node |

(Source: grammar §2.1 table, §2.3, §2.6.)

### List identity

Every list has a key. Derived from the grammar §2.3:

- exactly one `item_fields` entry with `role: key`: the key is that field;
- two or more with `role: key`: `item_key: [name, ...]` is required and gives the
  order of the composite key; every name in it must be an `item_fields` entry
  with `role: key`;
- none: the schema is invalid;
- `item_choice`: the same rule applies per case (`key: [...]` on the case).

Two cases of one `choice` / `item_choice` must not share a name, and two
`item_choice` cases must not resolve to the same key field set (grammar C17).

A set has no key. The library takes the key as given by the schema and never
guesses a list key from values.

### Policy and Role

- PolicySurface is passed through as given: `default_access`, `default_modify`,
  `surface_rules`, `inherit_policy`. Resolving inheritance is a later phase
  (P3); this ADR only fixes that the library receives these as input.
- Role has three axes in `role.yaml`: the config / state / operational axis,
  the structural axis (`key`, `reference`) and the lifecycle axis (`derived`,
  `volatile`). The library receives the declared value per node and does not
  interpret it beyond what later ADRs decide.

## Primitive positions and the recursion boundary

`access.yaml` describes the Access atom as wrapping every Shape value. This ADR
takes that literally:

- **every field descriptor's value is one primitive position**;
- recursion follows the schema, never the YAML shape: into `fields` for a
  `composite`, into `item_fields` (or the matching case's `fields`) for a list
  element, into the matching case's `fields` for a `choice`;
- **a list element is not a primitive position.** The collection field is one
  position; the element is a structural record, and the fields of that record
  (`item_fields` or `item_choice.cases[].fields`) are positions. An element gets
  no Access wrapper of its own unless a later schema declares a separate Shape
  position for it;
- **a `set` element is not a position either.** A `set` has no field
  descriptor for its elements; the set field is the position and its value is a
  list of scalars;
- an `opaque` node is a position and its content is **not** recursed.

Example (expanded wrappers omitted for the fields):

```yaml
vlans:                       # one position (collection / list)
  - ref: "cic:network:..."   # element: structural record, not a position
  - vlan_id: 100             # ref / vlan_id / vlan_mode: positions
    vlan_mode: trunk
```

Mapping of ADR 0001's test positions onto Shape:

| ADR 0001 position | Shape |
|---|---|
| `scalar` | a `scalar` field |
| `{structured: [...]}` | a `composite` field |
| `{list_of: ...}` | a `collection` / `list` field (its record fields are positions of their own kind) |
| `any` | an `opaque` field |

A `collection` / `set` field is a position whose value is a list of scalars.

## Instance documents (proposal, see finding 5)

For a given schema, an authored document maps names to values following the
descriptor tree: a `composite` value is a map of its `fields`; a `list` value is
a list of records of `item_fields` (or of the matching `item_choice` case); a
`set` value is a list of scalars; a `scalar` is a scalar; an `opaque` value is
anything.

**Choosing a case.** A `choice` or `item_choice` selects its case implicitly,
by the presence of the declared fields. This is what the `devel` grammar already
fixes (§2.3, "Diszkrimináció: implicit, mezőjelenlét alapján"), and no new
`type` / `case` key is introduced in the user's YAML. The library evaluates
every case and accepts exactly one matching case; **zero matches or more than
one is an explicit error.** A case matches when every key of the record is a declared field of the case
and every mandatory field of the case, key fields included, is present. Matching
runs on the raw authored structure, before any default is applied. Selecting a
case is not validating the record: the selected case's values, types and
contracts are checked afterwards. The grammar itself says that mutual exclusion
of cases is the schema author's responsibility and that its rule C17 does not
prove it in general; the instance-level error above is how the library reports a
schema whose cases overlap on a given record.

**Choosing the surface.** The surface (`config_surface`, `state_surface`, ...)
is supplied by the caller in the processing context, not written in the user's
YAML:

```
schema_ref, schema_digest, grammar commit and digest,
surface = config_surface, document = authored YAML
```

The first instance-document contract allows two processing targets, always named
by the caller and never by the YAML:

| Surface | Document |
|---|---|
| `config_surface` | authored intent, the desired configuration |
| `state_surface` | observed state coming from the adapter |

They may belong to the same schema and differ in validation and defaulting
rules. No observation is ever produced from a schema default for
`state_surface`. `policy_surface` is part of the resolution context, not an
intent document. `operation_surface` input/output structures may later become a
separate processing target; they are not generic intent documents.

## Exit criterion

A Rust developer and a Go developer, working only from this document and the
same pinned schema, determine the same primitive positions, child structures,
list identities and rejections.

## Corpus and licensing

The real-schema fixtures are pinned by reference, not copied:

| Item | Pin |
|---|---|
| `cic-schema-registry` | commit `6db77d4dca4ce3af3be66b3a7a85404fd0b63684` |
| file | `general/network/network-interface/network-interface.yaml` |
| sha256 | `e338815ca93c40a656ce440082ddfcda08023a24aa05ce526acdbce466da7ca3` |
| `cic-primitives` grammar | `devel` at `5a26bd8810cce0d614c2d0bb2e8c2d7477390cfd` |

`cic-schema-registry` and `cic-primitives` are licensed CC BY-NC-SA 4.0; this
repository is Apache-2.0. Placing an unmodified file of another licence in this
repository is not automatically forbidden, but the file keeps its own licence
and does not become Apache-2.0, and fetching a file at test time does not remove
the NonCommercial condition from that use. The choice here, a technical
compliance choice and not legal advice:

- the Apache-2.0 corpus consists primarily of synthetic fixtures written in
  this repository;
- testing against the real CIC schema uses a pinned, SHA-256-verified file from
  its own source, and the CC licence of that file stays documented next to the
  pin;
- the cleanest route is an explicit Apache-2.0 (or dual-licence) grant by the
  rightsholder for the specific fixtures; the `owner` field in the
  schemas' metadata does not by itself prove the complete rights, so it is not
  taken as a basis for relicensing; the grant, if given, is made explicitly.

Coverage of the real schema, with the lines in the pinned file:

| Required case | Source |
|---|---|
| simple scalar | `type` (`shape_type: scalar`, `mandatory`) |
| nested composite | `ip` (`composite` with `fields`) |
| keyed list | `vlans` (`list` with `item_choice`, keys `[ref]` / `[vlan_id]`) |
| set | `ip.addresses` (`collection_variant: set`) |
| opaque | the tunnel `ip` field (`shape_type: opaque`) |
| choice | **none in the registry** (finding 4); synthetic fixture from the grammar |

## Not decided here

`null` / absent / intentionally empty; the type of the symbolic `inherit`;
whether `conformance` is user-writable; path grammar; diff output; error codes;
branch-coverage measurement. This ADR may name the schema information they need
and nothing more.

## Decisions proposed in this ADR (from the review of PR 5)

1. A list element is not a primitive position; its fields are. A `set` element
   is not a position.
2. A case is chosen implicitly by field presence; zero or several matches is an
   error. The surface comes from the caller's context.
3. For P0 the library is bound to an immutable grammar commit and digest carried
   in the schema input, never to the moving `devel` branch. The relation between
   the current `network-interface.yaml` and the `devel` grammar is a **temporary
   P0 compatibility profile, not a stable release contract**; a stable release
   needs the `cic-primitives` versioning contradiction (finding 3) resolved.
4. Fixture licensing as above.

## Grammar identity (decided at the P0 acceptance review; implementation pending)

The grammar identity is the immutable `cic-primitives` commit **plus a file manifest**.
This decision is binding; this ADR stays `proposed` until it is implemented and checked
(see "Conditions to acceptance").

- The manifest lists exactly the files the library reads. Each entry is the SHA-256 of
  the **raw Git blob bytes** of that file at the pinned commit. There is no YAML
  normalization and no line-ending or whitespace processing before hashing.
- The manifest text has one line per file, `<sha256-hex>` two spaces `<path>` and `\n`
  (the `sha256sum` format, LF line ends), sorted by the UTF-8 bytes of the path. The
  manifest digest is the SHA-256 of that text. It is reproducible with
  `git show <commit>:<path> | sha256sum`.
- The file set is part of the identity: adding or removing a file changes the digest.
- The starting file set, seven files of `cic-primitives`:
  `schemas/atomic/access.yaml`, `schemas/atomic/shape.yaml`, `schemas/atomic/role.yaml`,
  `schemas/aggregate/policy-surface.yaml`,
  `proposals/atom-grammar/instance-grammar.schema.yaml`,
  `proposals/atom-grammar/check_grammar.py` and `proposals/atom-grammar/README.md`.
- **The README is in the manifest** (owner decision). Its section 2 is the normative
  description of the Shape structure that the schema file and the checker build on and
  that this ADR names as a source. A typo in it changes the identity; that is accepted
  deliberately. It may leave the manifest only through a new, versioned grammar-identity
  contract, once every normative rule exists in machine-readable form.
- The digest cross-checks the commit and does not replace it. It cannot detect a grammar
  that is simply the wrong version (the `item_choice` situation above).
- A mismatch between the supplied digest and the content is an error: the context error
  `GRAMMAR_DIGEST_MISMATCH` (phase `validate`, no source anchor, stable), in the ADR 0007
  registry, with the vectors of `corpus/grammar`.

### Conditions to acceptance

Implemented in the change that follows the acceptance record; the status stays `proposed`
until the owner has reviewed it.

1. `corpus/pins.yaml` carries the seven-file manifest and its digest, with a structural
   rule and a seeded fault. *Done:* the pin lists the seven paths with their SHA-256 and
   `manifest_sha256`; the pin name carries the identity (`<repo>-<commit7>-<manifest8>`),
   so re-pinning renames it and breaks every reference until migrated on purpose; `make
   corpus` checks the structure, the exact file set, the order by path bytes and the
   recomputed digest offline (`PIN_INVALID`, `PIN_DIGEST`), each with seeded faults.
2. The **full** manifest is reproduced in CI by the implementing change; abbreviated
   hashes quoted in review documents are not authoritative. *Done:* the `corpus`
   workflow reads the pinned commit from `pins.yaml`, checks out
   `CentralInfraCore/cic-primitives` at exactly that commit and runs
   `tools/grammar_identity.py verify`, which recomputes every hash from the Git blob bytes
   and the manifest digest. The step is unconditional and mandatory: a failed download, a
   missing file, a different commit or a different byte is red, and `make corpus` itself
   fails if the workflow lacks the step (`UPSTREAM_VERIFY`). Nothing from upstream is
   executed, only its bytes are read.
3. The verifier checks that the digested file set is exactly the manifest. *Done:* the
   accepted set is a constant of the verifier and of the offline gate.
4. The temporary compatibility profile for `item_choice` stays labelled.

## Open questions

1. Whether and when `operation_surface` becomes a processing target.
2. The explicit rights grant for fixtures, if one is wanted. It is not a
   precondition for this ADR, because no file of the source repositories is
   copied into this repository.
