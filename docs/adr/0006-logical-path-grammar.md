# ADR 0006: Logical path grammar (draft)

Status: **proposed, not approved.** Addresses issue #3 point 2. It defines the
logical path model and one canonical text form. It does **not** define diff
operations, final error codes or source positions (the next P0 ADR), authorization
or inheritance resolution. The error codes used in the corpus are provisional.

## Principle

A logical path identifies a location in a schema-interpreted document. It must not
depend on YAML serialization style, mapping order, list order, the Access short or
expanded representation, or a runtime guess. The caller supplies the pinned schema,
the grammar and the surface (`config_surface` / `state_surface`, ADR 0002); a path is
relative to that surface and never names it.

The one invariant everything else serves: **the same logical object has the same
path when the YAML field order, the list order or the Access representation
changes.** The later diff relies on stable object identity, not on serialization
side effects.

## Internal model

A path is a root plus a sequence of typed segments:

- `Field(name)`: a field, identified by its exact name;
- `ListItem(components)`: one element of a list, identified by key components, each
  a field name and a typed value (`string` or `integer`).

A `ListItem` directly follows the `Field` that names the list. The string form is a
serialization of this model, not the model itself. An item may be addressed as a
structural location, but it does not become an Access-wrapped primitive position;
its declared fields stay the positions (ADR 0002).

## Canonical text

```
path      = "$" { "." name | selector }       ; a selector directly follows a name
selector  = "[{" component { "," component } "}]"
component = name "=" ( string | integer )
```

| Logical target | Path |
|---|---|
| root (the surface itself) | `$` |
| field | `$.enabled` |
| nested field | `$.ip.addresses` |
| field named `interface.name` | `$.interface\.name` |
| field named `weird\name` | `$.weird\\name` |
| integer-keyed item | `$.vlans[{vlan_id=100}].vlan_mode` |
| string-keyed item | `$.logical_interfaces[{name="eth0.100"}].ip.addresses` |
| composite-keyed item | `$.nics[{name="nic-0",zone="eu"}].subnet` |
| `item_choice`, referenced case | `$.vlans[{ref="cic:network:vlan-100"}]` |

### Names

- One escape set serves path names and selector component names: a backslash before
  any of `\ . [ ] { } = ,` stands for that character. Any other escape, or a
  trailing backslash, is an error. (This extends the first draft, which listed
  `. \ [ ]`; the selector punctuation needs the same treatment or a component name
  could not be written back unambiguously.)
- An unescaped character from that set in the wrong place is a syntax error.
- A name is non-empty. Control characters (U+0000 to U+001F, U+007F) cannot be part
  of a name and are an error, never escaped. Every other Unicode scalar, spaces
  included, is a literal name character.
- No normalization and no case folding. The scalar sequence is kept as written.

### Values

- A string value is a double-quoted JSON string with exactly one writing:
  `\"` and `\\`, the short escapes `\b \f \n \r \t`, `\u00xx` (lowercase hex) for the
  other control characters, everything else literal UTF-8 (also U+007F and
  non-ASCII). `\/` and any other `\u` escape are valid JSON but not canonical.
- An integer is `0` or `-?[1-9][0-9]*`, within signed 64 bits. A quoted value is a
  string and a bare value an integer; which one a key needs comes from the schema.
- No whitespace is emitted, and none is accepted outside a name.

### Strict parse

The parser accepts only canonical text, so `format(parse(t)) == t` for every
accepted `t`, and `parse(format(p)) == p` for every representable `p`. This
replaces the first draft's separate "normalize" direction: a text with a valid
reading that is not the canonical writing (leading zero, `+7`, `-0`, a needless
`A`, uppercase hex) is **rejected** as non-canonical, not silently rewritten.
Two spellings of one path would defeat byte-identical diffs.

Lexical parsing produces the segment list and knows nothing of the schema. It
rejects: a missing root, empty or unescaped names, bad escapes, an index selector
such as `[0]`, a malformed selector, duplicate component names, non-canonical
writing, an integer outside 64 bits. Everything below needs the schema.

## Resolution against the schema

Identity comes only from the pinned Shape schema (ADR 0002, "List identity"):

- one `role: key` field is the key; several need `item_key`, whose order is the
  component order;
- `item_choice` has a key per case. The selector's component names pick the case;
  the grammar guarantees that two cases never share a key field set (C17), so the
  choice is unique and no record matching is needed to resolve a path;
- `binding_surface.addresses[].key_fields` is never list identity;
- an index never substitutes for a key;
- a `set` has no element selector: the collection field is addressable, an element
  is not;
- `opaque` is one position: the field is addressable, nothing below it is.

Resolution fails, explicitly, for: an unknown field, descent through a scalar, set
or opaque, a selector on something that is not a list, a list used without a
selector, a selector whose components are missing, extra, unknown or not in the
declared order, a component of the wrong kind (a quoted value for an integer key
and the reverse), and a key type other than `string` or `integer`.

## Building a path from a document

Constructing the path of an element of a document uses the ADR 0002 record
matching: a record picks the one `item_choice` case whose declared fields contain
all of its keys; zero or several is an error. Each key component must be present,
of the key's type, with an Access wrapper looked through (ADR 0001 recognition). A
second element with the same identity is an error and is never mapped to an index.
Component order is the schema's, not the document's.

## Path versus Access

`$.mtu` is the same path whether `mtu` is written short or expanded. The Access keys
`value access modify inherit default_injection conformance` are not path segments.
A field that is itself called `value` is an ordinary field `$.value`. How a later
phase addresses Access metadata (a separate typed component beside the path) is
**not decided here.**

## Measured against the pinned corpus

Registry `cic-schema-registry` at `6db77d4`, current (non-versioned) schemas,
throwaway script walking `item_fields` / `item_choice` (not committed):

| Scope | Lists / cases | Key shape |
|---|---|---|
| `network-interface.yaml` (the pin) | 9 | all single-component; 7 `string`, 2 `integer`; `vlans` and `vxlans` are `item_choice` (`ref` string, `vlan_id` / `vni` integer) |
| `general/**` (Shape format) | 38 | 37 single-component (35 `string`, 2 `integer`), 1 list without a key |
| `standards/yang/**` (older YANG format) | 11 | 1 composite (`ietf-lldp` `chassis_id`, `port_id`), 4 `string`, 4 prefix types, 2 without a key; no Shape `scalar_type` |

- Neither `item_key` nor any boolean, number or bytes key occurs in the Shape format.
  The only observed key types are `string` and `integer`, so only those have an
  encoding. Other key types are an explicit error, not a guessed encoding.
- All 268 field names in `general/**` match `[a-z][a-z0-9_]*`. The escaping exists
  for generality, not because the corpus needs it.
- Composite keys are therefore specified (ADR 0002) and exercised only by synthetic
  fixtures; the one real composite is in the older format.

## Consequences

- Item identity is case-local. A VLAN written as `{ref=...}` and the same VLAN
  written inline as `{vlan_id=...}` are two paths; the library does not relate them.
- A selector written `{name="a", zone="b"}` (space after the comma) is not rejected
  lexically, because a space is a legal name character: it reads as a component
  named ` zone` and fails at resolution as a mismatch. Accepted; the alternative is
  to forbid spaces in names.
- Integer keys beyond signed 64 bits are rejected until a numeric contract exists.

## Relation to the engine

`cic-primitive-engine` writes paths like `/nics/{name=nic-0,zone=eu}/subnet`. That is
an engine convention, not this grammar: it cannot represent every string identity (no
escaping, no string/integer distinction). The library never reinterprets such a path.
A later conversion is an explicit, checked adapter. No lossy or silent fallback.

## Corpus

`corpus/paths/vectors.yaml` (ADR 0005 envelope, extended by the amendment in that
ADR) carries 123 cases: `path_parse`, `path_format`, `path_resolve`, `path_of` and
`path_stability`. Every positive `path_parse` case is also a format case. The
vectors cover root and nested fields, literal dots, backslashes, brackets and
selector punctuation in names, string, integer and composite keys, delimiters and
control characters in key values, `item_choice` case-local keys, a field called
`value`, stability under list reordering, mapping reordering and short versus
expanded Access, and the negative cases listed above. Expected results were
cross-checked against a throwaway model, not committed, so the vectors do not
depend on one implementation. The structural gate checks the new kinds; whether any
implementation produces these results is for the P1 runners.

## Not decided here

- Diff operations and conflicts (P0 point 5); error taxonomy and positions (point 6).
- Path syntax through a plain `choice` (no real schema uses one, ADR 0002 finding 4).
- Boolean, number and bytes key encodings, and integers beyond 64 bits.
- How Access components are addressed.
- Conversion from the engine's path syntax.
- Source-preserving editing (P3b).
