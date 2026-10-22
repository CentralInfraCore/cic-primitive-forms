# P3b: source-preserving editing - the first unit, replacing one existing scalar

Status: **design, no code.** It follows the accepted ADR 0001, 0003, 0006, 0007 and 0010 and the merged P1-P3 behaviour; it re-opens none of them and changes
**no** existing API: `parse`, `recognize`, `validate`, `expand`, `collapse`, `normalize`, `denormalize`, the policy resolution, the `Node` type, the
`Provenance` and the origins stay exactly as they are. It adds a new, separate thing. There is no editor and no patch engine here (P4 is later). The points
that are the owner's are in section 10.

P3b in `PHASES.md`: *Source-preserving editing (comments, style, anchors). Exit: edits keep untouched source bytes.* This document defines the smallest unit that can
honestly satisfy that sentence, and what it refuses.

## 1. What the first unit is

> Given the original YAML text of a document, the logical path of a position, and a new scalar value, return the original text with the **one existing scalar
> that holds that position's value** replaced, and every other byte unchanged. If that cannot be done safely, return an error and no text.

That is all. Not included: inserting a missing key, deleting, renaming a key, reordering, adding a list element, changing a short position into an expanded one,
changing a collection, editing a block scalar, editing inside a flow collection, and anything with an anchor, alias or tag. Each of those is a later unit
(section 9) and, until it exists, an error with a code (section 7), never a best effort.

## 2. Why this is a separate thing and not a change of `Node`

The parsed tree has the start position of a node (`line`, `column`, `offset`, ADR 0007) and no end. Positions are kept only for the nodes the source spells.
Editing needs the **extent** of a scalar and its **style**, and it must not make the semantic tree heavier for everyone who does not edit. So:

* A new function builds a **source index** from the text: for every position the parser knows, the byte span of its value scalar, its style, and the spans of
  the surrounding structure that matter (the key, the entry, the line). The index is produced beside the tree, never inside it; `Node` and every P1-P3 result are unchanged.
* The index is derived from the **same parse** as P1 (so it sees the same document, the same refusals), and it is checked against it (section 4).
* A document that was supplied as an already parsed value has no text; ADR 0007 calls that `source_location: unavailable`. It cannot be edited: `EDIT_NO_SOURCE_TEXT`.

## 3. The mapping: logical path to source bytes

The addressing is the logical path of ADR 0006 and the compiled schema, as everywhere else; no line number or offset is an input of an edit (ADR 0007: *logical
identity is not source position*; the index resolves a path to a position inside this one text).

Step by step, for `edit(text, schema, path, new)`:

1. Parse `text` (P1). A refusal there is the answer (`UNSUPPORTED_SYNTAX`, `YAML_SYNTAX`, ...), unchanged.
2. Resolve `path` against the schema (`resolve_path`, P1). It must name a **primitive position** that holds a scalar (`PATH_*` errors as they are; a position that
   holds a composite, a list or a set is `EDIT_NOT_A_SCALAR`).
3. Find the document node of the position. A list element is found by its declared identity (the key fields), never by its index, so reordering the list does not
   change which element is meant. In **short form** the position is the scalar node itself. In **expanded form** (P2) it is the `value:` entry of the wrapper; the
   `access`, `modify`, `inherit`, `default_injection` and `conformance` members are not addressable by this unit, and the form is not changed (a short position stays short).
4. A position that has no source text of its own is not editable here: an absent field, an implicit `null` written as an empty value (`key:` with nothing after the colon), a value that
   normalization inserted as a default (origin `default`, no provenance). Editing those is an insertion: `EDIT_NO_SOURCE_VALUE` (a later unit).
5. Take the byte span of the scalar from the index.

The span is half-open `[start, end)` in **bytes of the original UTF-8 input, a leading BOM included**, the same offset as ADR 0007. It never starts or ends inside a
multi-byte character or inside a line break (CRLF is one break, a lone CR is one).

## 4. How the span is found, and why the edit is checked afterwards

Go's YAML library gives a node's line and column only; the Rust one gives spans. The two must give the same bytes, so the extent is not taken from either library. A small
**scanner over the supported subset** reads the source from the node's start offset to the end of that scalar, by style, and both languages implement the same scanner against
the same shared vectors (section 8):

| Style | Where the span ends |
|---|---|
| plain | the end of the text of the scalar on its line: before trailing spaces and before a comment that starts with ` #`; a plain scalar that continues on the next line is multi-line and refused in this unit |
| single-quoted | the closing `'`; `''` is an escaped quote and not the end; the closing quote must be on the same line |
| double-quoted | the closing `"`; `\"` and `\\` do not end it; same line |
| block (`\|`, `>`) | not scanned: refused in this unit (section 6) |

The scanner is trusted **only** as far as it is checked. After computing the span, the library **reads the scalar back**: it decodes the bytes of the span as that style would and
requires the result to equal the value the parser gave for that node. If not, the answer is `EDIT_SPAN_MISMATCH` and nothing is edited. So a scanner defect can never
silently damage a document; at worst it refuses one. Both implementations also run the check on every vector and on every generated document (section 8).

## 5. The edit and the guarantees

The replacement text is the new value written **in the style the scalar already has**:

* **plain** stays plain if the new value is *plain-safe* and is the same *kind* of scalar as before (a string stays a string, an integer an integer, a boolean a
  boolean). Plain-safe is a short, closed rule, stricter than YAML's: no leading or trailing space, no `: ` or ` #` inside, no first character that is an indicator
  (`- ? : , [ ] { } # & * ! | > ' " % @` and the backtick), not empty, no control character, no line break, and it must not read as another type than the one the
  position has (for a string position: not `true`, `false`, `null`, `~`, an integer, ...). Integers are written in the canonical decimal form the parser accepts.
* **single-quoted** stays single-quoted: `'` is written `''`; no line break.
* **double-quoted** stays double-quoted: `\` and `"` are escaped, a control character is written as an escape; no line break.
* A new value that the existing style cannot carry is **not** requoted silently: `EDIT_STYLE_CONFLICT` (owner decision 1, section 10).

Guarantees, each of them checked by the library on every successful edit and not only tested:

1. **Untouched bytes.** The bytes before `start` and from `end` on of the result are equal to the original. (Comments, indentation, blank lines, key order, quoting of every other
   scalar, line endings, the BOM and the final newline are all outside the span, so they are preserved by construction, and the check proves it.)
2. **The span holds the new value.** Reading the new span back with the same style rules gives the new value.
3. **Only that value changed.** The result parses (P1) to a tree equal to the old tree with only that scalar's value replaced, and it validates like the old one did when the
   new value is allowed by the schema; if validation of the result would produce errors that the original did not have, the answer is `EDIT_INVALID_RESULT` with those errors and no text.
4. **Same form.** `recognize` of the result says the same as before for every position (a short position is still short), so `collapse` and `expand` of the result agree
   with those of the original except for the value. `Provenance` therefore needs no change: it is computed from the text, and the text's shape did not change.
5. **Deterministic and identical in Rust and Go**, byte for byte (section 8).
6. **Idempotent.** Editing a position to the value it already has returns the original text byte for byte. Two edits of different positions give the same text in either order.

The returned text is a new string; the input is not modified (as `validate` never modifies its input).

## 6. Where the edit has to stop, and with which code

The codes are new and live in the phase `edit` (owner decision 3). A parse-phase refusal keeps its own code.

| Case | Answer |
|---|---|
| the text was not parsed from text (a value, ADR 0007 `unavailable`) | `EDIT_NO_SOURCE_TEXT` |
| the path does not parse, resolve, or address a primitive position | the existing `PATH_*` code, unchanged |
| the position holds a composite, list or set | `EDIT_NOT_A_SCALAR` |
| the position has no value text (absent, empty value, default-origin) | `EDIT_NO_SOURCE_VALUE` |
| the scalar is a block scalar (`\|`, `>`) | `EDIT_BLOCK_SCALAR` |
| the scalar spans several lines (plain or quoted, folded) | `EDIT_MULTILINE_SCALAR` |
| the scalar is inside a flow collection (`{...}`, `[...]`) | `EDIT_FLOW_CONTEXT` (owner decision 2) |
| the new value contains a line break, a control character, or is not valid UTF-8 | `EDIT_VALUE_NOT_REPRESENTABLE` |
| the new value is not the same kind as the position, or the schema does not allow it | `EDIT_TYPE_MISMATCH` |
| the existing style cannot carry the new value | `EDIT_STYLE_CONFLICT` |
| the read-back of the span disagrees with the parser | `EDIT_SPAN_MISMATCH` (an internal check that failed: a defect, reported, not repaired) |
| the result would not validate where the original did | `EDIT_INVALID_RESULT` |

### 6.1 Anchors, aliases, tags, merge keys

P1 refuses them today (`UNSUPPORTED_SYNTAX`, `docs/P1-SUPPORTED-SUBSET.md`), and this unit **adds no accepted syntax**: a document with an anchor, alias, tag or merge key is
refused at the parse step, so it can neither be edited nor be damaged. That is the safe boundary and it is the whole answer for this unit. For the day the parser accepts them (a
separate decision, with its own ADR), the rule is fixed now so that it is not invented under pressure: an edit that **writes into an anchored node, into a node reached through an alias,
or into a merge key's source** changes more than one logical place and is refused (`EDIT_SHARED_NODE`), unless the caller names every affected path; a plain scalar next to an anchor
that nothing refers to is editable. No unit may rewrite an anchor name or an alias.

### 6.2 Comments, indentation, quotes, style: what is promised

Comments (own line, trailing, between entries, inside a block) are never read and never written: they are outside every span. A trailing comment after the edited scalar stays and keeps
its distance (the bytes between the old end and the comment are untouched; if the new text is longer or shorter, the comment simply moves with the text after it, the spaces before it are
not changed). Indentation is not touched, because a single-line scalar replacement does not move any line. Quote style is kept (section 5). Key order, blank lines, line endings, the BOM, the
final newline and the spelling of every other scalar are not touched. The YAML *style* of collections (block or flow) is never touched.

## 7. The surface

(Names are proposals for the owner's review, not a commitment; the data is what matters.)

```
source_index(text)                      -> SourceIndex | Error          the spans of the scalars, beside the tree; no edit
edit_scalar(text, schema, path, value)  -> EditedText  | Error          the text with one scalar replaced; or an error and no text
EditedText { text, edit: { path, span_before, span_after, style, old_value, new_value } }
```

`Error` is the existing structured error (ADR 0007): `code`, `phase` (`edit`, or the earlier phase that refused), `source_location` (the span's start, or `unavailable` when there was no text),
`logical_path` where determinable. The caller gets the span, so a later layer can show or log exactly what changed. There is **no** batch call in this unit: several edits are several calls, each on the
result of the previous one (each is re-indexed), which is slower and obviously correct; a batch belongs with P4, where conflicts between edits are the subject.

## 8. The shared contract and the proof (Rust and Go)

* **A shared suite per ADR 0005**: a new case kind `edit` with `input_yaml`, the schema, `path`, `value`, and either `expect.output_yaml` (the exact bytes) or `expect.error` (code and phase). The outputs are
  compared as **bytes**; both languages run every case, and the accounting is exact. Cases to write first, before any code: every style (plain, single, double) with a longer and a shorter value;
  a trailing comment kept; comments above, below and between; CRLF, LF and lone CR files; a BOM; non-ASCII (the span in bytes, a multi-byte character before and after, in the value);
  a file without a final newline; the same scalar text appearing twice (only the addressed one changes); a list element addressed by identity, then the list reordered; short and expanded forms; an
  expanded position's `value:`; a position whose value is a quoted number; and one case per code of section 6, with a position for the error. **Every refusal has a case.**
* **Properties** (both languages): untouched bytes (guarantee 1) over generated documents and edits; edit to the same value is the identity; two edits of different positions commute; edit then edit back is the original
  when the style was kept; the result always re-parses to the old tree with one value replaced; `recognize` is unchanged; no input is modified; an edit never turns a valid document into an invalid one without `EDIT_INVALID_RESULT`.
* **A differential run** like P3's (`tools/parity`): generated documents and edits, Rust output against Go output byte for byte, fixed seed, in the required `parity` job, with a selftest that breaks the outputs
  six ways and requires red.
* **A fuzz target** (`edit`) with a canary: on any input that parses and any path/value, the call returns an error or text for which guarantees 1-4 hold; it never panics.
* **Seeded defects**, both languages: the span one byte short, one byte long; an edit that eats the line break; a trailing comment dropped; a quote style changed; a multi-byte character cut; the CRLF turned into LF;
  the wrong occurrence of equal text edited; the read-back check removed; the validation of the result removed. Each must turn the suite red.
* The same quality gates as every unit (branch coverage and mutation, through the diff gate of `docs/DELTA-DIFF-GATE.md`). No gate is lowered.

## 9. Order of work after this design is accepted

| Step | Content |
|---|---|
| E0 | An ADR (proposed here as ADR 0012, section 10): the phase `edit`, the codes of section 6, the span and read-back contract; amendments to ADR 0005 (the `edit` kind) and 0007 (the phase). **No code before it is accepted.** |
| E1 | The `edit` suite: all vectors and refusals of section 8, expected outputs written by hand from the rules, run first by neither language (a gate that cannot fail proves nothing). |
| E2 | `source_index` and the scalar scanner in Rust and Go, against the E1 index vectors; the read-back check. |
| E3 | `edit_scalar` for single-line plain, single- and double-quoted scalars in block context; the guarantees as checks. |
| E4 | Properties, fuzz, the differential, seeded defects. |
| later units | insertion of an absent key; a block scalar; a flow-context scalar; a multi-line scalar; changing a short position to expanded; deletion; the anchors (needs a parser decision first). Each is its own design note and PR. |

## 10. Decisions that are the owner's

1. **A new value the style cannot carry**: refuse (`EDIT_STYLE_CONFLICT`, proposed) or requote to double-quoted automatically. Proposed: refuse; a caller that wants a requote asks for it explicitly in a later unit.
2. **Flow context** (`{a: 1}`, `[a, b]`) in the first unit: proposed refuse (the scalar's end depends on `,` and the brackets and doubles the scanner); allow in a later unit.
3. **A new phase `edit` and a new ADR (0012)**: proposed yes, since ADR 0007 fixes the phase list and says it commits to no editing. The ADR is the E0 step and needs the owner's acceptance before code.
4. **Typed value or text** as the input of `edit_scalar`: proposed a typed value (string, integer, boolean, null) so the library, not the caller, chooses the spelling and checks the kind.
5. **Editing `null`**: a position written `~` or `null` is a scalar with text and is editable; a position written as nothing (`key:`) is not (an insertion). Proposed as written.
6. **The refusal of everything with an anchor, alias or tag** stays tied to the parser's refusal; extending the parser is a separate decision and not part of P3b's first unit.
7. **Where the tool lives and who may call it**: the library only; no command line, no file writing. Writing the result to disk, atomically, is the caller's.
