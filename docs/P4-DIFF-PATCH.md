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
| `diff` in Rust or Go | **does not exist** |
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
| **D1** | `diff`, step one: the logical view (expanded form, identities, sets), scalar and declaration changes, representation, opaque values, the canonical bytes; the vectors of those groups run in both languages |
| **D2** | `diff`, step two: composites, lists by identity, sets, appearing and disappearing nodes as one event |
| **D3** | `diff`, step three: the scope, the errors (`DIFF_CONTEXT_MISMATCH`, `DIFF_UNSUPPORTED_VALUE`), the stability cases; all 66 vectors run; the Rust-against-Go differential and its self-test |
| **P4-2** | `patch`, first unit, in Rust and Go on top of D1-D3 and `edit_scalar` |
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
