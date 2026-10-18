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
takes that literally and proposes:

- **every field descriptor's value is one primitive position;**
- recursion follows the schema, never the YAML shape: into `fields` for a
  `composite`, into `item_fields` (or the matching case) for a list element,
  into `cases` for a `choice`;
- a `set` element and a `scalar` have no children;
- an `opaque` node is a primitive position, its content is **not** recursed.

Mapping of ADR 0001's test positions onto Shape:

| ADR 0001 position | Shape |
|---|---|
| `scalar` | `scalar` and `set` elements |
| `{structured: [...]}` | `composite` |
| `{list_of: ...}` | `collection` / `list` |
| `any` | `opaque` |

Open: whether a list **element** is itself a primitive position or only its
fields are. The proposal here is that the collection field is one position and
the element's fields are positions, and the element itself is not wrapped. The
owner decides.

## Instance documents (proposal, see finding 5)

For a given schema, an authored document maps names to values following the
descriptor tree: a `composite` value is a map of its `fields`; a `list` value is
a list of maps of `item_fields` (or of the matching `item_choice` case); a `set`
value is a list of scalars; a `scalar` is a scalar; an `opaque` value is
anything. How a `choice` selects its case in an instance, and which surface a
document addresses, are open and need an owner decision or a source in the
corpus.

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
repository is Apache-2.0. Vendoring their files into this repository would carry
that licence with them, so the corpus stores the pin and fetches the file for
the test, plus minimal synthetic fixtures written here. Confirm this reading of
the licence before any file is copied.

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

## Open questions

1. A list element: a primitive position itself, or only its fields?
2. Instance document: how a `choice` selects its case; which surface a document
   addresses.
3. The grammar version: pin the library to the unreleased `devel` grammar
   (`item_choice`), or wait for it to be released in `cic-primitives`?
4. Licensing of fixtures (see above).
