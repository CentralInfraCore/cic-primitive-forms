# ADR 0013: Patch - the first unit

Status: **accepted** (2026-10-23, the day the owner merged this ADR; see "Acceptance record"). It is step P4-0 of `docs/P4-DIFF-PATCH.md`, which holds the reasoning, the order of work and the questions that are the owner's; this ADR holds only what has to be
fixed as a contract so that Rust and Go can implement and test the same thing. The reviewer's professional GO was the review, not the acceptance: the acceptance is the owner's merge. It re-opens none of ADR 0001-0012: the diff
stays exactly as ADR 0008 defines it (its bytes, its events, its order, its errors), and the replacement of one scalar stays exactly as ADR 0012 defines it. Nothing here is evidence that a Rust or Go implementation exists.

## Context

ADR 0008 defines the diff as descriptive evidence of a difference and says that patch, "`patch(A, diff(A, B)) == B`", needs the semantics of applying operations, preconditions, conflicts and authorization, and is P4. The diff is accepted and has 66
hand-derived vectors; neither language implements it yet. The one write path that keeps untouched source bytes is `edit_scalar` (ADR 0012). The first unit of the patch is the smallest operation that is honest about all of that.

## Decision

### 1. The operation

`patch(text, diff, schema, context)` takes the original YAML text of a document A, a diff (the structured events of ADR 0008, or its canonical bytes read back into them), the compiled schema and the `SchemaContext` the caller says the diff was made under. It returns
either the patched text and the list of the scalars it replaced, or an error and **no text**. It decides nothing about who may apply a diff: a successful patch is not an authorization, a diff does not become an instruction by being applied, and an empty
diff is neither evidence of provenance nor of authorization.

### 2. What is applied

Only `value_change` events whose `path` (ADR 0006) addresses an **existing primitive scalar position** of A: a string, integer or boolean field in short or expanded writing, also inside a list element, where `previous` and `next` are scalars of the kind the position has.
Each is applied as `edit_scalar(text, schema, path, next)`; every refusal of that operation keeps its code and its phase `edit` (ADR 0012). `representation` entries are not events and are ignored.

Everything else is refused, as a whole patch, with `PATCH_UNSUPPORTED_EVENT`: an `added`, `removed`, `policy_declaration_change` or `set_change` event; a `value_change` on an opaque position, on a composite or list, or on a key component of a list element (a diff never produces it: a changed identity is a removal plus an addition); an event of a kind this ADR does not name.
There is no application of the supported part of a diff that also holds an unsupported event.

### 3. The checks, in a fixed order; one error per response

Each condition belongs to **exactly one** phase and one code; a failure is reported where it belongs and nowhere else.

| # | Condition | Reported as |
|---|---|---|
| 1 | A does not parse, or does not validate against the schema | the error of the earlier phase (`parse`, `validate`) with its own code and violations; **never** `PATCH_CONTEXT_MISMATCH` |
| 2 | the `context` the caller passed does not match the `schema` it passed (the schema identity or digest, the grammar commit or digest, or the surface), or the version of the diff is not `1` | `PATCH_CONTEXT_MISMATCH` |
| 3 | an event or a target that section 2 does not apply (the first in the canonical order of ADR 0008) | `PATCH_UNSUPPORTED_EVENT` |
| 3a | two events of the diff name the **same path** (text equality: paths are canonical, ADR 0006, so one position has one spelling); the error names the path of the later of the two | `PATCH_DUPLICATE_TARGET` |
| 4 | the path of an event does not resolve against the **schema** (an unknown field, a component that names no declared key) | the error of the path operations (`path_resolve`), as it is today; **not** `PATCH_TARGET_ABSENT` |
| 5 | the path resolves against the schema, but **A does not hold** that position (the field is absent, or the list has no element of that identity) | `PATCH_TARGET_ABSENT` |
| 6 | A holds the position and its value is not `previous`, type-strict (`0` is not `false`, `null` is not absent) | `PATCH_CONFLICT` |
| 7 | a replacement is refused by `edit_scalar` | the code and phase `edit` of ADR 0012 |

Row 3a is decided over all events after row 3 has passed every event, and before any path is resolved: it does not depend on the document or on the schema. Rows 4-6 are taken per event in the canonical order of ADR 0008, and a row for an earlier event comes before the rows of a later one. Rows 1-6 are all decided **before the first byte is changed**; row 7 happens during the replacements, on a working copy, and if it (or anything else) fails the response is the error and **no text is returned**. The replacements are applied in the canonical order, each by `edit_scalar` on the result of the one before. Positions that are independent commute (ADR 0012, guarantee 6); a change to the key field of a list element is not among the applied events, so no application changes the address of another.

### 4. What a success guarantees: checked on every call, and proved from outside

A single call does not receive B, the other language, or its own earlier calls. So the guarantees are of **two kinds**, and the ADR does not claim of the one what only the other can show.

**Checked by the patch on every call** (and part of its result):

1. *Preconditions.* Every `previous` equals the value A holds (section 3, row 6): this is what makes a replay a conflict (a diff with at least one effective change, applied to its own result, is `PATCH_CONFLICT`), and a diff with **no** changes is not a conflict: `patch(A, empty diff)` is A byte for byte.
2. *Minimality.* The bytes outside the replaced scalars are A's, in every style, comment and line ending (ADR 0012, guarantee 1, which `edit_scalar` checks).
3. *Nothing partial.* On any error no text is returned, also when a working copy was edited internally.
4. *Each replaced scalar* holds `next`, in the style it had (the guarantees 2-4 of ADR 0012).

**Proved from outside, by the proof pass (P4-3), and not claimed per call:**

5. *Round trip, within the scope.* For `d = diff(A, B)` made under one context and one scope, `diff(patch(A, d), B).changes` is empty under the same context and scope. With the complete scope that is logical equality; with a partial scope it is equality of the **observed region only** (ADR 0008: what was not observed is not absent), and the patch claims nothing about the region outside it. This needs B, so it is a property over generated A and B and a vector set, not a check inside `patch`.
6. *Rust and Go give the same bytes, the same list of replaced scalars and the same error* for the same input: a differential, with a self-test that breaks it.
7. *Replay and the empty diff* as properties over generated diffs (a second application is a conflict; an empty diff is the identity), alongside the per-call precondition of 1.

**Byte equality with the text of B is not promised and cannot be**: the diff ignores order, style, quoting, comments and short versus expanded writing, so the text of B is not a function of A and the diff.

### 5. The context: what is verified and what is taken on trust

A diff's canonical bytes carry no schema context, and this ADR does not add one. The patch therefore **cannot verify under which context a diff was made**.

* *The patch verifies itself:* A parses and validates under the `schema` the caller passes; that `context` matches that `schema` and the version of the diff is known (`1`); every path resolves against the schema; `previous` and `next` are of the kind the position has; the value A holds equals `previous`.
* *The patch takes from the caller, as a trusted binding:* that the diff was computed under that same context. `Context supplied` is not `Context verified`, and `previous matches` is not `diff provenance verified`.

`PATCH_CONTEXT_MISMATCH` is therefore raised only for what can be checked (section 3, row 2): the context does not match the schema the caller passed, or the version of the diff is not `1`. It is **not** the code of a document that does not validate (that is the earlier phase, row 1), and it never claims that the diff was made under another context. A binding of a diff to its context, if wanted, is the work of a separate trusted envelope (an integration contract), not of this ADR.

### 6. Errors: the phase `patch` and its codes

ADR 0007's phase list gets a tenth phase, `patch`, which runs after the earlier phases have accepted A. It reports **one** error, the first failing condition in the order of section 3.

| Code | Condition | Anchor |
|---|---|---|
| `PATCH_CONTEXT_MISMATCH` | the context does not match the schema given, or the version of the diff is unknown (section 3, row 2) | `not_applicable` |
| `PATCH_UNSUPPORTED_EVENT` | an event or a target that section 2 does not apply | `not_applicable`, the path of the event |
| `PATCH_DUPLICATE_TARGET` | two events of the diff name the same path (section 3, row 3a) | `not_applicable`, the path of the later event |
| `PATCH_TARGET_ABSENT` | the path resolves against the schema but A does not hold the position (section 3, row 5) | `parent` of the position |
| `PATCH_CONFLICT` | the value A holds at the path is not `previous` | `value` of the position |

The logical path of the error is the path of the event. The refusals of `edit_scalar` come with the code and the phase of ADR 0012; those of A with their own. Message wording is not a conformance criterion. Every code needs at least one negative vector (the gate rules `CODE_UNUSED` and `PHASE_UNUSED` of ADR 0007 apply).

### 7. The suite

ADR 0005 gets a case kind `patch`: the original text (`input_yaml` or `input_hex`), the schema, the context, the diff (the structured events, and for some cases the canonical string), and either `expect.output_yaml` / `output_hex` (the exact bytes, compared as bytes) with the list of replaced scalars, or `expect.error` (code, phase, source position, logical path). Expected outputs are written by hand from these rules before either language implements the operation (P4-1). The gate rules for the kind are defined with the vectors; the accounting stays exact, and its numbers are expectations written down in advance, never computed from a run.

## Not decided here

Applying `added` and `removed` (insertion and deletion), `set_change`, `policy_declaration_change`, opaque values, a change of representation, a patch of an already-parsed document without text, a merge of two diffs, a three-way merge, a reverse patch, rename detection, authorization, the interpretation of a difference as drift, a command line, writing to disk, and a context digest inside the diff. Each is a later note and ADR.

## Consequences

* Rust and Go get one small contract and a suite that tells them apart before any code exists.
* ADR 0007 is amended in one place (the phase list); ADR 0005 in one place (the kind `patch`). ADR 0008 and ADR 0012 are not changed.
* The patch reuses the proved scalar replacement; its own new code is the checks and the order, which is what the proof pass (P4-3) has to make fail.
* The first unit is deliberately narrow; what it refuses it refuses with a code and a vector.

## Amendment: one path, one event (2026-10-29)

The first unit did not say what a diff with two events on one path is, and the implementations (Rust and Go) accepted it: both events passed row 6, because `previous` is compared with A and not with the result of the event before, and the replacements ran one after the other, so the later one silently overwrote the earlier and `replaced` listed the path twice. A diff that ADR 0008 defines cannot hold this (its events are strictly ordered, one per path and kind, and `ReadDiff` refuses the bytes); only a `PatchDiff` that a caller builds directly can. The contract now refuses it, as a whole, before anything is applied: section 3 gets row 3a and section 6 the code `PATCH_DUPLICATE_TARGET` (anchor `not_applicable`, the path of the later event). It is a new code and not `PATCH_UNSUPPORTED_EVENT` because each of the two events is one the first unit applies; it is the pair that is contradictory.

The input contract of `Patch` is unchanged: a `PatchDiff` holds its events in the canonical order of ADR 0008, and the patch does not check that order (a list in another order applies, and `replaced` follows the list). Only the repeated path is a refusal.

## Acceptance record

Accepted on 2026-10-23 by the owner's decision to merge this ADR (#81, merge commit `c73c5a23`), together with the design note (#80, `22c8c7d6`), the closing record of the first unit of P3b (#82, `bcf3b34d`) and the nightly profile of cic-factory (cic-factory #44, `fc91d6e2`). *Accepted* means this is the normative contract of the first unit of patch and we know what must be built; it is **not** evidence that an implementation exists or conforms. Nothing of it is implemented, and no patch vector exists yet.

The decisions of section 9 of `docs/P4-DIFF-PATCH.md` are taken as proposed there, with the two clarifications of the review that are in sections 3 to 5 above:

1. The invariant is S+: the diff against B is empty **within the context and the scope of the diff**, with the replay rejection and the nothing-partial rule; byte equality with the text of B is not promised.
2. The context is passed by the caller; the patch verifies what it can and takes the rest, that the diff was made under that context, as the caller's trusted binding. ADR 0008 is not changed.
3. The patch is atomic, and a diff with any event the first unit does not apply is refused as a whole.
4. The first unit applies only `value_change` on existing primitive scalars, through `edit_scalar`, text in and text out.
5. The order of work is the one of section 5 of the note: the patch vectors by hand first, the diff by vector group, the patch, the proof pass.
6. The numbers of the accounting move out of the tests into a data file, as expectations written in advance and never computed from a run; this is a separate small pull request that comes before the patch vectors.
7. The first unit of P3b is closed and its later units are open (#82); P4 starts before them because the first unit of patch needs only the scalar replacement.

Applied with this acceptance: the amendment of ADR 0005 (the kind `patch`, and the optional `path_after` of the kind `edit`, which the amendment of ADR 0012 had not named) and the amendment of ADR 0007 (the phase `patch`). Not applied, and not part of the acceptance: the registry `corpus/codes.yaml` (the four codes come with their vectors in step P4-1), and any code.
