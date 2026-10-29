# ADR 0007: Error contract and source positions

Status: **accepted** (2026-10-18, P0 acceptance review; see "Acceptance record" at the end). Addresses issue #3 point 6. It defines the
structured error, the phases, the source location, the path offset, which error is
reported and in what order, and reviews every error code of the corpus. It does
**not** define message wording, source-preserving editing (P3b), diff conflicts or
any behaviour of a runner. Nothing here is evidence that the Rust or Go implementation
exists.

## Principle

Rust and Go must give the **same machine response** for the same faulty input, and an
input that has no source text must never be given a made-up line and column. So a
response is data, its order is fixed by a rule that does not depend on traversal
order, and a position exists only where preserved source text exists.

## The error object

| Field | Required | Meaning |
|---|---|---|
| `code` | yes | a code of `corpus/codes.yaml` |
| `phase` | yes | one of the seven phases below |
| `source_location` | yes | exactly one of three states (see "Source location") |
| `logical_path` | where determinable | canonical ADR 0006 path text; never invented |
| `path_offset` | `path_parse` only | offset in the path text (see below) |
| `operand` | diff only | `a` or `b`: which view of a two-operand operation the error is about (ADR 0008) |
| `message` | optional in the API | human text; **not a conformance criterion**, and not allowed in the corpus |

A response is an ordered list of such errors, never empty on failure. A conforming
implementation may add fields to its own types, but the machine response above is what
the corpus asserts and what the two languages must agree on.

## Phases

`parse` (YAML text to document; ADR 0008 adds an eighth phase, `diff`), `recognize` (short or expanded, ADR 0001), `validate`
(schema, ADR 0003 and 0004), `path_parse` (path text, ADR 0006), `path_format`,
`path_resolve` (path against schema), `path_of` (path of a document element).

- **Phases run in order and stop.** Errors of a later phase are not reported while an
  earlier phase has errors, so one response holds one phase only.
- `parse` reports **one** error: the first in source order (parsers differ in how
  they recover, so more is not portable). `path_parse`, `path_format`,
  `path_resolve` and `path_of` also report one error: the first failing segment or
  the located element.
- `recognize` and `validate` report **all** their errors, except that nothing is
  reported below a node that already has an error (no cascades).

## Which error comes first

The errors of a `recognize` or `validate` response are sorted by `logical_path`,
segment by segment, then by `code`:

- a prefix sorts before its extensions (`$.a` before `$.a.x`);
- `Field` segments compare by the UTF-8 bytes of the name;
- `ListItem` segments compare by their components in the declared key order: strings
  by UTF-8 bytes, integers numerically;
- equal paths compare by `code`, bytewise.
- an error at a position whose path cannot be written as text (an empty name, or one with a control character: no
  `logical_path`, see ADR 0006) is ordered by its segments like every other: the order never depends on the text of the path,
  so `$.<<` comes before the error at `$.d.` and the empty name, because `<<` sorts before `d` (issue #46).

The key deliberately contains no source offset: the order must be the same whether or
not the input had source text, and the same for any traversal order of the two
implementations. Consequently several errors need a `logical_path` each, and
`(logical_path, code)` is unique within a response. Fixtures that name a position
only (no field structure) can therefore produce a single error only; the corpus gate
enforces this.

## Logical path in errors

`logical_path` uses the ADR 0006 canonical text only. It is present when the
location is determinable: `validate` errors on a composite or list structure, a
`path_resolve` error (the path up to and including the segment that failed), a
`path_of` error (the path of the list field when the element has no identity, of the
element when its identity is duplicated). It is absent in `parse` and `path_parse`
(no document or schema has been interpreted yet), `path_format` (there is no path),
and when the validated value is a single position with no structure around it. When
it cannot be determined it is left out, not guessed.

## Source location

`source_location` is **always present** and is one of:

| Form | Meaning |
|---|---|
| `{line, column, offset}` | the error is anchored in preserved source text |
| `{state: unavailable}` | a document exists but was supplied as an already parsed value, so there is no text |
| `{state: not_applicable}` | the error has no document at all (path text and path model errors) or spans several documents (a stability case) |

`null` and a missing key are not states and are rejected. A position is never
computed from a parsed value.

Coordinates, all measured **inside the source text itself** (the `input_yaml` of a
case, not the file that holds the case):

- `line`: 1-based; line breaks are LF, CRLF (one break) and a lone CR.
- `column`: 1-based, in Unicode code points from the line start; a tab is one column;
  a leading BOM is not a column.
- `offset`: 0-based, in bytes of the original UTF-8 input, a leading BOM included.
  The offset is not allowed to fall inside a multi-byte character or a CRLF.

The gate checks that line, column and offset agree on the text, so a miscounted
column (non-ASCII, CRLF, BOM) is a corpus error.

### What the position points at

The anchor is fixed per code and per phase in the registry:

| Anchor | Position |
|---|---|
| `second_key` | the second (and later) occurrence of a duplicated key; only the first duplicate is reported |
| `key` | the key of the offending entry (unknown field) |
| `value` | the value node of the offending entry |
| `value_or_key` | the value node, and the key when the value is an empty scalar, which has no text of its own |
| `mapping` | the mapping node: its first key in block style, its opening brace in flow style |
| `sequence` | the sequence node: its opening bracket in flow style (block style is not covered by a vector yet) |
| `parent` | the enclosing structure, for a missing field, because an absent field has no position |
| `path_text`, `none` | no YAML position; see `path_offset`, or the logical path only |

The anchors are claims about positions that the runners of P1 must reproduce from two
different YAML libraries; that is the real test of this table.

### Logical identity is not source position

`$.nics[{name="eth0",zone="eu"}].subnet` is the same logical path when the YAML is
rearranged, while its source position moves. Logical identity must survive
rearrangement; the position points into the one input document. Source-preserving
editing (P3b) may later reuse positions retained by the parser, but this ADR commits
to no editing.

## Path offset

A `path_parse` error carries `path_offset`: 0-based, in Unicode code points of the
path text. It is a separate range from YAML coordinates and is never converted into
one. Per code it points at: `PATH_SYNTAX` the first character that cannot continue a
valid path (the text length at a premature end); `PATH_BAD_ESCAPE` the backslash;
`PATH_EMPTY_NAME` the first character after the introducing dot, or the equals sign;
`PATH_INDEX_SELECTOR` the opening bracket; `PATH_DUPLICATE_COMPONENT` the start of the
second name; `PATH_NONCANONICAL` and `PATH_INTEGER_RANGE` the start of the offending
token (the backslash for an escape).

## Review of the codes

Each code has a meaning, the phases it can be reported in with an anchor, a trigger,
and at least one negative vector **per listed phase** (gate rules `CODE_UNUSED`,
`PHASE_UNUSED`). `stable` means the code is closed in this proposal and changes only
by an ADR amendment; `provisional` means a named dependency is still open. The
registry is `corpus/codes.yaml`; this table is its summary.

| Code | Status | Phases | Review decision |
|---|---|---|---|
| `DUPLICATE_KEY` | stable | `parse` | kept |
| `EXPANDED_WITH_EXTRA_KEY` | stable | `recognize` | kept |
| `MAP_AT_SCALAR_POSITION` | stable | `validate` | kept |
| `UNKNOWN_FIELD` | stable | `path_resolve`, `validate` | merged with `PATH_UNKNOWN_FIELD`: one meaning, the phase tells where |
| `MISSING_MANDATORY` | stable | `validate` | kept |
| `NULL_NOT_ALLOWED` | stable | `validate` | kept |
| `MIN_ITEMS_NOT_MET` | stable | `validate` | renamed from `CONTRACT_VIOLATION`, which promised every contract and had one vector |
| `INVALID_ACCESS_VALUE` | provisional | `validate` | kept; open upstream (cic-primitives #17) |
| `INVALID_CERT_PATTERN` | provisional | `validate` | kept; open upstream (cic-primitives #17) |
| `INVALID_INHERIT` | provisional | `validate` | kept; open upstream (cic-primitives #17) |
| `PATH_SYNTAX` | stable | `path_format`, `path_parse` | kept |
| `PATH_BAD_ESCAPE` | stable | `path_parse` | kept |
| `PATH_EMPTY_NAME` | stable | `path_format`, `path_parse` | kept |
| `PATH_INDEX_SELECTOR` | stable | `path_parse` | kept |
| `PATH_DUPLICATE_COMPONENT` | stable | `path_parse` | kept |
| `PATH_NONCANONICAL` | stable | `path_parse` | kept |
| `PATH_INTEGER_RANGE` | provisional | `path_format`, `path_parse` | kept; needs a numeric contract |
| `PATH_NO_CHILDREN` | stable | `path_resolve` | merged from `PATH_THROUGH_SCALAR` and `PATH_THROUGH_OPAQUE`, and now also covers a field below a set |
| `PATH_SET_ELEMENT` | stable | `path_resolve` | narrowed to a selector on a set |
| `PATH_NOT_A_LIST` | stable | `path_resolve` | kept |
| `PATH_LIST_NOT_SELECTED` | stable | `path_resolve` | kept |
| `PATH_SELECTOR_MISMATCH` | stable | `path_resolve` | kept |
| `PATH_KEY_TYPE` | stable | `path_of`, `path_resolve` | kept |
| `PATH_KEY_UNSUPPORTED` | provisional | `path_format`, `path_of`, `path_resolve` | kept; key encodings undecided |
| `PATH_DUPLICATE_IDENTITY` | stable | `path_of` | kept |
| `PATH_KEY_MISSING` | stable | `path_of` | kept |
| `PATH_CASE_NONE` | stable | `path_of` | kept |
| `PATH_CASE_AMBIGUOUS` | stable | `path_of` | kept |

23 of 28 codes are stable. The three `INVALID_*` codes wait for the upstream decision
on the `inherit` enum and the `access` / `modify` union (cic-primitives #17). Their
text will change only if that decision does. `PATH_INTEGER_RANGE` waits for a numeric
contract and `PATH_KEY_UNSUPPORTED` for key encodings other than string and integer
(ADR 0006).

"Stable" here is a statement of this proposal, which stays proposed until reviewed:
the stable status takes effect together with the ADR.

## Corpus

The corpus format moves to `corpus_format: 2`:

- `expect.errors` (an ordered list of error objects) replaces `error`,
  `validate.code`, the recognition `phase` and `code`, and the `path` hint. Every
  negative case now states phase and source location.
- `validate` cases may carry `input_yaml`, so a validation error can have a
  position.
- New cases: raw `input_yaml` with multi-line text, non-ASCII before and on the
  error line, CRLF, a BOM, a duplicate key reported once, the first of several;
  recognition and validation errors with locations; a missing mandatory field
  anchored at its parent; an empty scalar anchored at its key; and the same errors
  from parsed input alone, which carry `unavailable` and no coordinates. Several
  errors are asserted in their sorted order, including one whose source order is the
  reverse of the response order.

New structural rules, each with seeded faults: `ERROR_SHAPE`, `ERROR_PHASE`,
`ERROR_LOCATION` (state per phase and input form, coordinates consistent with the
text, path offset in range), `ERROR_ORDER` (one phase per response, single-error
phases, ordering key present and unique, and the list really in the order above,
compared on typed path segments and not on text), `PHASE_UNUSED`. A `logical_path`
must be a canonical ADR 0006 path, and for `path_resolve` a prefix of the resolved
path in whole segments (`$.no` is not a prefix of `$.nope`). The gate carries a small
lexer for that, which decides only whether a path text is canonical and how two
compare; it knows no schema and no document. The gate checks structure
only. It does not produce parse, recognition or validation errors, and a green run
still says nothing about Rust or Go.

## Amendment: the list-element identity codes in `validate` (2026-10-21, owner decision)

`PATH_KEY_MISSING`, `PATH_KEY_TYPE`, `PATH_KEY_UNSUPPORTED`, `PATH_CASE_NONE`, `PATH_CASE_AMBIGUOUS` and
`PATH_DUPLICATE_IDENTITY` were registered for `path_of` only. Validating a document against a schema meets the same
facts (ADR 0002 "List identity", ADR 0006 "Building a path from a document"), and a response holds one phase, so the six codes
are now also reported in phase `validate`. Meaning, code and trigger do not change:

- in `path_of` the error stays `phase: path_of` (no source location, `unavailable`);
- while validating a document the same fact is `phase: validate`, with the anchors below and the rules of "Source location"
  and "Logical path in errors" as for every other validation error.

| Code (phase `validate`) | Anchor | Logical path |
|---|---|---|
| `PATH_KEY_MISSING` | `mapping`: the element's record, which starts at its first key | the list (`$.items`) |
| `PATH_KEY_TYPE` | `value`: the key value of the element | the list |
| `PATH_KEY_UNSUPPORTED` (provisional) | `value`: the key value | the list |
| `PATH_CASE_NONE`, `PATH_CASE_AMBIGUOUS` | `mapping`: the element's record | the list |
| `PATH_DUPLICATE_IDENTITY` | `mapping`: the record of the **second** element that has the identity | the identity (`$.items[{name="a"}]`) |

The logical path is extended to the element's identity only where the identification succeeded: a duplicate is reported at its
identity and nothing is reported below an element that already has an error; a failed identification is reported at the list.
A duplicate identity is one error per identity, never one per element and never mapped to an index. The corpus has a negative vector
for each new phase in `corpus/lists/vectors.yaml`, in the `validate` kind with the list notation of its header
(`shape.elements`, ADR 0005 fixture notation, not a schema).

## Not decided here

- Message wording and translation.
- Source-preserving editing and the use of positions in P3b.
- Anchors for block sequences, anchors, tags and comments; no vector covers them.
- A source position for `path_of` errors when the document is supplied as text; in
  this version `path_of` takes a parsed document and reports `unavailable`.
- Whether `recognize` and `validate` run as one pass in an implementation; only the
  response, which is sorted and phase-exclusive, is fixed.
- Warnings and a non-error channel.
- That the two YAML libraries of P1 agree on the node start marks assumed above.

## Amendment: the phase `edit` (ADR 0012, 2026-10-22)

A ninth phase, `edit`, is added after `diff`. It runs after `parse`, `validate` and `path_resolve` have accepted the document and the path, and reports **one** error: the first failing condition of the fixed order of ADR 0012, section 4. Its anchors
are `value` and `parent` as defined above. The sentence at the top of this ADR (and the entry of the *Not decided* list) that says it commits to no editing stands for what this ADR itself defines: the use of the positions
retained by the parser to replace a scalar is the subject of ADR 0012. A position here is still the start of a node; the extent of a scalar is found by the scanner of ADR 0012, section 2, and is checked against the parser's value.

## Amendment: the phase `patch` (ADR 0013, 2026-10-23)

A tenth phase, `patch`, is added after `edit`. It runs after the earlier phases have accepted the document (`parse`, `validate`) and reports **one** error: the first failing condition of the fixed order of ADR 0013, section 3. It has five codes, `PATCH_CONTEXT_MISMATCH`, `PATCH_UNSUPPORTED_EVENT`, `PATCH_DUPLICATE_TARGET` (added 2026-10-29, ADR 0013), `PATCH_TARGET_ABSENT` and `PATCH_CONFLICT`
(the anchors are in ADR 0013, section 6: `not_applicable`, `parent` and `value`). A document that does not validate, a path that does not resolve against the schema and a refusal of the replacement itself are **not** patch errors: they are reported with the code and the phase of the phase that refuses them (`validate`, `path_resolve`, `edit`).
`PATCH_CONTEXT_MISMATCH` is raised only for what the patch can check, and does not say under which context a diff was made.

## Acceptance record

Accepted on 2026-10-18 by the owner's decision in the P0 acceptance review (`docs/reviews/p0-acceptance-review.md`). *Accepted* means this is the normative contract of the P0 scope and we know what must be built; it is **not** evidence that an implementation exists or conforms. All corpus expectations still have one author and are cross-checked only against throwaway models; the Rust and Go runners of P1 are the independent check.

Conditions and limits:

- Six codes stay `provisional` and acceptance does not change that: `INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN`, `INVALID_INHERIT` (upstream, cic-primitives #17), `PATH_INTEGER_RANGE`, `PATH_KEY_UNSUPPORTED`, `DIFF_UNSUPPORTED_VALUE` (numeric contract and key encodings). A contract does not need every code to be stable.
- The anchor table is a claim that the P1 runners must reproduce on two YAML libraries; that a coordinate points at the intended node is not proven by the gate.
- Block-sequence anchors, message wording and source positions for `path_of` given text are not decided.
