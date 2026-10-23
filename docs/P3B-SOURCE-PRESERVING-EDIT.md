# P3b: source-preserving editing - the first unit, replacing one existing scalar

Status: **design; steps E2 (`source_span`), E3 (`edit_scalar`) and E4 (the proof of the guarantees) are implemented in Rust and Go (sections 11 and 12).** It follows the accepted ADR 0001, 0003, 0006, 0007 and 0010 and the merged P1-P3 behaviour; it re-opens none of them and changes
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
* **double-quoted** stays double-quoted: `\` and `"` are escaped; no line break and **no control character in the new value**. *Reading* is wider than *writing*: an existing double-quoted token that holds an escaped control
  character (`\t`, `\x07`, ...) is read by the scanner and the read-back as the parser reads it, and such a scalar can be edited (the edit replaces the whole token); but this first unit never *writes* a control
  character, in any style: a new value that holds one is `EDIT_VALUE_NOT_REPRESENTABLE` (section 6).
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
6. **A no-op is a no-op, and the spelling is not promised back.** Editing a position to the value it already has returns the original text byte for byte: the library does not rewrite a token whose
   value does not change, so a non-canonical but accepted spelling (`True`, `+5`, `~`) is never normalized as a side effect of an edit that changes nothing. Two edits of **independent** positions give the same text in
   either order. Independent means: the spans do not overlap, and neither edit changes the logical address of the other's position or makes the document invalid for the other (editing a key field that
   identifies a list element changes the address of every position inside that element, so an edit of the key and an edit inside the same element are not independent, and no commutation is promised for them). **Editing a value and then back is not promised to restore the original bytes**: the edit writes the canonical spelling of the new value (decimal integers, lower-case booleans, `null`), and the
   original may have used another spelling the parser accepts. It is promised only when the original token already was the canonical spelling and the style is kept (the review of this note asked for exactly this limit;
   keeping the original token's spelling instead is owner decision 8).

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
* **Properties** (both languages): untouched bytes (guarantee 1) over generated documents and edits; edit to the same value is the identity (also for a non-canonical spelling); two edits of independent positions (section 5, guarantee 6) commute; edit then edit back is the original
  only when the original spelling is the canonical one and the style was kept (and a case where it is not, which must come back with the canonical spelling, as the limit of guarantee 6); the result always re-parses to the old tree with one value replaced; `recognize` is unchanged; no input is modified; an edit never turns a valid document into an invalid one without `EDIT_INVALID_RESULT`.
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
8. **The spelling of a token**: proposed that an edit writes the canonical spelling of the new value and a no-op keeps the original token (guarantee 6). The alternative is to keep the original token's spelling family (`True` stays a `True`-style boolean, `+5` stays signed) when the kind is unchanged; it makes edit-and-back exact in more cases, but needs a spelling table per kind and the two parsers do not read every spelling alike.

## 11. Implementation notes of step E3 (for the owner's review; none of them changes the ADR)

Things the implementation had to decide that the ADR leaves open or reads two ways. Each is stated so that it can be changed in a later step.

1. **The original must validate.** `edit_scalar` validates the original first and returns its violations as they are (phase `validate`) when it does not: the reading of
   section 4 ("runs after parse, validate and path_resolve have accepted"), which is stricter than the sentence of guarantee 3 ("without errors the original did not have").
   P1 `validate` does not read an expanded Access wrapper (it is P2's), so the validator reads the document through a **short view** in which an expanded wrapper is replaced
   by its `value`; the other members of a wrapper (`access`, `modify`, ...) are the recognition's, and an edit of the value cannot change them.
2. **The surface.** `EditScalar` / `edit_scalar` (text, schema root, path, key set, a scalar `Node`) returns the text, the new span and `changed`. `EditParsed` / `edit_parsed` is
   the case of a document supplied as a parsed value: after the document and the path are accepted it is always `EDIT_NO_SOURCE_TEXT` (anchor unavailable).
3. **Spellings.** Integers in decimal, booleans `true`/`false`, null as `null`. A plain string is written plain only if it is plain-safe by the closed rule of section 5 *and*
   the parser reads the token back as the same string (so `true`, `123`, `null`, `~`, `1.5`, `False` are `EDIT_STYLE_CONFLICT` in a plain scalar). `yes`/`no`/`on`/`off` are
   plain-safe: the rule of section 5 does not list them, and the parser reads them as strings.
4. **The value.** `EDIT_VALUE_NOT_REPRESENTABLE` is a control character (Unicode category Cc, which includes tab, DEL and the C1 controls), U+2028, U+2029, or (Go) invalid UTF-8; a Rust
   `&str` cannot be invalid. A value that is not a scalar (a collection) is `EDIT_TYPE_MISMATCH`, as is null for a position that is not nullable; a quoted scalar that is given null or a
   number is `EDIT_STYLE_CONFLICT` (the style is never changed).
5. **A change to the E1 vectors.** The edit of the key of a list element changes the address of the scalar: the old path no longer resolves in the result, so the `span_after` of
   `edit.key-of-a-list-element` could not be checked. The `edit` kind got an optional `path_after` (the author tool, the checker and both runners know it); no other case has it.
6. **Not proved here.** The internal assertion `EDIT_SPAN_MISMATCH` (and its twin check of the re-parse) has no vector by design; its seeded defect is step E4. The 24 seeded
   defects of E3 prove the tests, not that assertion.

## 12. Step E4: the proof that the guarantees hold, and that the proof can fail

Step E3 made the guarantees true and checks them on every call. Step E4 shows, for each of them, **where it is checked from outside the library**, and that each of those checks can turn red. A check that has never been seen to fail proves nothing.

| Guarantee (ADR 0012, section 3) | In the library, on every call | Property, each language | Rust against Go differential (`tools/parity`, 3000 inputs) | A planted defect that it catches |
|---|---|---|---|---|
| 1. the bytes outside the span are the original's | `verify_edit` | yes | prefix, suffix and the start of the new span, from the original span of the dump | the check of the bytes before / after the span is skipped |
| 2. the style is kept and the canonical spelling is written | by construction (`token_for`) and `verify_edit` | yes | the style before, the style of the new span and of the token are the same; the token is the canonical spelling of the value | a single quote is not doubled; the backslash and the quote are not escaped; a string that needs quotes is written plain; the booleans are written capitalised |
| 3. the result parses to the old tree with that one scalar replaced, and validates | `verify_edit` (expected tree, parse, validate) | yes | the parsed tree of the original and of the result are dumped by each language; the comparison replaces the scalar in the original tree itself and compares | the tree of the result is not compared; the expected tree replaces every scalar; the result is not validated |
| 4. every position keeps its form | by the tree comparison of 3 | - | the same tree comparison, which also checks the order of the entries and an expanded wrapper | (as 3) |
| 5. Rust and Go give the same bytes | - | - | the text, the span, the refusal (code, phase, position, logical path) of the edit, the no-op, the edit back and both orders, byte for byte | any defect planted in one language |
| 6. a no-op is the identity; edit and back; independent edits commute | the no-op returns the input | yes | the no-op is the text; an accepted edit can be edited back and restores the bytes when the original token was canonical; independent edits are both accepted or both refused and give the same text in either order | a no-op rewrites the token |

**The differential is not trusted until it has failed.**

* Its input generator (`gen_edit.py`) is deterministic, and the comparison refuses a run that is too thin: every refusal code of the edit phase that a text can earn appears at least five times, and enough edits were accepted, changed, edited back and paired.
* `selftest_edit.py` breaks the outputs in 22 ways, each of which must turn the comparison red: one side differs (a byte, a span end, a refusal, `changed`, a missing line, a wrong order), **both sides wrong in the same way** (a byte outside the span, a changed style, a plain value written quoted, a no-op that changes the text, an edit that cannot be edited back, edits that do not commute, another scalar of the result changed, a reordered mapping, a changed form, a missing tree), and a run that proves nothing (nothing accepted, a code that never appears). Its first run found one break that stayed green (a plain value written quoted: the dump did not carry the style before the edit); `before` was added and the break is red.
* `edit_proof.py` plants ten defects in each language, one at a time, in a copy of the module, and the comparison against the other language's unmutated dump must turn red. Its first run found one defect that no generated input could see (the escapes of a double-quoted scalar: the generated documents never held a quoted scalar and a value with a quote or a backslash together); the generator got `quoted_case` and both languages are red now. The unmutated dumps must pass first.
* The internal assertion `EDIT_SPAN_MISMATCH` (and `EDIT_INVALID_RESULT`) cannot be reached by any input of the public function. Each condition of `verify_edit` / `verifyEdit` is removed in turn (the bytes before the span, the bytes after it, parse and validate, the comparison of the trees, the choice of the scalar in the expected tree: ten seeded defects, five per language) and the direct tests of the check turn red. The first run left one condition unproved: the test for the bytes after the span also failed the tree comparison, which made up for it; the cases now differ in that one condition only.

What E4 does not claim: that the two parsers read the YAML text alike beyond the subset (that is the parity of P1), the editing of anything but one existing single-line scalar, and the path text of a new list key that needs the escapes of ADR 0006 (such a key is not edited back in the differential).
