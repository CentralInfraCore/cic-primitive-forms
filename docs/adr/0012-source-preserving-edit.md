# ADR 0012: source-preserving edit of one scalar

Status: **proposed** (2026-10-22). Not accepted: nothing below binds an implementation until the owner accepts it (see "Acceptance record"). It is step E0 of
`docs/P3B-SOURCE-PRESERVING-EDIT.md`, which holds the reasoning, the design and the questions that are the owner's; this ADR holds only what has to be fixed as a
contract so that Rust and Go can implement and test the same thing. It changes no existing behaviour: `parse`, `recognize`, `validate`, `expand`, `collapse`,
`normalize`, `denormalize`, the policy resolution, the `Node` type, `Provenance` and the value origins stay as they are.

## Context

ADR 0007 fixes the error contract and the source positions and says it *commits to no editing*; a position there is the start of a node, never its extent. `PHASES.md` has a
phase P3b, *edits keep untouched source bytes*, and nothing that defines an edit. The first unit that can honestly satisfy that sentence is small: replace one existing,
single-line scalar of a document in its original text, and leave every other byte alone. Anything wider (insertion, deletion, block scalars, flow collections, anchors, a batch of
edits) is a later unit with its own note.

## Decision

### 1. The operation

`edit_scalar(text, schema, path, value)` takes the original YAML text, the compiled schema, an ADR 0006 logical path, and a typed scalar value (string, integer, boolean, or null). It returns
either the edited text and a description of the edit, or an error and no text. The path addresses the value scalar of a primitive position (short form: the scalar; expanded form: its `value:`
scalar); the Access members of a position, and a position that holds a composite, a list or a set, are not addressable by this operation.

### 2. The span

The edit replaces exactly one **span** `[start, end)`: bytes of the original UTF-8 input, a leading BOM included (the offset of ADR 0007). A span never starts or ends inside a multi-byte
character or inside a line break. It is the extent of the scalar's own text: for a plain scalar from its first character to its last non-space character before any comment; for a quoted
scalar from the opening to the closing quote, both included. The span is found by a scanner over the supported subset that both implementations share and test with the same vectors, and
**every span is verified by reading it back**: the bytes of the span, decoded as the style of the scalar decodes them, must equal the value the parser gave for that node. A mismatch is
`EDIT_SPAN_MISMATCH` and no text.

### 3. What a successful edit guarantees (normative, and checked on every call)

1. The bytes before `start` and from `end` on are equal to the original.
2. The new span, read back, holds the new value, in the style the scalar had (plain stays plain when the new value is plain-safe and of the same kind, single- and double-quoted keep their quotes and escape as their
   style requires). A value the style cannot carry is `EDIT_STYLE_CONFLICT`; the edit never changes a style. Reading is wider than writing: an existing token that holds an escaped control character is read and can be replaced, but a new value that holds a control character is never written (`EDIT_VALUE_NOT_REPRESENTABLE`).
3. The result parses to the old tree with that one scalar's value replaced, and validates against the schema without errors the original did not have (`EDIT_INVALID_RESULT` otherwise).
4. `recognize` of the result gives the same form for every position (a short position stays short).
5. Rust and Go give the same bytes for the same input.
6. Editing a position to the value it already has returns the original text byte for byte (no token is rewritten when its value does not change, so a non-canonical accepted spelling such as `True` or `+5` is not normalized as a side effect); two edits of **independent** positions (non-overlapping spans, and neither edit changes the logical address of the other's position or makes the document invalid for it; editing a list element's key field changes the address of everything inside that element) commute. Editing a value and then back restores the original bytes **only** when the original token already was the canonical spelling the edit writes and the style is kept; otherwise the canonical spelling comes back, and nothing more is promised.

### 4. Errors: the phase `edit` and its codes

ADR 0007's phase list gets a ninth phase, `edit`, which runs after `parse`, `validate` and `path_resolve` have accepted the document and the path (a refusal of those phases is reported as it is today and
stops the edit). Like `parse`, it reports **one** error, the first failing condition in this fixed order:

| # | Code | Condition | Anchor |
|---|---|---|---|
| 1 | `EDIT_NO_SOURCE_TEXT` | the document was supplied as a parsed value, not as text (`source_location: unavailable`) | none |
| 2 | `EDIT_NOT_A_SCALAR` | the addressed position holds a composite, list or set | `value` |
| 3 | `EDIT_NO_SOURCE_VALUE` | the position has no value text: absent, an empty value (`key:`), or a default-origin value | `parent` |
| 4 | `EDIT_BLOCK_SCALAR` | the scalar is a block scalar (`\|`, `>`) | `value` |
| 5 | `EDIT_FLOW_CONTEXT` | the scalar is inside a flow collection | `value` |
| 6 | `EDIT_MULTILINE_SCALAR` | the scalar continues on more than one line | `value` |
| 7 | `EDIT_VALUE_NOT_REPRESENTABLE` | the new value holds a line break or a control character, or is not valid UTF-8 | `value` |
| 8 | `EDIT_TYPE_MISMATCH` | the new value is not the kind the position has, or the schema does not allow it | `value` |
| 9 | `EDIT_STYLE_CONFLICT` | the scalar's style cannot carry the new value | `value` |
| 10 | `EDIT_SPAN_MISMATCH` | the read-back of the span disagrees with the parser (a defect, reported and not repaired) | `value` |
| 11 | `EDIT_INVALID_RESULT` | the result would not validate where the original did | `value` (+ the validation errors) |

The anchor `value` is the value node of the addressed position; `parent` the enclosing structure (an absent field has no position of its own, ADR 0007). Message wording is not a conformance criterion.
Every code needs at least one negative vector in the `edit` phase (the gate rules `CODE_UNUSED` and `PHASE_UNUSED` of ADR 0007 apply), so the registry `corpus/codes.yaml` gets these entries together with their
vectors in step E1 and not before.

### 5. Anchors, aliases, tags, merge keys

The parser refuses them (`UNSUPPORTED_SYNTAX`), and this ADR does **not** make it accept any: such a document is refused in `parse` and cannot be edited. For the day a parser accepts them, an edit
that writes into an anchored node, a node reached through an alias, or a merge key's source is refused with a code to be added then (`EDIT_SHARED_NODE`), and no edit rewrites an anchor name or an alias.

### 6. The suite

ADR 0005 gets a case kind `edit`: `input_yaml`, the schema, `path`, a typed `value`, and either `expect.output_yaml` (the exact bytes, compared as bytes) or `expect.error` (`code`, `phase: edit`, the source position of the
anchor). Expected outputs are written by hand from these rules before either language implements the operation. The accounting stays exact.

## Not decided here

Insertion of an absent key, deletion, renaming, reordering, adding a list element, changing a short position to expanded, block scalars, flow collections, multi-line scalars, a batch of edits, the P4 diff and
patch engine, writing the result to disk, a command line. Each is a later note and ADR. The owner's open decisions of `docs/P3B-SOURCE-PRESERVING-EDIT.md` section 10 (the style policy, flow context in the first unit, the typed
value, editing `null`) are taken at acceptance and recorded below.

## Consequences

* Rust and Go get one small contract and a suite that tells them apart before any code exists.
* ADR 0007 is amended in one place (the phase list and the sentence about editing); ADR 0005 in one place (the `edit` kind). No existing code changes its answer.
* A scanner defect cannot damage a document: the read-back and the six guarantees turn it into a refusal.
* The first unit is deliberately narrow; what it refuses it refuses with a code and a vector.

## Acceptance record

Pending. At acceptance: the date, the decisions on the points of section 10 of the design note, and the two amendments applied to ADR 0005 and ADR 0007.
