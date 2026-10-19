# ADR 0004: Access inheritance vocabulary

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end); **amended by the D-018 adoption record** at the end of this ADR, which supersedes the "open upstream" statements below without rewriting them. Addresses issue #3 point 8 (the type of the symbolic
`inherit`) and fixes how the library reads the three-valued `inherit` key. It
does **not** define how inheritance is resolved: the normative semantics are
open upstream (see "Upstream status"), and resolution is P3.

## The word `inherit` is used in three places

| Place | Values | Meaning |
|---|---|---|
| `access` / `modify` value | the symbol `inherit`, or a `CertPattern` list | this field takes its rule from the parent's PolicySurface rules (`access.yaml`, default `inherit`) |
| Access key `inherit` | `true` (default), `false`, `0` | whether sub-objects inherit this field's rule (`access.yaml`, D-010) |
| `PolicySurface.inherit_policy` | declared `boolean`, default `true`; the description also uses `0` | the same three states at object level (`policy-surface.yaml`); the declared type and the description contradict each other |

The library keeps the three apart. They share a spelling, not a type.

Two of the three carry a type contradiction in the source schemas, and this ADR
names both rather than papering over them:

- `access` / `modify` are typed `list<CertPattern>` but default to the symbol
  `inherit`;
- `inherit_policy` is typed `boolean` (`policy-surface.yaml`) but its description
  defines the value `0` ("full reset"), which a boolean does not hold.

Settling both type contracts is the job of upstream P0.4,
`cic-primitives` issue #17 ("Define Access atom instance grammar (P0.4)"), not
of this library.

## Sources

| Source | Used for |
|---|---|
| `cic-primitives` `schemas/atomic/access.yaml` | `access` / `modify` typed `list<CertPattern>` with default `inherit`; `inherit` typed `boolean \| 0`; `cert_pattern` syntax |
| `cic-primitives` `schemas/aggregate/policy-surface.yaml` | `inherit_policy`, evaluation order |
| `cic-primitives` `ai/DECISIONS.md` D-010 | the three meanings of `inherit` |
| `cic-primitives` `proposals/atom-grammar/README.md` (P0.4) | the upstream statement that the three-state `inherit` is not a stable boolean |

## Upstream status

The grammar document states that the three-state `inherit` (`true` / `false` /
`0`) is not a stable boolean type, that a real enum is needed (`inherit` /
`no-inherit` / `reset`), and that it belongs to the full policy grammar of
`access` (P0.4), "not separately". P0.4 is explicitly outside the grammar as
written.

Consequences for this library:

- It does not invent the enum or its semantics.
- The sources do not sharply separate `false` from `0`: D-010 says `false` gives
  the sub-object "its default rules" and `0` makes it "recompute completely";
  `policy-surface.yaml` says "built from scratch"; the prompt map says "parent
  rules do not apply". The model exists; the bridge breaks at the normative
  difference between `false` and `0`. Resolution cannot be specified until
  upstream decides it.

## What the library does until then

1. **`inherit` key.** The accepted values are exactly three, told apart by YAML
   scalar type: boolean `true`, boolean `false`, integer `0`. Anything else
   (integer `1`, the strings `"true"` / `"0"`, `null`, a list) is a validation
   error. `false` and `0` are never coerced into one another. An implementation
   must not use loose equality (in several languages `0 == false`); it maps the
   parsed scalar type to an internal three-valued type at the YAML boundary.
2. **Round trip keeps the spelling.** `0` is emitted as `0`, `false` as `false`.
   The mapping from the three spellings to the internal type is a bijection, so
   nothing is lost.
3. **`access` / `modify`.** The accepted values are the scalar symbol `inherit`
   or a list of `CertPattern`. A `CertPattern` is `field=value` pairs
   (`OU=operators,O=acme-corp`, wildcard `*` allowed), so the bare string
   `inherit` is not a valid pattern: `access: inherit` (symbol) and
   `access: [inherit]` (a list holding an invalid pattern) cannot be confused.
   The empty list `[]` means *nobody* (`access.yaml`), it is **not** `inherit`
   and never turns into it (ADR 0003: empty is a value, not a request for the
   default).
4. **Type conflict of the symbol.** `access.yaml` types the key `list<CertPattern>`
   and defaults it to `inherit`, which is a symbol in a list-typed key. The
   library accepts the symbol exactly because the schema declares it as the
   default, and proposes upstream that the type be written as the union
   `list<CertPattern> | inherit`. Until that is changed the library's rule is the
   one in item 3.
5. **Expand** fills `access: inherit`, `modify: inherit`, `inherit: true`
   symbolically (PHASES, ADR 0001). It does not resolve anything.
6. **Recognition (ADR 0001) is unaffected.** It looks at keys only, never at the
   values of `inherit`.
7. **`inherit_policy`** is read with the same vocabulary rule as the `inherit`
   key: boolean `true`, boolean `false`, integer `0`, by YAML scalar type. This
   accepts `0` even though the schema declares `boolean`; the library follows the
   documented meaning and flags the contradiction (above) for upstream instead
   of rejecting a value the description defines.

## Not decided here

- The meaning of `false` versus `0`, and the evaluation of inheritance. The
  documented evaluation order (`policy-surface.yaml`: field-level Access, then
  `surface_rules`, then `default_access` / `default_modify`, then the parent's
  PolicySurface when `inherit_policy` is true) is recorded for P3, not
  re-specified.
- The internal representation. The vectors require only that the input type and
  value are preserved and that `false` and `0` stay different; they do not fix
  any enum name. The upstream names (`inherit` / `no-inherit` / `reset`) are not
  normative here.
- What "sub-object" covers given ADR 0002 (a nested composite, the record of a
  list element, a case of a choice).

## Vectors

Provisional vectors are in `corpus/inherit/vectors.yaml`, in the format of
ADR 0005.

## Open questions

1. Upstream: the normative difference between `false` and `0`, and the final
   enum (P0.4). Blocks the resolution part of P3.
2. Upstream: write the `access` / `modify` type as a union and resolve the
   `inherit_policy` `boolean` versus `0` contradiction. Both belong in the
   existing `cic-primitives` issue #17, as additions to it; no new issue is
   opened. (Issue #17 also says PolicySurface is not defined in the corpus, based
   on a search of two files; `schemas/aggregate/policy-surface.yaml` exists, so
   that statement needs correcting upstream. It does not affect this ADR.)
3. What a "sub-object" is for the purposes of `inherit`, in terms of the Shape
   structure of ADR 0002.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

### Scope of this acceptance

Two questions must never be merged:

1. *Can the library read `true`, `false`, `0` and the symbol `inherit` without loss?* This is what ADR 0004 specifies and what is accepted.
2. *Which effective permission follows from them?* This is **not** decided. It needs the normative semantics of `false` versus `0`, the enum and the union type, all open in cic-primitives #17.

ADR 0004 is accepted as the library's **reading contract** for the `inherit` key and for the `access` / `modify` / `inherit_policy` values: type-strict, lossless, `false` never equal to `0`, an empty list never equal to `inherit`. It does **not** define the meaning of `false` versus `0`, an enum, the evaluation of inheritance or any effective permission; those wait for the upstream decision, and the codes `INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN` and `INVALID_INHERIT` stay provisional. Accepting it must not be read as accepting an inheritance semantics.

Box 8 of issue #3 is not ticked by this acceptance and its wording is not narrowed: the type contradiction is real upstream and waits for cic-primitives #17.

## Amendment: adoption of D-018 (`access-policy/1`)

Recorded with the change that pins `cic-primitives` at `73ae4692f0680a316d367179ce057195a0031688`
(PR #19 and #20, grammar identity `cic-primitives-73ae469-71ee71ca`). The text above is left as it was
accepted; this record says what upstream has settled since, what it means for this ADR, and what is still
open. It changes no accepted decision of this ADR, and it does not accept an inheritance semantics.

### What upstream settled

| Statement above | After D-018 |
|---|---|
| "Upstream status": the three-state `inherit` is not a stable boolean, a real enum is needed, P0.4 is outside the grammar | `InheritanceMode = inherit \| no-inherit \| reset`, one concept at two scopes (`Access.inherit`, `PolicySurface.inherit_policy`), with two spellings: grammar 1 `true \| false \| 0`, grammar 2 the enum names. The spelling is told apart by YAML scalar type and never coerced |
| the normative difference between `false` and `0` is unknown | `no-inherit` and `reset` are defined and separated by vectors upstream; that is semantics, so it is P3 here, not this ADR |
| item 4: `access.yaml` types the key `list<CertPattern>` but defaults it to a symbol | the type is the union `list<CertPattern> \| inherit`, as this ADR proposed |
| item 7 and the type conflict of `inherit_policy` (`boolean` against `0`) | `inherit_policy` is typed `InheritanceMode`; the contradiction is gone. A grammar-1 `0` is a valid value of the declared type, so a vector no longer needs to flag an upstream conflict |
| "Open questions" 1 and 2 | closed upstream by D-018 |
| (not covered) the grammar version | a property of the pinned schema (`spec.access_grammar.version`) and of the processing context, never a key of a document; it has no default (`GRAMMAR_VERSION_MISSING`), must be a supported integer (`UNKNOWN_GRAMMAR_VERSION`) and must agree (`GRAMMAR_VERSION_CONFLICT`) |

### Compatibility findings

1. **ADR 0001 (recognition) is unaffected.** Upstream states the same rule over the same six keys and adds no
   seventh key; the version is not a document key.
2. **ADR 0004 items 1 to 3, 5 and 7 hold as written under grammar 1**: `true`, `false` and `0` by scalar
   type, `false` never equal to `0`, a lossless round trip, `[]` is nobody and never the symbol, `[inherit]`
   is a list with an invalid pattern, `inherit_policy` is read with the same rule.
3. **The error codes `INVALID_INHERIT`, `INVALID_ACCESS_VALUE` and `INVALID_CERT_PATTERN` have the same
   names and triggers upstream.** Their registry status stays `provisional`: the blocker named in ADR 0007
   (cic-primitives #17) is resolved, but turning the status into `stable` is a decision of the owner, not of
   an implementation change.
4. **Five upstream codes are not in `corpus/codes.yaml`**: `INHERIT_ENUM_IN_GRAMMAR_1`,
   `LEGACY_INHERIT_IN_GRAMMAR_2`, `GRAMMAR_VERSION_MISSING`, `UNKNOWN_GRAMMAR_VERSION`,
   `GRAMMAR_VERSION_CONFLICT`. The library emits them unchanged and traces them to the pinned
   specification; no vector uses them, so they cannot enter the registry without cases (the gate rejects an
   unused code). A gap to report, not guessed away.
5. **`CertPattern` is now defined exactly** (`attr=value` pairs joined by `,`, `attr` `[A-Za-z][A-Za-z0-9.-]*`,
   a value that is not empty, does not start with `=`, `,` or whitespace, and holds no `=` or `,`). This
   ADR only said "`field=value`, wildcard `*` allowed". The library adopts the upstream definition; its
   whitespace class is the Unicode White_Space property plus U+001C to U+001F, which is what the upstream
   Python `\s` matches.
6. **`0.0` as text.** Upstream lists `0.0` as `INVALID_INHERIT`. In this library a float spelling is refused
   earlier by the YAML subset (`UNSUPPORTED_SYNTAX`, `docs/P1-SUPPORTED-SUBSET.md`), so the value never
   reaches the `inherit` reading when it comes from text.
7. **ADR 0002 and the grammar identity.** The file set grows from seven to ten files (the specification
   `P0-4-ACCESS-GRAMMAR.md`, its migration rules `P0-4-MIGRATION.md`, and the upstream `LICENSE.md`); the
   old pin is replaced and every `grammar:` reference was migrated deliberately. The reference model and the
   187 upstream vectors are contract evidence and are not in the file set.
8. **ADR 0010 is unaffected**: upstream states that `conformance` stays where it is.

### Corpus consequence

`inherit.policy-zero-ok-despite-boolean-declaration` carried `upstream_conflict: true`. The conflict no
longer exists, so the expectation key is removed and a description records why. The id is unchanged (ids are
stable); it is now a plain grammar-1 case.

### Still open, and not decided here

* The effective permission that follows from the three modes, and the system defaults: P3.
* The registry status of the three provisional codes, and the registry entries for the five upstream codes.
* A text-level case for `0.0`: the YAML subset refuses it before it can be read.
