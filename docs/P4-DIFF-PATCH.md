# P4: diff and patch - the first units

Status: **design, no code.** It follows the accepted ADR 0001, 0003, 0006, 0007, 0008 and 0010 and the merged P1-P3 and P3b behaviour; it re-opens none of them and changes **no**
existing API. It adds two things: the implementation of the diff that ADR 0008 already defines, and a first, narrow `patch`. The points that are the owner's are in section 9.

P4 in `PHASES.md`: *Diff and patch. Exit: `patch(A, diff(A, B))` equals B for every representable change; conflicts are explicit errors.* The sentence leaves open what "equals" means and
what "representable" is. This note answers the first with the contract that already exists (section 3) and the second by naming one small set of changes for the first unit (section 4).

## 1. What exists, and what does not

| Piece | State |
|---|---|
| The diff contract (ADR 0008): what a diff compares, its events, their order, the canonical bytes, the scope, the errors | **accepted** (P0), a contract and no more |
| 66 hand-derived vectors of it (`corpus/diff/vectors.yaml`: 57 positive, 5 negative, 4 `diff_stability`) | exist; **run by neither language** (counted NOT RUN) |
| `diff` in Rust or Go | **exists since D1-D3** (`go/diff*.go`, `rust/src/diff.rs`): all 66 vectors run in both languages, and since D3b a Rust-against-Go-against-model differential on 3000 generated pairs, its self-test, 32 planted defects and a fuzz target prove it (section 11) |
| `patch`, its contract, its vectors | **do not exist**; ADR 0008 says expressly that patch "needs the semantics of applying operations, preconditions, conflicts and authorization" |
| The pieces a diff and a patch are made of: expand / collapse (P2), the identity of a list element and the path operations (ADR 0006), normalization (P3), the source-preserving replacement of one scalar (`edit_scalar`, P3b, merged, with its proof) | **exist** in both languages |

So P4 is not one unit. The diff comes first, because `patch(A, diff(A, B))` cannot be written, tested or fuzzed until a diff can be computed; and a diff is defined by 66 vectors that are waiting.

## 2. What the diff and the patch are for

A diff is *descriptive evidence of a difference* (ADR 0008): it is not an instruction, and a difference is not drift and not an authorized change. A patch is the step from that evidence back to a document: it applies a
set of changes to a document it is handed. **It decides nothing about who may.** Authorization, the interpretation of a difference as drift, and inheritance resolution stay outside (ADR 0008, "Not decided here"); the patch of this note is a mechanical, checked application. That a patch applied says nothing about whether it was allowed, a diff does not become an instruction by being applied, and **an empty diff is neither evidence of provenance nor of authorization** (it says only that the compared logical content is equal).

## 3. What "equals" means: the invariant

`patch(A, diff(A, B)) = B` can mean three things.

| Reading | Meaning | Possible? |
|---|---|---|
| **S, semantic** | the logical content of the result equals that of B: `diff(patch(A, diff(A, B)), B)` has no `changes` | yes, and it is exactly what a diff can promise |
| **S+, semantic and minimal** | S, and the result differs from A only where a change was applied (for text: the bytes outside the replaced scalars are A's, ADR 0012) | yes, with P3b |
| **B, byte for byte** | the result has the bytes of B's text | **no, in general** |

B is impossible, not merely hard. ADR 0008 defines a diff on the logical content alone: YAML mapping order, list order, flow versus block style, quoting, comments and short versus expanded writing are **not** differences (the last is reported in `representation` and is not a change). Two texts of the same logical content therefore have
the same diff from A, and the diff cannot say which of them was meant. A small example: A is `name: alpha`; B is `name: 'beta'  # the host`. The diff is one `value_change` (`alpha` to `beta`); `patch(A, d)` is `name: beta`; the quotes and the comment of B are not in the diff, and cannot be.

**Proposed invariant (the owner's to accept, section 9.1): S+.** For every A and B that the first unit can handle (section 4), with `d = diff(A, B)` made under one `SchemaContext` and one comparison scope:

1. *Round trip, within the scope.* `diff(patch(A, d), B).changes` is empty **under the same context and the same scope as `d`**. With the complete scope that is logical equality of the two documents. With a partial scope (ADR 0008: what was not observed is not absent) it is equality of the **observed region only**: a diff made over `$.ip.gateway` and an empty result do not show that the whole of B equals the patched A, and the patch makes no such claim.
2. *Minimality.* The result differs from A only at the positions of the applied changes; the bytes outside those scalars are A's, in every style, comment and line ending.
3. *Replay rejection* (not idempotence). A diff with at least one effective change applied a second time is a conflict, not a second application: the `previous` of the change no longer matches. A diff with **no** changes is not a conflict: `patch(A, empty diff)` is A byte for byte, every time.
4. *Nothing partial.* Either every change of the diff is applied or the result is an error and **no text**; no partly modified text is returned, even when a working copy was edited internally on the way.

Written the other way, the promise is: after a patch the **diff against B is empty**, and the **only bytes that moved are the values that had to**. Anything that would need B's text (its quoting, its comments, its short or expanded writing) is a *representation* matter, reported by the diff and never applied.

## 4. The first unit of the patch

Small, and with every other change refused by name.

* **Applies:** `value_change` events whose `path` addresses an **existing primitive scalar position** of A (a string, integer or boolean field, in short or expanded writing, a scalar inside a list element), with `previous` and `next` scalars of the kind the position has. Applying one is `edit_scalar` (P3b) with `next`, so every refusal of that operation (a block scalar, a multi-line scalar, a flow collection, a value a style cannot carry, ...) is already defined and already proved.
* **Precondition (the conflict check):** the value that A holds at the path must equal `previous`, type-strict (`0` is not `false`). If it does not, the patch is **`PATCH_CONFLICT`** and nothing is applied. This is the explicit conflict the exit sentence asks for.
* **Refused, whole patch, with a code:** `added`, `removed`, `policy_declaration_change`, `set_change`, a `value_change` on an opaque position (the value is not a scalar), on a key component of a list element (a diff never produces it: a changed identity is a removal plus an addition), or on a path A does not have. `representation` entries are not events and are ignored.
* **Context, and what is proved of it.** A diff's canonical bytes carry no schema context (ADR 0008 is not reopened), so a patch **cannot verify under which context a diff was made**. The two sides are kept apart:
  * *The patch verifies itself:* A parses and validates under the `SchemaContext` the caller passes; the version of the diff is known; every path of an event resolves against that schema; `next` and `previous` are of the kind the position has; the value A holds equals `previous`, type-strict.
  * *The patch takes from the caller, as a trusted binding:* that the diff was computed under that same context. `Context supplied` is not `Context verified`, and `previous matches` is not `diff provenance verified`. A binding of a diff to its context, if one is wanted, is the work of a separate, trusted envelope (an integration contract, not this ADR); the patch does not claim to know where a diff came from.
* **Order:** the changes of a diff are in canonical order and at most one per `(path, kind)`; positions that are independent commute (P3b guarantee 6), so the order of application is not observable. Atomicity holds either way: nothing is returned until every change has been checked and applied.

Not in the first unit (each a later unit with its own note): applying `added` / `removed` (insertion and deletion are not in P3b either), set changes, declaration changes (the five fields of an expanded position), opaque values, changing representation, a patch of an already-parsed document without text, a command line, authorization.

## 5. Order of work, each its own pull request

| Step | Content |
|---|---|
| **P4-0** | this note; ADR 0013 (the patch contract: invariant S+, the first unit, the phase `patch` and its codes), written after the owner's decisions of section 9 |
| **P4-1** (done: `corpus/patch`, 60 cases, `tools/patch-vectors/author.py`, the rules `PATCH_INPUT`, `PATCH_BYTES`, `PATCH_ROUNDTRIP` with 15 faults) | the patch vectors, written by hand before any code (kind `patch`: a clean apply, a conflict, each refused event kind, the round trip, the minimality, the context mismatch, replay rejection and the no-op of an empty diff), the checker, accounting stays exact |
| **D1** (done, one pull request with D2 and D3: the groups turned out small and are one function) | `diff`, step one: the logical view (expanded form, identities, sets), scalar and declaration changes, representation, opaque values, the canonical bytes; the vectors of those groups run in both languages |
| **D2** | `diff`, step two: composites, lists by identity, sets, appearing and disappearing nodes as one event |
| **D3** (the code and the 66 vectors done with D1-D2; the differential, its self-test, the planted defects and the fuzz target are **D3b, done**) | `diff`, step three: the scope, the errors (`DIFF_CONTEXT_MISMATCH`, `DIFF_UNSUPPORTED_VALUE`), the stability cases; all 66 vectors run; the Rust-against-Go differential and its self-test |
| **P4-2** (done) | `patch`, first unit, in Rust and Go on top of D1-D3 and `edit_scalar` |
| **P4-3** | the proof pass, as E4 was for P3b: properties (the round trip and minimality over generated A and B), a Rust-against-Go differential that checks each promise on its own, its self-test, planted defects, the seeded defects of the checks inside `patch`, a fuzz target |

D1-D3 may be fewer steps if the groups turn out small; the cut is by vector group, so the accounting stays exact at every step. P4-1 can run beside D1.

## 6. What the proof has to be (the lessons of P3b)

* Vectors by hand **before** the code; the expectations are not produced by the implementation.
* A check is believed only after it has failed: every promise of section 3 gets a place where it is checked from outside the code (a property, a differential), and that place gets a self-test that breaks it in the ways it must catch, **including both languages wrong in the same way**.
* A thin run is a failure: the generators must reach every refusal code of the phase, and the comparison says so when they do not.
* A defect planted in one language must turn the differential red; the first run is expected to find a generator that cannot reach some defect, and that is fixed, not explained.
* Whole new tests, not lines added to existing ones, so that the diff gate stays in `diff` mode where it can (section 9.6).

## 7. Errors

A new phase `patch` after `diff` (ADR 0007 gets a tenth phase when ADR 0013 is accepted). Proposed codes, each with a negative vector:

| Code | Condition |
|---|---|
| `PATCH_CONFLICT` | the value A holds at the path is not `previous` (this is also what a second application of a diff with effective changes is; a diff with no changes never conflicts) |
| `PATCH_UNSUPPORTED_EVENT` | an event kind or a target the first unit does not apply (the list of section 4) |
| `PATCH_TARGET_ABSENT` | the path does not address a position that A holds |
| `PATCH_CONTEXT_MISMATCH` | only what the patch can check: A is not readable under the context the caller gave, or the diff's version is unknown. It does **not** mean that the diff was made under another context: nothing in the inputs can show that |

A refusal of the replacement itself keeps the code and the phase of ADR 0012 (`edit`); a refusal of A (parse, validate, path) is reported as it is today. One error per response, in the fixed order: the earlier phases, `PATCH_CONTEXT_MISMATCH`, then the events in canonical order.

## 8. What this note does not do

It does not define a patch of anything but existing scalars, authorization, drift, a merge of two diffs, a three-way merge, a reverse patch (`diff(B, A)` is the inverse as a diff; applying it is the same operation), or a rename detection (ADR 0008 detects none). It adds no code and moves no existing behaviour.

## 9. Decisions that are the owner's

1. **The invariant (section 3).** Proposed: S+ (the diff against B is empty **within the context and the scope of the diff**, and only the bytes of the changed scalars move); byte identity with B's text is not promised, with the argument above. The alternative is to demand more of the diff (carry the authored form and the text), which reopens ADR 0008.
2. **The context of a patch (section 4).** Proposed: the caller passes the `SchemaContext`; the patch checks what it can (A under that context, the version, the paths, the kinds, `previous`) and takes the rest, that the diff was made under that context, as the caller's trusted binding; the diff's bytes stay as ADR 0008 fixes them. The alternative is a context digest inside the diff (a change of ADR 0008), which would make the binding checkable.
3. **Atomicity and the refused events.** Proposed: all or nothing, and a diff that holds any event the first unit does not apply is refused as a whole (`PATCH_UNSUPPORTED_EVENT`), never applied in part. The alternative is to apply the supported events and report the rest, which makes a patch of a diff differ from "the" patch of it.
4. **The first unit's reach.** Proposed: only `value_change` on existing primitive scalars, through `edit_scalar` (text in, text out). The alternative is to start with a patch of a parsed value (no text); it needs a second writer that nothing proves.
5. **The order of work (section 5).** Proposed: D1-D3 before P4-2, with P4-0 and P4-1 first and in parallel with D1. The alternative is to build the patch against a hand-made diff and the diff later, which tests the patch against evidence nobody computed.
6. **The cost of the gate.** The counts of the accounting are numbers inside existing tests, so every new suite makes a pull request `full`. Proposed: move them to a data file before P4-1 (a small separate pull request), so that a new suite is an addition. **The numbers stay expectations written down in advance, by hand, in that file; they are never computed from the run** (a test still compares the run with them, and a run that disagrees fails). The rule of the quality gate is not weakened by where the numbers live.
7. **P3b is not closed by this.** The first unit of P3b (one scalar, E1-E4) is done; insertion, deletion, block and flow scalars, anchors and a batch of edits are still outside the supported subset. The `PHASES.md` row of P3b should say "first unit closed, later units open" once E4 is merged, and P4 is allowed to start before the rest of P3b because the first patch unit needs only the scalar replacement.

## 10. What D1-D3 decided (the points ADR 0008 leaves to the implementation)

ADR 0008 fixes the events, the order and the bytes. The implementation had to choose the following, and each choice has a test in both languages (`go/diff_test.go`, `rust/tests/diff.rs`). None of them changes a byte of the contract.

1. **Reading a view** is `validate` (every expanded wrapper read as its value) and then `expand` (the five symbolic defaults for a short position; no declared default is inserted, so a position the author did not write is absent, not defaulted). The members of an expanded wrapper are *not* read by the grammar (`inherit: 0` is a value that differs from `inherit: false`, as a vector says).
2. **The order of what is refused**, one error per response: the contexts differ; a scope entry is not a canonical path of the schema; an unsupported number in A, then in B (the check does not depend on what the other operand holds and happens before the views are read, because a float cannot validate); the view A does not validate or expand; the view B the same. Nothing is compared before every operand has been read.
3. **Reading errors name the operand** (`a` or `b`) and keep the first violation of the validator; an identity code (`PATH_KEY_*`, `PATH_CASE_*`, `PATH_DUPLICATE_IDENTITY`) is reported in the phase `path_of`, as ADR 0008 says, though the validator reports it in `validate`.
4. **An unsupported number** is a new document-model kind, `KindUnsupported` (Go) / `Value::Unsupported` (Rust), that holds the spelling. The parser never produces it (it refuses the spelling); only a caller that builds a view from another source can. The diff names the logical path of the position that holds it (through an expanded wrapper, into a list element, a set or an opaque value; an undeclared key or an element without an identity is named by the position above). It exists only so that `DIFF_UNSUPPORTED_VALUE` can be reached at all and tested.
5. **The scope** is `None`/`nil` for the whole document; a given empty list compares nothing. Entries that repeat or lie inside another entry are dropped before anything is compared. A place a scope entry names that only one side has is one `added` or `removed` event at that place, with its fragment.
6. **Not decided by ADR 0008, so refused as `Unsupported` and not guessed:** a `null` against a collection (a nullable composite, list or set that is `null` on one side only), a plain list that declares no elements, and a place below a value that cannot hold it (a scope below a `null`).
7. **The order of set elements** across kinds (null, booleans, integers, strings) is implemented and tested, though a validated set holds one kind, so only a unit test reaches it. The payload order of list elements across `item_choice` cases is the order of the case declarations, then the identity (ADR 0008 says no vector covers it; the tests do now).
8. **`null` at an opaque position** is a value in the vectors (Absent to `null` is an appearance). The tree notation of the corpus does not spell nullability, so the runners mark every opaque position nullable.

## 11. D3b: what proves the diff beyond the vectors

The vectors are 66 cases; the implementations were written by the author of the vectors. D3b adds checks that do not depend on that:

* **A third opinion, `tools/parity/diff_model.py`.** A Python model of ADR 0008 that shares no code with Rust or Go (it works on plain JSON trees, builds the events bottom-up from the expanded trees, orders once at the end, and writes the bytes with `json.dumps`). It had to reproduce all 65 positive vector runs first (`diff_model.py --selftest`), and it did at the first run. Same author, so it guards against transcription errors and shared code bugs, not against a misreading of the ADR. It models valid operands only.
* **The differential, `tools/parity/gen_diff.py` + `parity_diff_dump` (both languages) + `compare_diff.py`.** 3000 generated pairs (A and a few edits of A, or an independent B) over a tree with every shape the ADR distinguishes, short and expanded writing, non-default declarations, a scope with nested, repeated and absent entries, and one case in ten with an injected fault. Four diffs per pair: A to B, B to A, A to A, and the pair with every list and set in another order. Checked: Rust = Go byte for byte (errors with code, phase, location, path, operand); both = the model; the shuffled pair gives the bytes of the pair as drawn; A against A is empty; B to A is A to B turned round; every answer is its own canonical form (strict JSON, no float); an injected fault gives the stated error with the operand of its side, A first. The run must also be worth something (every event kind, representation entries, scoped and unscoped diffs, empty and non-empty results, every fault code and both operands, minimum counts). First run: 0 differences on 3000 pairs.
* **Its self-test, `selftest_diff.py`: 21 breaks.** Seven of one side (a byte, a refusal, an operand, a location, a missing line, another order, A-against-A answering like another pair), two in which both sides are wrong alike and only the model can see it, and twelve in which both are wrong alike and the model is switched off (`COMPARE_DIFF_NO_MODEL`, a hook for this script only) so that the property itself has to catch it: stability, identity, inversion (three ways), the canonical form (a space, a float), a wrong fault code, a wrong operand, a valid pair refused, a run without events, a run without representation entries. All 21 turn the comparison red. The first run of the self-test found that one break crashed the comparator instead of being found by it (a float in the canonical bytes): the self-test now counts a crash as a miss, and the comparator catches it.
* **32 planted defects, `tools/parity/diff_proof.py`.** The `diff:` entries of `tools/seeded_defects.py` (16 per language: the order of kinds, the sorting of set changes and fragments, representation, one declaration field, a value of another kind, the pairing of list elements, a one-sided place, nested scope entries, the unsupported number of B, the phase and operand of a reading error, the escape of the quote, of U+007F, the order of keys). Each is planted in one language, the dump is made, and `compare_diff.py` must go red with the other language and the model unmutated. All 32 do. The same entries are the seeded defects the unit tests and vectors must catch (31 of 32 at the first run; the 32nd was not caught because the seeded run did not include the `diff` test target, which it now does).
* **Fuzzing.** `FuzzDiff` (Go) and `diff` (Rust, cargo-fuzz): two texts, any parseable pair; no panic, never `DIFF_UNSUPPORTED_VALUE` from a parsed text, B to A has as many events, A against A is empty, the bytes are the canonical writing. A first minute of each (about 1M executions) found nothing.

Not claimed: that the model is independent of the author's reading of ADR 0008; that the generated trees reach a nullable composite, a list or a set (they are `Unsupported` by decision and covered by unit tests); that the patch exists (P4-2).

## 12. P4-2: what the first patch decided (the points ADR 0013 leaves to the implementation)

`go/patch.go` and `rust/src/patch.rs`; the 60 vectors run in both languages and passed at the first run of working code, which is the evidence that the hand-written expectations and the code agree without having been derived from each other.

1. **The diff is passed as events, not as bytes.** `Patch(text, root, keys, grammar, schema, context, diff)` takes the version of the diff and its events (`PatchDiff`); representation entries are not passed. Reading the canonical bytes of a diff into events is not part of this unit (the runners read the structured `changes` of a vector, and the version from the bytes with a JSON reader of their own); an unknown version is therefore a number the caller passes. The ADR allows either form; the narrower one avoids a second JSON reader in the library that nothing proves.
2. **The context** is two `DiffContext` values, the schema's and the one the caller says the diff was made under, compared field by field; any difference, or a version other than `1`, is `PATCH_CONTEXT_MISMATCH`, after A has been read (parse, validate come first, as row 1 says).
3. **Rows 3 and 4 are passes over all the events, rows 5 and 6 are per event.** An unsupported kind or target anywhere in the diff is named first (its path, `not_applicable`), whatever the events before it; then the first path the schema does not resolve (the error of the path operations); then, event by event in the order of the diff, an absent position (anchored at the enclosing structure: the mapping for a field, the list for an element) and a value that is not `previous`, type-strict (anchored at the scalar). The events are taken in the order given: a diff is in the canonical order by ADR 0008, and the patch does not sort or check it.
4. **A value change that carries no `previous` or no `next`** is `PATCH_UNSUPPORTED_EVENT` (it is not the event section 2 describes). No vector covers it; the unit tests do.
5. **A key component** is detected from the schema (the path ends at a field of the selected case's key, below an element selector), not from the text.
6. **The application** is `edit_scalar` on the result of the one before, in the order of the diff; the refusals keep their code and phase `edit`, with the position in the working text (identical to A's for the first event, and for any event whose scalar lies before the earlier replacements). `spans` hold, for each replaced scalar, its span in A and its span in the final text. An event whose `next` equals its `previous` is accepted, is listed in `replaced`, and moves nothing.

Not done in this step: the proof pass of P4-3 (properties over generated A and B, the round trip, the Rust-against-Go differential of `patch` with its self-test, planted and seeded defects, a fuzz target), the reading of canonical bytes into a diff, the patch of a parsed value, and every unsupported event.
