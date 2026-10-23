# ADR 0013: Patch - the first unit

Status: **proposed** (2026-10-24). It is step P4-0 of `docs/P4-DIFF-PATCH.md`, which holds the reasoning, the order of work and the questions that are the owner's; this ADR holds only what has to be
fixed as a contract so that Rust and Go can implement and test the same thing. It is **not accepted** until the owner merges it, and the reviewer's professional GO is not the acceptance. It re-opens none of ADR 0001-0012: the diff
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

1. The earlier phases on A, as they are today: `parse`, `validate`, and the path operations. A document that does not validate is refused with its violations.
2. `PATCH_CONTEXT_MISMATCH`: **only what the patch can check** (section 5).
3. The diff as a whole: an event that section 2 does not apply is `PATCH_UNSUPPORTED_EVENT` (the first in the canonical order of ADR 0008).
4. For each event in the canonical order of ADR 0008: the path must resolve against the schema and A must hold the position (`PATCH_TARGET_ABSENT`), and the value A holds must equal `previous`, type-strict (`0` is not `false`, `null` is not absent) (`PATCH_CONFLICT`).
5. The replacements, in the canonical order, each by `edit_scalar` on the result of the one before. Positions that are independent commute (ADR 0012, guarantee 6); a change to the key field of a list element is not among the applied events, so no application changes the address of another.

All of 1-4 happen before the first byte is changed. If any check or replacement fails, the response is the error and **no text is returned**, also when a working copy was edited on the way.

### 4. What a success guarantees (normative, and checked on every call)

For `d = diff(A, B)` made under one context and one scope:

1. *Round trip, within the scope.* `diff(patch(A, d), B).changes` is empty under the same context and the same scope as `d`. With the complete scope that is logical equality; with a partial scope it is equality of the **observed region only** (ADR 0008: what was not observed is not absent). The patch claims nothing about the region outside the scope.
2. *Minimality.* The result differs from A only in the replaced scalars: the bytes before the first and after the last replaced span, and between them, are A's (ADR 0012, guarantee 1), in every style, comment and line ending.
3. *Replay rejection.* A diff with at least one effective change, applied to its own result, is `PATCH_CONFLICT` (the `previous` no longer matches). A diff with **no** changes is not a conflict: `patch(A, empty diff)` is A byte for byte.
4. *Nothing partial.* As in section 3.
5. Rust and Go give the same bytes, the same list and the same error for the same input.

**Byte equality with the text of B is not promised and cannot be**: the diff ignores order, style, quoting, comments and short versus expanded writing, so the text of B is not a function of A and the diff.

### 5. The context: what is verified and what is taken on trust

A diff's canonical bytes carry no schema context, and this ADR does not add one. The patch therefore **cannot verify under which context a diff was made**.

* *The patch verifies itself:* A parses and validates under the `schema` and the `context` the caller passes; the version of the diff is known (`1`); every path resolves against the schema; `previous` and `next` are of the kind the position has; the value A holds equals `previous`.
* *The patch takes from the caller, as a trusted binding:* that the diff was computed under that same context. `Context supplied` is not `Context verified`, and `previous matches` is not `diff provenance verified`.

`PATCH_CONTEXT_MISMATCH` is therefore raised only for what can be checked: A is not readable under the given context, or the version of the diff is not `1`. It never claims that the diff was made under another context. A binding of a diff to its context, if wanted, is the work of a separate trusted envelope (an integration contract), not of this ADR.

### 6. Errors: the phase `patch` and its codes

ADR 0007's phase list gets a tenth phase, `patch`, which runs after the earlier phases have accepted A. It reports **one** error, the first failing condition in the order of section 3.

| Code | Condition | Anchor |
|---|---|---|
| `PATCH_CONTEXT_MISMATCH` | A is not readable under the context given, or the version of the diff is unknown | `not_applicable` |
| `PATCH_UNSUPPORTED_EVENT` | an event or a target that section 2 does not apply | `not_applicable`, the path of the event |
| `PATCH_TARGET_ABSENT` | the path does not address a position that A holds | `parent` of the position |
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

## Acceptance record

Not yet accepted. The owner's decisions of section 9 of `docs/P4-DIFF-PATCH.md` (the invariant, the context, atomicity, the reach of the first unit, the order of work, the accounting, the closing of P3b) are taken when this ADR is merged, and are recorded here then.
