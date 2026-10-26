# P1: the implemented slices, their supported subset and their quality gates

Status: P1 (parse/recognize/emit, presence and schema-bound validation, list-element identity, the text of logical paths, the reading of the
`access-policy/1` values) is done, and the later phases extend the same document: `expand` / `collapse` (P2), and from P3 the reading of
`policy_surface` and the Role, the schema defaults (`ApplyDefaults`) and `normalize` / `denormalize`; see the bullets of "Schema-bound
validation". In Rust (`rust/`) and Go (`go/`), one shared corpus. It is implementation, not a decision: it follows the accepted ADRs and
re-opens none of them. `PolicyResolution` (P3, fourth unit, `docs/P3-UNIT4-POLICY-RESOLUTION.md`) is implemented: see its bullet below.

## What exists

| Function | Rust | Go | What it does |
|---|---|---|---|
| parse | `parse`, `parse_with` | `Parse`, `ParseWith` | One YAML document to a `Node` tree with ADR 0007 source positions; `DUPLICATE_KEY` (parse phase) |
| recognize / validate | `recognize` | `Recognize` | ADR 0001 recognition at a synthetic position, then the position validation of ADR 0003/0007 (`MAP_AT_SCALAR_POSITION`, `UNKNOWN_FIELD`, `EXPANDED_WITH_EXTRA_KEY`) |
| emit | `emit` | `Emit` | Deterministic block-style YAML; the text always parses back to the same value |
| access-policy/1 reading | `read_mode`, `read_rule`, `bind_grammar`, `pinned_grammar_version`, `is_cert_pattern` | `ReadMode`, `ReadRule`, `BindGrammar`, `PinnedGrammarVersion`, `IsCertPattern` | `InheritanceMode` in grammar 1 or 2, an `access`/`modify` value, the grammar version binding derived from the verified pinned schema (see below) |
| list element identity | `path_of` | `PathOf` | The canonical ADR 0006 path of an addressed node; a list element named by its declared key (see "List elements") |
| schema compile | `compile_schema` | `CompileSchema` | A pinned CIC schema (bytes + SHA-256 + grammar pin + surface) to the Shape tree `validate` reads; every refusal named by schema path (see "Schema-bound validation") |
| expand / collapse (P2) | `expand`, `collapse` | `Expand`, `Collapse` | Every primitive position wrapped with the symbolic policy and back, with the provenance of how each was written (`docs/P2-EXPAND-COLLAPSE.md`) |
| defaults (P3) | `apply_defaults` | `ApplyDefaults` | The declared default of every absent optional field of a short document, with the origin of each position (see the bullets in "Schema-bound validation") |
| normalize (P3) | `normalize`, `renormalize`, `denormalize` | `Normalize`, `Renormalize`, `Denormalize` | `expand`, then the defaults inserted into the expanded tree, the origin kept beside the object; `denormalize` gives the authored document back (`docs/P3-UNIT3-COMPOSITION.md`) |
| presence validation | `validate` | `Validate` | Absent / null / value against a Shape: `mandatory`, `optional`, `nullable`, scalar type, `min_items`, composite fields; every error with `logical_path`; default *candidates* (see below) |

The expanded key set `A` is an argument, never a constant of the library. The input is never
modified (a test per language checks it).

The machine response of `recognize` is the ADR 0007 one: each violation carries `code`, `phase`,
`source_location` and the canonical ADR 0006 `logical_path`, every validation error is reported,
and they are ordered by path, then code. The path is left out where ADR 0007 leaves it out: the
parse phase, a single position with no structure around it, a name that cannot be written (empty
or holding U+0000 to U+001F or U+007F), and a list element, whose identity the synthetic schema
does not declare. Only those path-less errors cannot be told apart, so only they collapse to one
per code.

## Supported subset

`parse` reads: one document; block and flow collections; quoted and block scalars (strings); plain
scalars `null`, `~`, empty, `true`/`false` (`true`, `True`, `TRUE`, and the same for `false`),
decimal integers that fit 64 bits (`+`/`-` allowed); every other plain scalar is a string. Mapping
keys are the text of a scalar. `emit` writes keys longer than 512 characters in the explicit
`? key` form, because YAML limits an implicit key to 1024.

## Presence validation (ADR 0003, ADR 0007)

`validate(node, shape, A)` takes `Option<&Node>` / `*Node`: **nil is a field that is absent, `Null` is a field
that is present with the value null.** They are different states with different answers.

| Declared | Absent | `null` (or an empty scalar) | Other value |
|---|---|---|---|
| `mandatory`, not nullable | `MISSING_MANDATORY` | `NULL_NOT_ALLOWED` | by type |
| `mandatory`, `nullable` | `MISSING_MANDATORY` | valid | by type |
| `optional`, not nullable | valid, stays absent | `NULL_NOT_ALLOWED` | by type |
| `optional`, `nullable` | valid, stays absent | valid | by type |

* **A default is a candidate and nothing more.** An absent optional field that declares a default is listed
  in `default_candidates` (`$` for the root, otherwise the canonical path). Nothing is substituted, no Role
  eligibility is decided (P3), and `null`, `""`, `0` and `false` never trigger it.
* **Types.** `string`, `integer`, `boolean` scalars; a list with `min_items` (inclusive); a composite with
  `fields`; an opaque field takes any value. A mapping where a scalar is declared is
  `MAP_AT_SCALAR_POSITION`. An unknown key of a composite is `UNKNOWN_FIELD`.
* **Anchors (ADR 0007).** A missing mandatory field is anchored at its *parent* mapping (an absent field has
  no text); an explicit `null` or `~` at the *value*; an empty scalar (`a:`), which has no text of its own, at
  the *key*; `MIN_ITEMS_NOT_MET` at the sequence; `UNKNOWN_FIELD` at the key.
* **Response.** Every error of the phase, ordered by canonical `logical_path` segment by segment (so `$.a.x`
  comes before `$.a b`), then code. Recognition (ADR 0001) runs first at every node.
* The fixture notation `schema.synthetic.shape` is bound in the corpus runners to the real Shape members
  (`shape_type`, `scalar_type`, `mandatory`/`optional`, `nullable`, `default`, `fields`, `min_items`); the
  library takes those members and defines no schema of its own.
* Out of scope on purpose: `state_surface`. A missing observation there is a coverage fact (ADR 0003 rule 8),
  not an authoring-validation error and not the observation state `absent`; the library does not decide it.

## Access reading (corpus kind `validate`, `slot`; ADR 0004, cic-primitives D-018)

The normative source is the pinned `proposals/atom-grammar/P0-4-ACCESS-GRAMMAR.md` and `schemas/atomic/access.yaml`
(`corpus/pins.yaml`, `cic-primitives-73ae469-6a188078`). The module reads and checks values; it resolves
nothing (P3), decides no permission and applies no default.

* **Grammar version binding is fail-closed.** The version is not a document key and has no default. It is the
  `spec.access_grammar.version` of the pinned schema, derived **after** the SHA-256 of the schema bytes matched the
  pin (`GRAMMAR_DIGEST_MISMATCH` otherwise), and the processing context must supply the same integer.
  Missing is `GRAMMAR_VERSION_MISSING` (checked first), a value that is not a supported integer (`"1"`, `true`, `3`)
  is `UNKNOWN_GRAMMAR_VERSION`, a difference is `GRAMMAR_VERSION_CONFLICT`. One `Grammar` value binds a whole
  document. The corpus runners read `access.yaml` from a checkout of `cic-primitives` at the pinned commit
  (`CIC_PRIMITIVES_ROOT`) and **fail**, not skip, when it is absent or differs.
* **`InheritanceMode`.** Grammar 1: boolean `true`, boolean `false` and integer `0` are three distinct, losslessly
  represented values (`inherit`, `no-inherit`, `reset`); `1`, `"true"`, `"0"`, `null`, a list or a mapping is
  `INVALID_INHERIT`; an enum name is `INHERIT_ENUM_IN_GRAMMAR_1`. Grammar 2: the enum names; a legacy spelling is
  `LEGACY_INHERIT_IN_GRAMMAR_2`. No conversion between versions, and writing a mode back gives the spelling that was
  read (a bijection per version, tested). **Fail-closed in Go as well as Rust:** a `Grammar` that was not bound to a supported version (the zero value or any other) reads nothing (`UNKNOWN_GRAMMAR_VERSION`, the upstream code) and writes nothing; only the three states can be written, and an invalid or unknown `Mode` gives no Node and an error; the set of supported versions is returned as a copy, so a caller cannot extend it. Rust enforces the same by type: a `Grammar` exists only after `bind_grammar` succeeds, `Mode` is a closed enum, and the supported set is a constant.
* **`access` / `modify`.** The symbol `inherit` is not a CertPattern list; `[]` is an explicit "nobody" and never the
  symbol; `[inherit]` is `INVALID_CERT_PATTERN`; `null`, a bare pattern string, a boolean or a mapping is
  `INVALID_ACCESS_VALUE`. A CertPattern is the upstream expression `attr=value` pairs joined by `,`
  (`attr` = `[A-Za-z][A-Za-z0-9.-]*`, a value that does not start with `=`, `,` or whitespace and holds no `=` or `,`);
  its whitespace class is Python's `\s` (Unicode White_Space plus U+001C to U+001F), and both languages are checked
  against that expression as an oracle by property test and fuzz target.
* Violations are anchored at the value (registry anchor `value`), with phase `validate`, and carry no `logical_path`
  (a single position).

### Where each code comes from

| Code | In `corpus/codes.yaml` | Traced to |
|---|---|---|
| `INVALID_INHERIT`, `INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN` | yes (provisional) | ADR 0004 and the pinned specification, "Value rules" and "Spelling and versions" |
| `INHERIT_ENUM_IN_GRAMMAR_1`, `LEGACY_INHERIT_IN_GRAMMAR_2` | **no** | pinned specification, "Spelling and versions" |
| `GRAMMAR_VERSION_MISSING`, `UNKNOWN_GRAMMAR_VERSION`, `GRAMMAR_VERSION_CONFLICT` | **no** | pinned specification, "Grammar version binding" |
| `GRAMMAR_DIGEST_MISMATCH` | yes (stable, ADR 0002) | the pinned schema's bytes differ from the pin |

The five upstream codes are emitted unchanged, not renamed or invented. No vector uses them, and the corpus gate
rejects a registry code no case uses, so they cannot enter the registry yet: this is a reported gap (finding 6).

## Schema-bound validation (ADR 0002; `compile_schema` / `CompileSchema`)

The first step from "validate against a hand-built Shape" to "validate an authored document against a CIC schema":

    authored YAML  +  pinned schema (bytes, SHA-256, grammar pin, surface)  ->  validation result

The library fetches nothing, resolves nothing and has no schema meaning of its own. The caller supplies the schema
bytes and the digest they were pinned with; the grammar identity is the pin name of `corpus/pins.yaml`; the surface
is named by the caller, never by the document, and is `config_surface`, the authored intent.

* **Binding order, fail-closed.** `SCHEMA_DIGEST_MISMATCH` (the bytes are not the pinned ones), then
  `GRAMMAR_PIN_UNKNOWN` (the library carries a profile only for the pins it knows; there is no default), then
  `SURFACE_UNSUPPORTED` (anything but `config_surface`). All three are `ErrBinding`. `state_surface` is a processing
  target of ADR 0002 but is **refused here on purpose**: a missing mandatory field on it is a coverage fact, not an
  authoring error (ADR 0003, rule 8), and an observation validated with authoring semantics would be stated wrongly.
* **Grammar profile and `item_choice`.** The one known pin (`cic-primitives-73ae469-6a188078`, `devel`) defines
  `item_choice`; the primitives release v0.2.0 does not, and a schema such as the registry's `network-interface.yaml`
  already uses it while declaring `validatedBy: cic-primitives v0.2.0` (ADR 0002, finding 3). Under a profile without
  the feature the schema is refused with `SCHEMA_FEATURE_NOT_IN_GRAMMAR`; the profile is the pinned contract and
  nothing infers it from the document. The declared `validatedBy` is kept in `declared_validated_by` so the
  difference stays visible. This is the temporary P0 compatibility profile, not a release contract.
* **What is read.** `shape_type` `scalar` (`string`, `integer`, `boolean`), `composite`, `collection` (`list` with
  `item_fields` or `item_choice`; `set` with `item_scalar_type`) and `opaque`; the presence axis, where **only
  `true` is a valid value of `mandatory`/`optional`, both together contradict, and neither means mandatory**;
  `nullable`; `default` (its presence only; invalid on a mandatory field, invalid as `null`); `min_items`; the list
  key rules (exactly one `role: key` field, or several named exactly by `item_key`; per `item_choice` case a `key`
  naming exactly the case's key fields, unique case names, no two cases with one key set, C17).
* **The object-level policy and the base (P3, first unit).** `spec.policy_surface` is read as *declared*, into
  `CompiledSchema.Policy` / `.policy`, and `spec.identity.base` into `.Base` / `.base` (a pinned reference, `""` when the schema
  has none). Read: `default_access` (a list of CertPatterns or the symbol `open`), `default_modify` (a list or the symbol `owner`),
  `surface_rules` (`surface` and `access`/`modify`, each a list or `inherit`; an `inherit` or missing member declares no rule) and
  `inherit_policy` (`InheritanceMode` in the spelling of the pinned grammar: `true | false | 0`). A member the schema does not
  write stays **undeclared**; the system defaults `open` and `owner` are never written in, they are the system layer of a later
  resolution. An absent `policy_surface` and `{}` are legal and transparent. Refused as `SCHEMA_INVALID` with the path of the
  member: a policy that is not a mapping, a member outside PolicySurface or SurfaceRule, the other symbol (`owner` as
  `default_access`, `open` as `default_modify`, `inherit` as a default), an invalid CertPattern or access value, an
  `inherit_policy` in the wrong spelling, a surface rule without a surface, and two entries that declare the same
  (surface, operation) (`SURFACE_RULE_CONFLICT` in the detail). Nothing is evaluated: no requestor, certificate, owner or
  snapshot is looked at, and the Forms library takes no access decision. The shared cases are `testdata/p1/policy.json` (both
  languages), the real `network-space` schema is pinned structurally in `testdata/p1/real-schemas.json`.
* **Role and defaults (P3, second unit; ADR 0011).** The `role` of every node is read: the short forms `config`, `state`,
  `operational`, `key`, `derived`, `volatile` (`reference` has no short form) or the long form, a mapping of the three axes
  (`authority`, `structural`, `lifecycle`). An unknown value or member, a wrong kind, or a combination the atom grammar
  forbids (`derived` or `volatile` with the authority `config`, a key that is derived or volatile) is `SCHEMA_INVALID` at
  `<node>.role...`; an unknown Role is never read as `config`. A `default` is allowed only on a node that takes one: the
  authority is `config` (written, or by its default), there is no structural value, and the node is optional; on any other
  node it is `SCHEMA_INVALID` at the node. The default must also be a valid value of its own node (type, `min_items`, record
  structure), else `SCHEMA_INVALID` at `<node>.default`: applying it can never make a document invalid. The declared value is kept
  in the `Shape` (`Default` / `default`).
* **Applying the defaults (`ApplyDefaults` / `apply_defaults`).** The authored document is validated first; with any violation,
  or with an input this slice does not support, nothing is defaulted (the violations are returned and there is no document). Then
  the declared default of every **absent** optional field is inserted as a copy, in the short form, after the entries of its
  mapping, in schema order. Never replaced: an explicit `null`, `""`, `0`, `false`, `[]` or `{}`, nor any authored value. The walk
  goes through present composites and the records of declared lists (named by their identity); it never goes into an absent
  composite or list, so a default does not invent structure. A default is a whole value, defaulted nowhere inside. The result
  lists every declared position with its origin, `authored` or `default`, in path order, relative to the input: the defaulted
  positions are exactly `DefaultCandidates` of the validation, and applying the defaults to the result changes nothing and finds
  every value authored. The input is never modified. Composing with `Expand` (the symbolic policy of the inserted position) and the
  effective rules (`PolicyResolution`) are the next units. The shared cases are `testdata/p1/defaults.json`.
* **Normalize and denormalize (P3, third unit; `docs/P3-UNIT3-COMPOSITION.md`).** `Normalize` / `normalize` is `Expand`, then the
  declared default of every absent optional field inserted into the **expanded** tree, as an Access atom like any position (the
  symbolic policy of the bound grammar; `default_injection` stays null, the default is never written there; ADR 0003 rule 6). It
  returns the object, the P2 `Provenance` (only for the positions the author wrote: an inserted one has none) and `ValueOrigins`, a
  side map from the logical path of every declared position to `authored` or `default`. The origin is produced where the default is
  inserted and travels with the object; it is never read back from the values. `Denormalize` removes the values whose origin is
  `default`, then collapses with the provenance and the trusted context, so `denormalize(normalize(x)) == x` as a value for every
  document `Expand` accepts: short, expanded or mixed, with explicit policy settings, `null`, and a value that equals its default
  (which stays `authored`). `Renormalize` is the idempotence on the **pair** (object and origins); normalizing the bare object
  reports every inserted default as `authored`, because from then on it is written in it. Like `Expand`, `Normalize` expects a
  document that validates (it refuses an undeclared key and a structure the schema does not allow, but does not report a missing
  mandatory field), and it never inserts a default for a mandatory field, into an absent composite or list, over a present value, or
  below another default. The shared cases are `testdata/p1/normalize.json`; both languages also prove the round trip, the
  provenance of the author's positions and the idempotence on the pair as properties, over random shapes and documents.
* **PolicyResolution (P3, fourth unit; `docs/P3-UNIT4-POLICY-RESOLUTION.md`).** `resolve_rule` finds the effective rule of one
  operation (`access` or `modify`) for a field of an object graph, in the order of the reference (`access_policy.py`, D-018): the
  field's own list, the object's `surface_rules` for the surface, its default, the rule a carrier passes down, the ancestors'
  policy where each step permits it, then the system default (`open` for access, `owner` for modify). The result is data: the kind
  (`list`, `open`, `owner`, `none`, `unknown`), the layer, the `source_path` of what declared it and, for none and unknown, the
  reason. `none` and `unknown` are never permissive, and the empty list is an explicit "nobody". `resolve_policy` /
  `ResolvePolicy` walks a **normalized** object with the compiled schema and resolves every position it holds (the root, the value
  of a composite, every list element by identity); it creates nothing, does not read the origin map and leaves the object alone. A
  position that is not expanded, a field the schema does not declare and an `unknown` path that names no object are refused as
  unsupported. The library has no requestor, owner or certificate: the decision is the Relay's. Both languages run all 86 golden
  vectors of `access-vectors/resolution.yaml` against it, read from the pinned checkout after their SHA-256 matched the pin (the
  reference decision exists only in test code), the shared cases `testdata/p1/resolution.json`, and properties over generated
  object graphs. The pin now lists the twelve grammar files (ADR 0002, Amendment 2).
* **NormalizeWithPolicy / normalize_with_policy (P3 closing round).** `Normalize` followed by `ResolvePolicy` on the normalized object,
  both with the **one** compiled schema, in one operation: the result holds the normalization, the policy resolution and the digest
  of the schema both were made from. The caller cannot pair the two with different schemas through this path; if either step fails
  there is no result. It decides nothing and does not replace validation. `Normalize`, `Renormalize`, `Denormalize` and
  `ResolvePolicy` stay as they are. Shared cases: the ones of `testdata/p1/resolution.json`, run through the composed call and compared
  with the two separate calls.
* **SourceSpan / source_span (P3b step E2; ADR 0012, `docs/P3B-SOURCE-PRESERVING-EDIT.md`).** Read only: `source_span(text, root, path, a)` parses the
  text, finds the position by its logical path (a list element by its declared identity, an expanded Access wrapper looked through to its `value:`), and
  returns the half-open **byte** span of the scalar (a leading BOM counted, the offset of ADR 0007) and its style (`plain`, `single_quoted`,
  `double_quoted`), or a refusal. The refusals of the earlier phases are returned as they are (`parse`, `path_parse`, `path_resolve`); the new
  ones are of the phase `edit`: `EDIT_NOT_A_SCALAR`, `EDIT_NO_SOURCE_VALUE` (anchored at the enclosing structure), `EDIT_BLOCK_SCALAR`,
  `EDIT_FLOW_CONTEXT`, `EDIT_MULTILINE_SCALAR`, and `EDIT_SPAN_MISMATCH`, an internal assertion that is never meant to be reached. The extent is found by a small scanner
  over the supported subset and **verified by reading it back**: the span, parsed alone, must be the value the parser gave for that node, so a scanner defect refuses
  instead of pointing at the wrong bytes. `Node`, `Provenance` and every P1-P3 result are unchanged. It does not call `validate` (P1 `validate` does not read an expanded
  document; the edit of step E3 validates its result). Proved by the 28 `source_span` vectors in both languages (written before the code), the edge cases a vector cannot
  reach (an unspelled value, a node of the wrong kind, a wrapper in a flow collection, the path errors, the scanner's edges), a property over generated documents (the span
  of every scalar reads back to its value; a plain word, a number or a boolean is never refused), a fuzz target with a canary in each language, and 22 seeded defects.
* **EditScalar / edit_scalar (P3b step E3; ADR 0012, `docs/P3B-SOURCE-PRESERVING-EDIT.md`).** `edit_scalar(text, root, path, a, value)` replaces the one scalar of a
  position by a typed value (a string, an integer, a boolean or null; a scalar `Node`) and leaves every other byte alone; it returns the text, the new span and whether
  anything changed, or a refusal and no text. The order is parse, validate (an expanded Access wrapper is read as its `value`; the violations are returned as they are),
  the path operations, then the `edit` phase in the fixed order of ADR 0012: the refusals of the span (`EDIT_NOT_A_SCALAR`, `EDIT_NO_SOURCE_VALUE`,
  `EDIT_BLOCK_SCALAR`, `EDIT_FLOW_CONTEXT`, `EDIT_MULTILINE_SCALAR`), `EDIT_VALUE_NOT_REPRESENTABLE` (a string with a control character, a line or paragraph separator;
  reading is wider than writing), `EDIT_TYPE_MISMATCH` (the kind of the value is not the scalar type of the position; null only where it is nullable; a collection is no scalar),
  `EDIT_STYLE_CONFLICT` (the style of the token cannot carry the value; there is no requoting). The value is written in the **canonical spelling** in the style the scalar
  has (decimal integers, `true`/`false`, `null`; a single-quoted `'` is doubled, a double-quoted `\` and `"` are escaped; a plain string must be plain-safe: not empty, no
  leading or trailing space, no leading indicator, no `: ` or ` #`, no final colon, and read back by the parser as the same string); a position that already holds the value keeps
  its token byte for byte. On every call, after the text is built: the bytes outside the span are the original's, the result parses and validates (`EDIT_INVALID_RESULT`
  otherwise, for example two list elements that now share one identity), and it is the old tree with that one scalar replaced (`EDIT_SPAN_MISMATCH`, an internal assertion).
  `edit_parsed` is the case of a document supplied as a parsed value: after the document and the path are accepted, `EDIT_NO_SOURCE_TEXT`. No `Node`, `Provenance` or earlier
  result changes. Proved by the 44 `edit` vectors in both languages (written before the code, compared as bytes), the edge cases a vector cannot reach (null, the styles at their
  edges, the plain-safe rule, the earlier phases, the parsed value), a property over generated documents (the untouched bytes, the re-parse, the no-op, the commutation of
  independent edits, edit and back), a fuzz target with a canary in each language, and 24 seeded defects. Step E4 adds the proof from outside (`docs/P3B-SOURCE-PRESERVING-EDIT.md`
  section 12): a Rust-against-Go differential of the six guarantees on 3000 generated inputs (`tools/parity/gen_edit.py`, the `parity_edit_dump` tests, `compare_edit.py`) with a
  self-test that breaks it in 22 ways, planted defects that turn it red (`edit_proof.py`), and ten seeded defects of the internal guarantee check.
* **What is refused, with a code and the path of the schema node.** `SCHEMA_INVALID` (a structure outside ADR 0002),
  `SCHEMA_SHAPE_UNSUPPORTED` (`choice`, `number`, `bytes`, and a well-formed `max_items`: ADR 0003 specifies `min_items` and no
  other count, so a maximum the validator would not check is refused rather than dropped, #88), `SCHEMA_REF_UNPINNED` (a base, identity, aggregate or node
  reference that is not `cic:<namespace>:<Name>@vX.Y.Z`; only the parts the contract reads are checked),
  `SCHEMA_FEATURE_NOT_IN_GRAMMAR`. The error lists **every** refused node of the schema in schema order (the first
  problem of each node), so a schema is judged in one pass.
* **Numbers in the schema (`min_items`, `max_items`).** A non-negative integer is valid; a negative number or a non-integer is
  `SCHEMA_INVALID` (and a valid `max_items` is then `SCHEMA_SHAPE_UNSUPPORTED`, above). There is **no upper bound**: nothing in ADR 0002 or ADR 0003 limits the number, and a very large minimum is not a
  malformed schema, only a requirement no realistic list meets (`MIN_ITEMS_NOT_MET`). Before the P2 work an upper bound of 2^31 was
  enforced; it was removed on purpose (a semantic decision, recorded in #40), not as a side effect of a measurement. The
  implementation limit is `i64::MAX` (Rust: `i64` to `usize`, Go: `int64` to `int`, both lossless on the supported 64-bit
  targets); the ADRs allow larger integers in principle, and 32-bit targets are not supported.
* **Values that come with an error.** A function that fails returns no meaningful value together with its error (`zero[T]()` in Go,
  the `Err` arm in Rust); no caller reads one. This is a statement about the contract, not an optimisation: it keeps a mutation of
  such a value from looking like a behaviour.
* **Passed through, not evaluated.** `contract`, `description`, `role`, `capability` and the other descriptive
  members. A node that declares a `contract` is listed in `unevaluated_contracts`; validation does not check it.
  Dependency digests (ADR 0002 asks each pinned dependency to carry its own) are **not** checked yet: a reference is
  pinned by version only.
* **Validation of what was compiled.** A `set` is a list of scalars of its type (`MIN_ITEMS_NOT_MET`; a wrong element
  type is outside the registry like any wrong type). A list with a declared element structure is validated element by
  element (next section).
* **Codes** (outside `corpus/codes.yaml`, like the upstream binding codes; the shared cases of
  `testdata/p1/schema.json` use them): `SCHEMA_DIGEST_MISMATCH`, `GRAMMAR_PIN_UNKNOWN`, `SURFACE_UNSUPPORTED`,
  `SCHEMA_INVALID`, `SCHEMA_SHAPE_UNSUPPORTED`, `SCHEMA_REF_UNPINNED`, `SCHEMA_FEATURE_NOT_IN_GRAMMAR`.
* **Tests.** `testdata/p1/schema.json` is one file of synthetic cases (compile, then documents) that both languages
  run (`go/schema_test.go`, `rust/tests/schema.rs`). The real registry schemas are checked against
  `testdata/p1/real-schemas.json` (`go/real_schema_test.go`, `rust/tests/real_schema.rs`): the files are
  CC BY-NC-SA 4.0 and not vendored; `CIC_REGISTRY_ROOT` points at a checkout at the pinned commit, the SHA-256 of
  each must equal its pin (`corpus/pins.yaml`, `schemas.*`), and the tests **fail**, not skip, without it (the
  `upstream-pinned` action provides it in CI). Seeded defects (`tools/seeded_defects.py`) show the suites turn red
  on a skipped digest check, a skipped grammar check, an accepted default on a non-optional field, an accepted list
  without a key, an unpinned reference and the number/bytes scalars read as unknown.

### What the real schemas showed

Both at the pinned registry commit `6db77d4`:

* `general/network/network-space/network-space.yaml` (NetworkSpace, the provider-neutral VCN/VPC/VNet space):
  `config_surface` **compiles** (7 fields, no `item_choice`, one declared contract not evaluated) and an authored VCN
  intent is validated against the registry's own definition: `cidr_block` is mandatory; `ipv6_enabled: ~` is
  `NULL_NOT_ALLOWED`, not an absent field; an undeclared `display_name` or `compartment_id` is `UNKNOWN_FIELD` (the
  registry schema has no such config fields, a gap already noted in the VCN pipeline work);
  `secondary_cidr_blocks` is a list keyed by `cidr`: valid by identity, a repeated `cidr` is `PATH_DUPLICATE_IDENTITY`, an element without `cidr` is `PATH_KEY_MISSING`.
* `general/network/network-interface/network-interface.yaml`: `config_surface` is **refused with 22
  `SCHEMA_INVALID` problems**: 22 fields (`stp.*`, `poe.*`, `storm_control.*`, `qinq.*`, ...) declare a `default`
  without saying `optional`. By `shape.yaml` such a field is mandatory, and a mandatory field has no default. The
  library does not guess that `optional` was meant; the schema (a working copy, not a signed release) has to say
  it. The 22 paths are in `testdata/p1/real-schemas.json` and were cross-checked with an independent scan of the
  file. `vlans` and `vxlans` are `item_choice` lists and compile under the `devel` profile.
* A scan of the other unversioned `general/**` working copies at that commit (`config_surface`): `network-space`,
  `subnet`, `dhcp-options`, `dhcp-service`, `kubernetes-node` and `storage-resource` compile. The adapters declare no
  `config_surface` (they are not authored intent; refused as `SCHEMA_INVALID`), `kubernetes-cluster` has two
  defaults without `optional`, and `compute-resource` uses the scalar type `number` (`SCHEMA_SHAPE_UNSUPPORTED`).

## List elements: identity, case selection, paths (ADR 0002, ADR 0006; `path_of`, element validation)

The identity of a list element comes **only** from the pinned schema: the single `role: key` field, `item_key` for a
composite key, the `key` of the `item_choice` case. Never from a field name, never from the position in the list.
A `PathOf` / `path_of` address `[field, index, field, ...]` gives the canonical path, and a list element is named by its
identity: `$.vlans[{vlan_id=100}].vlan_mode`, not `$.vlans[1].vlan_mode`.

* **Key components** are in the declared order (the schema's, not the document's), a string (`name="eth0"`, canonical
  double-quoted JSON: `\"`, `\\`, `\b \f \n \r \t`, lower-case `\u00xx` for the other controls, everything else
  literal) or an integer (`vlan_id=100`). An Access wrapper around a key value is looked through, so the short and
  the expanded form give one identity.
* **`item_choice` case selection** is by field presence on the raw record: a case matches when every key of the record is a
  declared field of the case and every mandatory field of the case, key fields included, is present. Zero matches is
  `PATH_CASE_NONE`, several is `PATH_CASE_AMBIGUOUS`, both at the path of the list. A plain list has one case and no selection, so a
  missing key is `PATH_KEY_MISSING`, not a case error.
* **Key errors** at the path of the list: `PATH_KEY_MISSING`, `PATH_KEY_TYPE` (a string where an integer is declared and
  the reverse; `null` too), `PATH_KEY_UNSUPPORTED` (a boolean, or a non-scalar, key: no encoding, ADR 0006).
* **Duplicates.** Two elements with one identity are `PATH_DUPLICATE_IDENTITY` at the path of the identity, once per
  identity, and nothing is reported below an element that already has an error (ADR 0007). The error is never mapped to an index.
* **Validation.** `Validate` identifies every element, reports the identity errors above, and validates the fields of
  each identified element against its case (presence, `null`, types, `UNKNOWN_FIELD` for a plain list; an unknown
  key of an `item_choice` record is `PATH_CASE_NONE`, the case rule). Errors inside an element are named by its identity and
  ordered as ADR 0007 says: by component name, strings by UTF-8 bytes before integers, integers numerically (`{vlan_id=9}`
  before `{vlan_id=10}`), independent of the list order.
* **Corpus.** The 18 `path_of` and 7 `path_stability` cases of `corpus/paths` run in both languages; the validation of
  elements is in `corpus/lists` and the shared `testdata/p1/schema.json` (list, `item_choice`, composite key in schema order, ambiguity,
  boolean key, canonical key text) and, against the registry's `network-space`, in `testdata/p1/real-schemas.json`.
* **Phase and anchors.** The six identity codes are reported in phase `path_of` by `path_of` and in phase `validate` by
  `Validate` (ADR 0007, "Amendment: the list-element identity codes in `validate`"): the missing key and the case errors are
  anchored at the element's record, a key value of the wrong or an unsupported type at that value, a duplicate at the second
  element with the identity. `corpus/lists/vectors.yaml` carries a negative vector per code and phase, with source locations,
  and runs in both languages.

## The text of logical paths (ADR 0006: `parse_path`, `format_path`, `resolve_path`)

`ParsePath` / `parse_path` accepts only the canonical writing, so `format(parse(t)) == t` for every accepted `t`; a reading that
is valid but not canonical (a leading zero, `+7`, `-0`, a needless `\u` escape, `\/`, an upper-case hex digit) is refused with
`PATH_NONCANONICAL`, not rewritten. Errors carry the code, the phase (`path_parse`, `path_format`, `path_resolve`), the offset
in code points (parse only) and, for resolution, the logical path up to and including the segment at fault; their source
location is `not_applicable`. In the machine response (`Violation`) the offset is `path_offset`: present for every `path_parse`
error, `0` included, and absent for every other phase (ADR 0007), never invented. `resolve_path` resolves against the compiled schema by the declared identity (the key of the
list, or of the `item_choice` case that the component names select) and says what the path addresses: root, scalar,
composite, list, set, opaque or item, and whether that is a primitive position (a field is; the root and a list element are
not). All 100 `path_parse`, `path_format` and `path_resolve` cases of the corpus run in both languages, and the edge cases the
corpus does not spell out (every escape, the ends of the text, control characters, the space) are in
`testdata/p1/pathtext.json`.

## Explicitly not supported (each is an error, never a reinterpretation)

| Input | Answer |
|---|---|
| anchors, aliases, tags, merge keys (`<<`), non-scalar keys, more than one document | `UNSUPPORTED_SYNTAX` |
| plain scalars that look like floats (`1.5`, `.5`, `5.`, `1e3`, `.inf`, `.nan`), hex or octal (`0x1F`, `0o17`), integers with a leading zero, integers beyond 64 bits | `UNSUPPORTED_SYNTAX` |
| the characters U+0085, U+2028, U+2029 anywhere in the text | `UNSUPPORTED_SYNTAX` (see finding 2) |
| control characters (U+0000 to U+0008, U+000B, U+000C, U+000E to U+001F, U+007F), a tab anywhere, a byte order mark after the first character, a directive line (`%...`), a document end marker (`...`), a `---` that is not the first content line (indented ones too; inside a block scalar and, when indented, inside a quoted scalar, `---`, `...` and `%` are text and read as such) | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| a word that starts with `!`, `&` or `*`, or with `?` (outside the emitter's own `? key` form), a `#` right after `]` or `}`, outside quotes, comments and block scalar content | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| a block scalar in a text that does not end with a line break, or one that holds nothing but line breaks | `UNSUPPORTED_SYNTAX` (parser parity, below) |
| an empty implicit mapping key (`: a`, `{: a}`) | `YAML_SYNTAX`: yaml.v3 refuses it, so the Rust parser does too |
| text the YAML library rejects | `YAML_SYNTAX` |
| more than 1 MiB, depth 64 or 100 000 nodes | `LIMIT_EXCEEDED` |
| a scalar where a mapping or a list is declared, a non-record list element, a list inside a record | `UNSUPPORTED_SYNTAX` (the slice has no rule) |
| a value of the wrong type in presence validation (a string where an integer is declared, a list where a mapping is declared, ...) | `UNSUPPORTED_SYNTAX`: the registry has **no code** for it (finding 4) |
| an expanded Access node inside a validated document | `UNSUPPORTED_SYNTAX`: its `value` is validated with expand/collapse (P2) |
| effective permission, `no-inherit` against `reset` semantics, surface rules, system defaults, request context (P3), `conformance` origin (ADR 0010), expand/collapse of Access nodes (now P2, `docs/P2-EXPAND-COLLAPSE.md`), grammar-version migration (`migrate_mode`) | not implemented |
| A list element that is not a record, or an element field of the wrong type | `UNSUPPORTED_SYNTAX` (no code in the registry) |
| An expanded Access node as a key value or any element field | `UNSUPPORTED_SYNTAX` (P2): the key's identity looks through it (`path_of`), but validating its `value` is expand/collapse |
| Shape type `choice`, scalar types `number` and `bytes`, evaluating `contract` | refused at schema compile (`SCHEMA_SHAPE_UNSUPPORTED`); contracts are passed through and listed in `unevaluated_contracts` |

These three codes are **not** in `corpus/codes.yaml`; they mark the boundary of the slice and are
not part of the ADR 0007 machine response. The limits are implementation defaults, not decided
values. Also out of the slice: the other corpus kinds (`validate` 66, `path_*` 125, `diff` 66,
`collapse`, `expand`, `roundtrip`, `basis`, `equality`), the `shape` presence rules and type checks,
the Access vocabulary, source-preserving emit (P3b),
expand/collapse (P2), normalize (P3), diff/patch (P4) and materialize/receipt (P5).

## Corpus coverage: run and `NOT RUN`

Exact: `the_run_and_not_run_accounting_is_exact` (Rust) and `TestTheRunAndNotRunAccountingIsExact` (Go) count every
case of the corpus (479), assert that each is either run or listed here, and compare the counts with `corpus/accounting.yaml`: for every kind of every suite how many cases are **run** and how many are **NOT RUN** (now 438 run + 41 NOT RUN of 479). That file is written by hand, in advance, and never computed from a run; the runners fail on any difference in either direction (a kind that is missing or not listed included), and `tools/corpus_check.py` (rule `ACCOUNTING`) checks that it lists exactly the kinds of the corpus with their number of cases. A new suite or kind is a line appended to it.

| Suite | Kind | Cases | Status |
|---|---|---|---|
| recognition | `recognize` | 30 | **run**, both languages, complete machine response |
| presence | `recognize` | 1 | **run** |
| presence | `validate` (shape) | 29 | **run**, complete machine response, input unchanged |
| inherit | `validate` (slot `inherit_key`, `inherit_policy`, `access_modify`) | 21 | **run**, complete machine response, typed value preserved, grammar bound from the verified pin |
| conformance | `validate` (slot `conformance`) | 16 | NOT RUN (ADR 0010, conformance basis) |
| conformance | `basis` | 16 | NOT RUN (ADR 0010) |
| conformance, presence, recognition | `collapse` | 15 | **run**, both languages (P2, `docs/P2-EXPAND-COLLAPSE.md`); the 4 conformance ones take the ADR 0010 basis as an input |
| inherit, presence | `roundtrip` 9, `expand` 1, `equality` 1 | 11 | **run**, both languages (P2) |
| lists | `validate` (list notation, ADR 0007 amendment) | 8 | **run**, both languages, complete machine response with source locations |
| paths | `path_of` | 18 | **run**, both languages: identity of a list element in a path, errors with their path |
| paths | `path_stability` | 7 | **run**, both languages, every variant gives the one path (or the one error) |
| paths | `path_parse` 57, `path_format` 9, `path_resolve` 34 | 100 | **run**, both languages (`parse_path`, `format_path`, `resolve_path`): the strict text of ADR 0006, offsets in code points, the resolution against the schema |
| diff | `diff` 62, `diff_stability` 4 | 66 | **run**, both languages (P4 steps D1-D3, ADR 0008): the canonical bytes of the result, or the one error with its phase, path and operand; the vectors were hand-derived in P0, before the code, and both runners passed them at the first run of working code |
| edit | `source_span` | 28 | **run**, both languages (P3b step E2, ADR 0012): the byte span and the style of a position's scalar, or the refusal with its code, phase, position and logical path; the vectors were written by hand in step E1, before the code |
| patch | `patch` | 60 | **run**, both languages (P4 step P4-2, ADR 0013): the patched text compared as bytes with the replaced scalars and the spans before and after, or the one error with its code, phase, position and logical path and no text; the vectors were written by hand in P4-1 before any code and passed at the first run of working code |
| edit | `edit` | 44 | **run**, both languages (P3b step E3, ADR 0012): the edited text compared as bytes (or the refusal with its code, phase, position and logical path), and the span of the scalar before and after (`span_before`, `span_after`; `path_after` where the edit changes the address of the scalar) |
| grammar | `grammar_manifest` | 9 | not run by these runners (checked by `make corpus` and `tools/grammar_identity.py`) |

A runner asserts the number it ran (31, 29, 21), so a vector that stops being run is a failure, and a green run cannot
be read as covering the rest.

## What agreement between the two languages is claimed for

* the 31 `recognize`, the 29 shape-based and the 21 inheritance `validate` cases of the corpus: class, `elements`,
  `opaque_value`, `default_candidate`, `stays_absent`, `preserved`, `kind`, `is_inherit` and the complete machine response (code, phase,
  source location, logical path), compared in both languages;
* `testdata/p1/subset.json` and `testdata/p1/access.json`: what `parse` reads and refuses, what `emit` writes, and the
  readings of `InheritanceMode`, `access`/`modify`, CertPatterns and the grammar version binding (hand-written
  expectations, one file each, read by both languages);
* the positions of those inputs: second key, mapping (first key in block style, opening brace in flow
  style), BOM, CRLF, lone CR, non-ASCII, leading line breaks.

* the 531 inputs of `testdata/p1/parity.json` (the shared regression corpus of the parser parity work, below):
  the complete outcome of `parse` (the tree with the position of every node and key, or the contract
  violation, or the refusal) is pinned and read by both languages.

**Not claimed:** general parity of the two parsers. The differential found real differences (below), the
closed ones have a pinned regression case each, and 13 hand-picked open ones (flow-context spellings
that saphyr reads and yaml.v3 refuses, or the reverse) are pinned with both outcomes and a test that
fails when one of them stops being a difference. The error *code* of a refusal may differ
(`YAML_SYNTAX` against `UNSUPPORTED_SYNTAX` is the same refusal for a different reason); that a text is
refused, and what a contract violation says, is compared. The two libraries also have their own
nesting limit and their own wording of a syntax error.

## Parser parity (Rust against Go)

**Stability of the generated inputs (#39).** The fixed-seed run and its allow list depend on the seed list, because the random edits `choice` from it. The seeds are therefore the *pinned* suites named in `tools/parity/gen_inputs.py` (`PINNED_SUITES`); a corpus suite added later (`corpus/lists`) is not a seed of that run but is appended after it, with its own texts and 3 000 mutations from a generator of its own, so adding a suite cannot reshuffle the pinned inputs again. Checked: with `corpus/lists` present the first 30 000 inputs are byte for byte those of the head before it, and the 286 allowed divergences, `changed: 0`, `new: 0` hold on them. The appended inputs found 2 divergences, both the open flow-context class (a `?` inside a plain scalar of a flow mapping), now in the allow list with their minimal forms.

**A new suite takes part in the run (`corpus/edit`, P3b step E1).** `edit` is not in `PINNED_SUITES`, so its texts (and 3000 mutations of them) are added after the pinned part: the pinned run is byte for byte what it was, and the new texts are compared too. They found 4 new inputs on which the parsers differ, all in the open classes below: three are a double-quoted scalar that continues on a line less indented than its key or after another entry (yaml.v3 folds it into one string, saphyr refuses it), one is a `?` inside a plain scalar of a flow mapping (Rust reads it as text, yaml.v3 refuses it). They are in `testdata/p1/parity-known.txt` with a comment, 291 allowed in all; the comparison is green, its selftest still turns red in every break. Nothing in the parsers changed. It matters to P3b because the editor refuses a multi-line scalar (`EDIT_MULTILINE_SCALAR`) and the scanner of step E2 may only read what both parsers accept.

**The pin rename shifted the samples (#52), and what that found.** The seeds are the texts of the pinned corpus suites, and the pin rename edited the `grammar:` lines of those texts, so the random edits of the fixed-seed run drew other texts. Checked, not assumed: the one allowed divergence whose input contained the old pin name is gone from the run, and its counterpart with the new pin name (a `?` inside a plain scalar of a flow mapping, the open class below) is the same divergence on a changed text, now in the allow list in its place. The run also found **one divergence of another open class**, a colon right before the closing brace of a flow mapping, ` {aa:}`: saphyr reads the colon as the value indicator (key `aa`, empty value, as YAML 1.2 does for a colon followed by a flow indicator), yaml.v3 keeps it in the plain scalar (key `aa:`). It is in the allow list with its input and both outcomes. Neither is a change of a parser: the 287 other allowed divergences hold, `changed: 0`, `new: 0` (289 allowed in all).

`tools/parity` compares `parse` in the two languages on the same inputs: `gen_inputs.py` writes
30 005 deterministic inputs (the texts the repository holds, the pinned `access.yaml`, 450 hand-written
cases that YAML readers disagree on, and seeded random edits of them), `rust/tests/parity_dump.rs` and
`go/parity_dump_test.go` write the outcome of each as one JSON line (the tree with positions, the
contract violations, or the refusal code), `compare.py` groups what differs. A refusal against a refusal
(any non-contract codes) counts as agreement; everything else, an accept against a refuse, two
different trees or positions, a contract error against anything else, is a divergence.

| | divergent inputs of 30 005 |
|---|---|
| before (main, `2c58713`) | 3 134 in 12 groups |
| after | 287 in 6 groups, all in the open classes below (228 hold a flow collection or a quote followed by `#`, 59 are quoted scalars that continue on an unindented line or are followed by more text) (mutated variants of the 13 hand-pinned inputs. The figure belongs to the generated set: every regression case added to `gen_inputs.py` is also a seed of the random edits, so the set, and with it this count, changes with it (274, 299, 326, 300 and 287 at successive heads of this PR) and is not comparable with the 3 134 of the old code; what the gate holds fixed is each divergence's input and outcomes, not the count) |

What the differential found and what was done about it (each has pinned cases in `parity.json`):

* **Error priority.** See "The order in which problems are reported" below.
* **Positions.** The Rust parser located an indentless block sequence (`a:` then `- 1`) at its first item
  and a `- - a` chain at the inner dash, a block scalar at its first content line, and gave an implicit
  null in a flow collection (`{a}`) or an empty document a position. Now a block sequence is located at its
  `-`, a block scalar at its `|` or `>`, and a value the source does not spell has no position, as in Go.
* **Values.** A block scalar at the end of a text without a final line break, and one made of line
  breaks only, have different values in the two libraries: refused. Tags, anchors and aliases: yaml.v3
  drops a bare `!` (so `a: ! 1` is the integer 1) and reads `*` and `&` words its own way: a word that
  starts with one is refused in both languages by a text scan (`scan_words`) that skips quoted scalars,
  comments and block scalar content.
* **Inputs the readers do not agree are YAML.** Tabs, control characters, directives, document markers
  other than a leading `---`, a byte order mark inside the text, explicit `?` keys other than the
  emitter's own form, a comment touching `]` or `}`, an empty implicit key: refused in both languages
  by one pre-parse scan (`prescan`), before either YAML library is asked, in the same order.

### The order in which problems are reported

One rule, the same in both languages, tested with explicit expected codes in both
(`the_order_in_which_problems_are_reported_is_fixed` / `TestTheOrderInWhichProblemsAreReportedIsFixed`),
not by comparing the two with each other. The first step that finds a problem decides the answer:

1. **Size** over the limit: `LIMIT_EXCEEDED`.
2. **U+0085, U+2028, U+2029** anywhere: `UNSUPPORTED_SYNTAX`.
3. **The text scan** (`prescan`, `scan_words`), whole text, before either YAML library is asked:
   control characters, tabs, a byte order mark inside the text, then, in text order, directives,
   document markers, tags, anchors, aliases, explicit keys, a comment touching `]` or `}`:
   `UNSUPPORTED_SYNTAX`. One of these *anywhere* in the text hides a YAML syntax error *anywhere* in
   it: the scan does not know about syntax, and a text with a tab is refused for the tab even if it
   is also malformed.
4. **A YAML syntax error anywhere** in the text: `YAML_SYNTAX`. It beats everything below, wherever
   the problems are (a duplicate key, a `1.5`, a merge key, a non-scalar key before it or after it).
5. **A second document**: `UNSUPPORTED_SYNTAX`, ahead of what the first document holds. (No text reaches
   this step, since the scan refuses every `---` after the first content line; the check stays as a guard
   and is tested through the internal `read`.)
6. **The first problem in document order** of: an unsupported construct (`UNSUPPORTED_SYNTAX`), a
   duplicate key (`DUPLICATE_KEY`, the contract error), a node or depth limit (`LIMIT_EXCEEDED`).

So "a syntax error anywhere wins" holds for the problems of step 6 only; the problems of steps 1 to 3
are decided before the YAML syntax is looked at.

The scan reads a line as text, not as a marker or a directive, inside a block scalar and, if it is
indented, inside a quoted scalar; a `---` or `...` that starts a line inside a quoted scalar is
refused (the YAML spec ends the document there and the two readers disagree on what follows).
`marker_like_text_inside_scalars_is_content` (Rust) and `TestMarkerLikeTextInsideScalarsIsContent`
(Go) hold the YAML 1.2 values as expectations.

Open (pinned with both outcomes in `known_divergences`, see `testdata/p1/parity.json`): plain scalars in a
flow collection that hold an indicator character (`{a?}`, `[|]`, `{:1}`, `{aa:}`, `{a:}`, `[a:]`, `{-}`), a quoted scalar
followed directly by `#`, `{a: 1,a: 2:}`, a flow mapping continued on a line that is not indented
(`a: {b: <CR>1,1,` and similar), and a quoted scalar that continues on an unindented line. They are the
libraries disagreeing on what YAML 1.2 allows in flow context; the narrowing that closes them (a flow
scan in `scan_words`) is not done, and nothing in the repository's schemas, vectors or emitter output
uses these spellings.

The gate numbers of the parity work are the rows of "Quality gates" below (whole crate and module, not only the
parser). The one Rust mutation survivor is `read`, `docs > 1`, equivalent because the check after the loop gives the
same answer; time-outs count as detected. 33 seeded defects, 15 of them for this work, all caught. The property
tests found two real faults while this was written (`a[&: 1` and `a *b: 1`, plain scalars that the word
scan took for an anchor and an alias), both fixed and pinned. The fuzz job found a third (a key past 512 characters that starts with `-` or `:` is
emitted as `? -kkk…`, which the explicit-key rule refused): fixed, with a regression test in each language. A second fuzz run found a fourth (`a? *` is one plain scalar, the emitter writes it as a key, and the scan took its `*` for an alias because it counted a `?` after text as an indicator): `-` and `?` are indicators only where a scalar can start. Found by a brute-force check of every string of up to three characters of YAML indicators in four positions; that check is now a test in each language (`every_short_string_of_indicators_round_trips`), and a run over 3.4 million longer strings, with long keys, is clean.

Run it: `python3 tools/parity/gen_inputs.py in.hex --upstream "$CIC_PRIMITIVES_ROOT"`, then
`PARITY_IN=in.hex PARITY_OUT=rust.jsonl cargo test --test parity_dump` and
`PARITY_IN=in.hex PARITY_OUT=go.jsonl go test -run TestDumpParseOutcomes .`, then
`python3 tools/parity/compare.py in.hex rust.jsonl go.jsonl --allow testdata/p1/parity-known.txt`.
The `parity` job of `p1.yml` does exactly this. The allow list names each known divergence by its input *and*
by a digest of both outcomes, so it is red on a new divergence and equally on a known one whose answer on either
side has changed; `tools/parity/selftest.py` breaks the real outputs in those ways (and one that must stay
green, a fix) and requires the right verdict, in the same job. `make_corpus.py` rebuilds `parity.json` from `HAND`, `EXTRA` and `REGRESSIONS` of `gen_inputs.py`; `REGRESSIONS` (an alias in the place of a mapping key, which once made the Rust parser panic, #31) is appended after the seeded random part, so adding to it leaves the fixed-seed run, and with it the allow list, as it was.

## Quality gates (docs/PHASES.md, "Quality gates")

All on the product code of this slice (`rust/src`, `go/*.go` without tests), strictly above 90%,
failing closed, in workflow `p1`. Numbers are the CI measurements of PR #25 at commit `fe80d559`, on all of
`rust/src` and `go/*.go`, not only on the parser. Later commits of the PR change documentation, tests, the corpus, the CI workflow (the `quality` job in shards, `docs/QUALITY-SHARDING.md`) and the PR text only;
`git diff fe80d559..HEAD -- rust/src ':(glob)go/*.go' ':(exclude)go/*_test.go'` (the product code, without the tests) is empty, which is what makes the numbers the ones of the head.

| Gate | Tool (pinned) | Rust | Go |
|---|---|---|---|
| Line coverage | `cargo-llvm-cov` 0.9.1 / `go test -cover` (statements: Go has no line metric) | 98.69% (1279/1296) | 100.00% (601/601) |
| Branch coverage (`cic-branch/1`, ADR 0009) | `rustbranch` / `gobranch` | 97.25% (638/656) | 100.00% (735/735) |
| Mutation score | `cargo-mutants` 27.1.0 / `go-mutesting` v0.0.0-20261002081621-9ce5f731e321 | 99.77% (438 of 439 viable mutants caught or timed out, 1 missed) | 97.82% (806 killed, 18 survived) |
| Property tests | `proptest` / `rapid` | 14 properties, 512 cases each | the same properties (13 tests: "no value is a valid mode in both grammars" is part of the type-preservation test), 1000 cases |
| Fuzzing | `cargo-fuzz` 0.13.2 / native `go test -fuzz` | targets `parse`, `validate` and `access`, 60 s each in CI | `FuzzParse`, `FuzzValidate` and `FuzzAccess`, 60 s each in CI |
| Corpus parity | the shared corpus runners | 31 of 31 `recognize`, 29 of 29 presence and 21 of 21 inheritance `validate` | the same |

`tools/quality_gate.py` decides line coverage and mutation score (`check`), and `selftest` runs 21
seeded faults, one per reason (`REPORT_MISSING`, `REPORT_FORMAT`, `MEASUREMENT_EMPTY`,
`BELOW_THRESHOLD`, exactly 90% is red).

**The 18 Go survivors at `fe80d559`** (806 of 824 mutants killed; the CI count, reproduced exactly by the six local
shards of `tools/mutation_shard_exec.sh`): 7 in `access.go`, `emit.go` and `recognize.go`, 11 in `parse.go`. All are
equivalent mutants, the behaviour cannot differ:

* `access.go`, `emit.go`, `recognize.go`: the value returned next to an error that the caller never reads
  (`UNKNOWN_GRAMMAR_VERSION`, two mutants); the numeric value of the `ModeInvalid` sentinel; the early `return nil`
  of a lookup in a node that has no entries; the control-character exclusion of `plainSafe` in `emit.go` (two mutants),
  redundant since `Parse` refuses control characters and tabs and folds line breaks, which the round trip it is
  defined by sees; an operand that is always false for the segments it guards (`s.isIdx`).
* `parse.go`: an end-of-input clamp that returns the same line (`line >= len`); a CRLF look-ahead whose only
  difference is a line start nothing can refer to; the assignment of the zero value (`out.Kind = KindNull`); the "no
  block" sentinel `-1` against `-2` (two mutants); a slice end equal to its start; the `break` against `continue` of a
  loop over two candidates that exclude each other; the look-ahead for `''` at the end of an unterminated string; a
  longer slice for a prefix test whose next condition looks at the character after the marker; the leading flag
  updated inside a quoted scalar, where it is already false (two mutants).

Earlier heads had more survivors, which tests killed since: 33 at `9bf7d1aa` (the end of a block scalar, a marker or an
explicit key at the end of the text, a `&` followed by a lone CR, a `%` at the start of a line inside a quoted
scalar, the key anchor behind the scan: `the_edges_of_the_text_scan_give_the_yaml_answer`,
`the_checks_behind_the_prescan_still_refuse`, the corpus) and 19 at `36809bab` (one not equivalent: a line indented
deeper than a block scalar was, after it, still taken for its content). The comment skip of `scan_words` was redundant
(a word after text is never a scalar start) and is gone with its mutants.

**What each property checks** (Rust and Go identically): emit then parse gives back the value for any
value of the subset including any string and key (long ones too); emit is a fixed point under parse;
parse never panics on arbitrary text and its positions lie inside the text on character boundaries;
recognition follows the key set and leaves its input unchanged. For validation: a document built to conform to a random shape has no violation; leaving out one mandatory field gives only `MISSING_MANDATORY` errors, at most one at that path; the default candidates are exactly the absent optional fields that declare a default; for any document against any shape the answer is unsupported or a response of one phase, ordered, with no repeated (path, code), and the input is unchanged. For the access reading: whatever reads is written back to the same spelling, to the type, and no value is a valid mode in both grammars; every mode has one spelling per version and reads back, and the other version's reading refuses it; the version binding agrees with an independently written oracle (missing before unknown before conflict); `is_cert_pattern` agrees with the upstream regular expression for any string (a regex oracle in each language); and reading never changes its input. **Fuzzing** checks on every input
that parse does not panic, that a document that parses survives emit then parse, and that its
positions lie inside the text (`parse`); and that validation of whatever parses against a fixed shape that
uses every rule leaves the document alone and gives an ordered, unique, single-phase response (`validate`);
and that reading an `access`/`modify` value and an `InheritanceMode` under both grammars answers with a known code, writes back the spelling it read, accepts only valid CertPatterns and leaves the document alone (`access`; the Go target also checks `IsCertPattern` against the upstream expression on the raw text); the seeds are the shared vectors and the presence and access inputs.

### Proof that each gate can fail (what was run, and what it did)

| Gate | Seeded fault | Result |
|---|---|---|
| Line coverage | only the subset test runs | Rust 50.59% (596/1178), Go 32.45% (195/601), both `BELOW_THRESHOLD`; reproduced in CI |
| Mutation score | `recognize` is mutated, only tests that do not use it run | Rust 0.00% (0/23), Go 23.53% (16/68), both `BELOW_THRESHOLD`; reproduced in CI |
| Branch coverage | Rust runs only the behaviour tests, Go runs one test | Rust 67.22% (441/656), Go 0.00% (0/735), both `BELOW_THRESHOLD`; reproduced in CI |
| Quality gate script | 21 faults | every reason turns it red (`quality_gate.py selftest`, CI) |
| Property tests | a seeded bug in `emit` (backslash not escaped), by hand, in both languages | all three round-trip properties fail with a minimal counterexample; not reproduced in CI |
| Fuzz job | a committed input starting with `CANARY` fails each of the six targets when `CIC_FUZZ_CANARY` is set | red with the switch, green without; reproduced in CI |
| Seeded defects (`tools/seeded_defects.py`) | 18 deliberate defects in the access reading (7 in Rust, 11 in Go; the Go set adds an unbound `Grammar` read or written as grammar 2, an invalid `Mode` written as `reset`, and a caller-changeable supported-version set): `0` coerced to `false`, a missing version silently defaulting to 1, the pin used without verifying its digest, a string without `=` accepted as a CertPattern, the empty list read as the symbol, grammar 2 accepting the legacy boolean, a violation dropping its position | every defect is caught by the suites (the unmodified copy passes first); reproduced in CI (job `seeded-defects`) |
| Seeded defects for the parser parity (same script) | 15 more, 9 in Rust and 6 in Go: the last non-syntax error reported instead of the first, a block sequence or scalar located at its first item or content line, a word starting with `!` not refused, a tab accepted, a block scalar without content accepted, an empty implicit key read as `~`, the content of a block scalar scanned for markers, an indented marker inside a quoted scalar taken for a document marker, a syntax error in a second document reported as a second document | all 33 defects of the script are caught; reproduced in CI (job `seeded-defects`) |
| Parity gate (`tools/parity/selftest.py`) | 25 breaks of the real outputs: a known divergence answering differently on one side or both, an allow line removed, an agreeing input that now differs, an unknown error code or `NOT_AN_ERROR_TYPE` on one side or both (also identical on both, also for an allowed input), and ten malformed outcome structures; and one that must stay green (a fix) | each turns the gate red with its word (`CHANGED`, `NEW`, `INVALID`), the fix stays green; reproduced in CI (job `parity`) |
| Performance of duplicate keys | the previous linear scan put back by hand (Go) | 16.9 s against a 5 s bound; not reproduced in CI |

## What these gates found (all fixed, with a regression test or a vector each)

1. `emit` wrote the key `<<` plain, which `parse` reads as a merge key and refuses: emit then parse
   was not a round trip. Found by a mutation survivor that led to the seed, then to a property
   generator that now draws it. `<<` is always quoted.
2. `emit` wrote a key longer than 1024 characters as an implicit key, which YAML forbids; the Rust
   fuzz target found it in under a minute (`regression-long-key` is in the seed corpus). Keys longer
   than 512 characters use the explicit `? key` form, in both languages.
3. A remaining difference was removed rather than documented: U+0085 is a line break to `yaml.v3`
   and not to `saphyr-parser` (`a: 1<U+0085>b: 2` is two entries in Go and a syntax error in Rust,
   found by a probe), so texts containing U+0085, U+2028 or U+2029 are refused in both.

## Findings about the contract

1. `recognition.structured-partial-is-short-then-rejected` expected one error for two unknown fields
   and the two structured vectors lacked their `logical_path`. That contradicted ADR 0007. The
   vectors are corrected (`$.access` and `$.value`, in path order); the code was not narrowed to fit.
2. `saphyr-parser` rejects a tab after the `:` of a block mapping value (`b:\t2`); a tab inside flow
   collections is accepted and counts as one column.
3. The parser reports an empty value (`a:`) as the scalar `~` with an empty span. An explicit `~` has a span of one character. The slice tells them apart by the span, so only the explicit one has a position and the empty one is anchored at its key, as `presence.text-null-empty-scalar-anchors-key` requires. In Go the empty scalar is a node with no text at all.
4. **A value of the wrong type has no error code.** The registry has `MAP_AT_SCALAR_POSITION` for a mapping at a scalar position, but nothing for a string where an integer is declared, a scalar where a list or a mapping is declared, and so on. ADR 0003 says the Shape type contracts decide validity; the codes for those violations are not defined, and no vector asks for one. The slice refuses them explicitly (`UNSUPPORTED_SYNTAX`) instead of inventing a code. This is a gap for the ADR owner, not decided here.
5. The banners of `make corpus` and `make coverage` ("implementation conformance NOT RUN", "no Rust or
   Go code is measured yet") describe those structural tools and are stale next to the new runners.
6. **D-018 compatibility (details in the ADR 0004 and ADR 0002 amendments).** Recognition and the six-key set are
   unchanged; ADR 0004's reading rules hold under grammar 1; the old `upstream_conflict` expectation of
   `inherit.policy-zero-ok-despite-boolean-declaration` no longer applies (removed, id kept). **Gap:** five upstream
   codes (`INHERIT_ENUM_IN_GRAMMAR_1`, `LEGACY_INHERIT_IN_GRAMMAR_2`, `GRAMMAR_VERSION_MISSING`,
   `UNKNOWN_GRAMMAR_VERSION`, `GRAMMAR_VERSION_CONFLICT`) have no registry entry because no vector uses them, and the
   three `INVALID_*` codes are still `provisional`; both are decisions for the ADR owner.
7. Upstream lists `0.0` as `INVALID_INHERIT`. A float spelling is refused earlier by the YAML subset, so from text the
   value never reaches the reading.
8. The corpus runners now need a checkout of `cic-primitives` at the pinned commit (`CIC_PRIMITIVES_ROOT`) to derive the
   grammar version from the verified schema; CI provides it through `.github/actions/upstream-pinned`.
9. **Two levels of guarantee for the order of a response (issue #46, ADR 0007 "Which error comes first").** The order is by
   path *segments*. The property test checks it for the errors that have a written path (`logical_path`), in both languages,
   against the path text; that is a full check of the order of those errors. An error whose path cannot be written (an empty
   name, a control character) has no text, and the property does not compare its place; one fixed case
   (`an_error_at_an_unwritable_name_is_ordered_by_its_segments`, with the replayed seed) pins it down instead. A complete
   segment-based oracle would need the response to carry the segments, which the contract does not give; the narrower
   guarantee is deliberate and is stated here so it is not read as the wider one.
