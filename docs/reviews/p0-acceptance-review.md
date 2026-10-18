# P0 acceptance review (decision preparation)

State: `main` = `01245aa`. **Draft.** This is a decision-preparation document, not an
ADR. It changes no ADR text or status and ticks no checkbox of issue #3; those follow
the review of this document, in a separate change.

## How to read it

- `Proposed`: a reviewed, versioned contract draft. `Accepted`: the normative decision
  inside the P0 scope was expressly accepted, dependencies and limits documented.
  `Implemented/Verified`: separate. **An `Accepted` ADR means we know what must be
  built; it does not mean the implementation is proven.**
- Every classification below uses one of: **P0** (a semantic question that must be
  settled before acceptance), **P1** (a proof obligation consciously carried into the
  first implementation phase), **upstream** (cannot be solved in this repository).
- The status shown for each ADR is a **review candidate**, not a decision.
- All expectations in the corpus have one author and were cross-checked only against
  throwaway models (not committed). No Rust or Go code exists; the CI proves the
  structure of the corpus (330 cases, 37 rules, 130 seeded faults) and that the
  coverage gate can fail (34 faults, 15 reasons). It proves no implementation.

## Summary

| ADR | Subject | Candidate | Main condition |
|---|---|---|---|
| 0001 | Recognition rule | **Accepted** | none blocking |
| 0002 | Schema contract | **Proposed** until amended | grammar digest byte contract (section A) |
| 0003 | Presence and value semantics | **Accepted** | none blocking |
| 0004 | Inheritance vocabulary | **Accepted, narrowed scope** | scope statement (section D) |
| 0005 | Corpus contract and gate | **Accepted** | editorial consolidation; digest field in pins |
| 0006 | Logical paths | **Accepted after clarification** | Access-attribute boundary (section B) |
| 0007 | Errors and positions | **Accepted** | six codes stay provisional |
| 0008 | Deterministic diff | **Accepted after amendment** | basis boundary (section C) |
| 0009 | Branch coverage measurement | **Proposed** | no qualified tool yet (section G) |
| 0010 | Conformance origin | **Accepted** | P5/P6 verification boundary |

ADR 0000 (the umbrella, still `draft`) needs a decision of its own: it can become an
index that points at the accepted ADRs, with its open points closed or re-homed.

## Per ADR

### ADR 0001: Recognition rule — candidate **Accepted**

| | |
|---|---|
| Decision | A node is Expanded exactly when its key set equals the Access set `A` (six keys, read from the schema); a proper superset is `EXPANDED_WITH_EXTRA_KEY`; anything else is Short, then validated. Recognition looks only at keys. A partial expanded form is a Short form and fails validation; it is never a partial override. `opaque` values are one position; a value that carries the full key set is written under an explicit wrapper. `expand` fills symbolic defaults; `collapse` shortens only when the six fields are the defaults, the intent did not author the long form and the value is not a map containing `A`. |
| Corpus | `corpus/recognition`: 40 cases (30 recognize, 10 collapse; 15 negative) plus the recognize/collapse cases of `presence`; positions in source text. 19 seeded faults touch the file (shared with the error contract). |
| Open | The silent `opaque` case (a short map that equals `A`) is an accepted cost; a warning channel is not defined. The collapse law gains one input in ADR 0010 (the basis of the position). |
| Class | warning channel: **P1+** (not required). Collapse extension: editorial cross-reference at acceptance. |
| Status | **Accepted.** Reason: the rule is simple, closed, covered by positive and negative vectors, and has no unresolved P0 question. Cross-reference ADR 0010's collapse input when recording it. |

### ADR 0002: Schema contract — candidate **Proposed** until amended

| | |
|---|---|
| Decision | What the caller supplies: schema identity and digest, grammar commit and digest, pinned dependencies; the five Shape types; list identity from `role: key` / `item_key` (never `key_fields`); a list element is a record, its fields are positions; a set element is not a position; a case is chosen by field presence and zero or several matches is an error; the surface is named by the caller. Pins: registry `6db77d4`, file sha256 recorded, grammar `devel` `5a26bd8`. |
| Corpus | No suite of its own. Exercised through the `trees` of `paths` and `diff`, the list-record cases of `recognition`, the case-matching cases of `paths` (`PATH_CASE_NONE`, `PATH_CASE_AMBIGUOUS`) and `pins.yaml`. The pin reference path is exercised only by a seeded fault. |
| Open | (1) The **grammar digest** is required by the ADR, but `pins.yaml` carries a commit only and no document defines which bytes the digest covers (section A). (2) `item_choice` exists on `devel` only, not in the released tag `primitives/@v0.2.0`; the ADR calls this a temporary P0 compatibility profile. (3) The instance-document mapping is a proposal, not a citation: no upstream specification of an authored intent document was found. (4) `operation_surface` as a target; fixture rights grant. |
| Class | (1) **P0**, solved by section A. (2) **upstream**, already named as a limit of the profile. (3) **P0 limit**, stated in the ADR (finding 5); acceptable if the acceptance record keeps the wording "a proposal". (4) deferred, **P1+**. |
| Status | **Proposed**, becoming **Accepted** once the digest contract of section A is written into the ADR and `pins.yaml`. Reason: two implementations must start from one context identity, and today that identity is a commit only. The temporary profile must stay labelled in the accepted text. |

### ADR 0003: Presence and value semantics — candidate **Accepted**

| | |
|---|---|
| Decision | `Absent` is not `null`; empty values are typed values; a default applies only to an `Absent` optional field and only as a *candidate* (eligibility by Role is P3); a missing mandatory field on `state_surface` is a coverage fact; engine coverage values (`observed`, `unobserved`, `absent`, `unknown`) are not invented from YAML structure. |
| Corpus | `corpus/presence`: 37 cases (29 validate, 6 roundtrip, 1 collapse, 1 recognize; 19 negative), typed expectations so `Absent`, `null`, `0`, `false`, `[]`, `{}` cannot mix; 17 seeded faults touch the file. The loader invariant (an empty scalar is `null`) is asserted by the gate. |
| Open | Role-dependent default eligibility; mapping of the engine's authored-absent maps onto `Absent`. |
| Class | Role eligibility: **P1+ (P3)**. Engine mapping: **P1+ (P5/P6)**. Neither changes the accepted semantics. |
| Status | **Accepted.** Reason: the distinctions are closed and tested, and the deferred items are consumers of this contract, not parts of it. |

### ADR 0004: Inheritance vocabulary — candidate **Accepted, narrowed scope**

| | |
|---|---|
| Decision | What the library **reads**: the `inherit` key is exactly `true`, `false` or `0` by YAML scalar type (no loose equality), preserved with its type; `inherit_policy` accepts the same three because its description defines `0`; `access` / `modify` are the symbol `inherit` or a list of `CertPattern`, and an empty list (nobody) is not `inherit`; `false` and `0` never become equal. |
| Corpus | `corpus/inherit`: 26 cases (21 validate, 3 roundtrip, 1 equality, 1 expand; 11 negative); 16 seeded faults touch the file. |
| Open | The normative difference between `false` and `0`; the final enum; the `inherit_policy` `boolean`-versus-`0` contradiction; the `access` / `modify` union type; what a "sub-object" is in terms of ADR 0002; the evaluation of inheritance. |
| Class | all of these are **upstream** (cic-primitives #17) except "sub-object", which is **P3**. |
| Status | **Accepted with the narrowed scope of section D**, or kept **Proposed** if the scope statement cannot be agreed. The three `INVALID_*` codes stay provisional either way. |

### ADR 0005: Corpus contract and structural gate — candidate **Accepted**

| | |
|---|---|
| Decision | One case envelope (stable id, kind, polarity, ADR and grammar reference, schema reference, typed expectations, `codes_status`), strict YAML loading (duplicate keys, YAML 1.1 spellings, anchors rejected), `make corpus` with a seeded-fault self-test that requires a fault for every rule. The gate checks structure only and prints that implementation conformance was not run. |
| Corpus | The gate itself: 6 suites, 330 cases (196 positive, 134 negative), 37 rules, 130 seeded faults, every rule demonstrably able to fail; the comparator, loader and numeric invariants are asserted in the self-test. |
| Open | The ADR accumulated five amendments (ADR 0006 to 0010) in one document. The gate needs Python and PyYAML (pinned). `pins.yaml` has no grammar digest (section A). A case against the real pinned schema does not exist. |
| Class | consolidation: **editorial**, at acceptance. Python tooling: **accepted P0 choice**, language-independent of the implementations. Digest field: depends on section A. Real-schema case: **P1**. |
| Status | **Accepted**, with the consolidation and the `pins.yaml` digest field as acceptance conditions. Reason: it is the evidence mechanism for everything else, and its limits (structure only, single author) are stated. |

### ADR 0006: Logical paths — candidate **Accepted after clarification**

| | |
|---|---|
| Decision | A typed path (`Field`, `ListItem` keyed by identity) with one canonical text (`$.nics[{name="a",zone="b"}].subnet`), a strict parser (accepted text is canonical, non-canonical text is an error, never rewritten), one escape set, string and signed-64-bit integer keys only, resolution rules against the schema, path construction from a document with ADR 0002 case matching. Paths survive reordering and short/expanded writing. The engine's `/nics/{...}` syntax is not reinterpreted. |
| Corpus | `corpus/paths`: 125 cases (57 parse, 9 format, 34 resolve, 18 `path_of`, 7 stability; 65 negative); 23 seeded faults touch the file. Measured against the pinned registry: only string and integer keys occur in the Shape format. |
| Open | Addressing Access components (section B); key encodings beyond string and integer; paths through a plain `choice` (no real schema uses one); conversion from the engine syntax; the Rust/Go parser equivalence. |
| Class | Access components: **P0 clarification**, solved by section B. Other key types and plain `choice`: **P1+**, explicit non-goals. Engine conversion: **P1+ (P5/P6)**. Parser equivalence: **P1**. |
| Status | **Accepted** once the sentence of section B is added. Reason: the grammar is closed and measured for the types that exist; the non-goals are explicit errors, not guesses. |

### ADR 0007: Errors and source positions — candidate **Accepted**

| | |
|---|---|
| Decision | A structured error (`code`, `phase`, `source_location` required; `logical_path`, `path_offset`, `operand` where applicable; `message` is not a conformance criterion); eight phases that run in order and stop; one error for parse and the path phases, all errors for recognize and validate, sorted segment-wise by logical path then code; source location only from preserved text, in three states (`{line, column, offset}`, `unavailable`, `not_applicable`), line/column in code points from 1, offset in UTF-8 bytes from 0; a registry of 33 codes with phase and anchor. |
| Corpus | The 134 negative cases across the suites carry structured errors; `corpus/codes.yaml` (33 codes: 27 stable, 6 provisional); the gate checks state per phase and input form, coordinate consistency with the text, ordering on typed segments, and that every registry phase has a vector. 44 seeded faults on the error and registry rules. |
| Open | The six provisional codes (`INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN`, `INVALID_INHERIT`, `PATH_INTEGER_RANGE`, `PATH_KEY_UNSUPPORTED`, `DIFF_UNSUPPORTED_VALUE`); that coordinates point at the intended YAML node on two libraries; anchors for block sequences; message wording; source positions for `path_of` given text. |
| Class | provisional codes: **upstream** (three) and **P1+** (numeric and key encodings). Anchors on two libraries: **P1**. Wording, block-sequence anchors, `path_of` text positions: **P1+**. |
| Status | **Accepted.** Reason: accepting the contract does not require every code to be stable; the registry says which are not and why. The anchor table is a claim the P1 runners must reproduce, and the acceptance record must say so. |

### ADR 0008: Deterministic diff — candidate **Accepted after amendment**

| | |
|---|---|
| Decision | `diff(LogicalView A, LogicalView B, SchemaContext [, Scope])`, A to B, descriptive evidence only; events `added`, `removed`, `value_change`, `policy_declaration_change`, `set_change`; lists paired by identity, no rename invented; opaque atomic; a node appearing or disappearing is one event; short/expanded differences go to a separate `representation` list; the scope is applied before parent/child merging and the diff never infers coverage; canonical bytes are fixed (sorted keys, no whitespace, fixed escapes, 64-bit integers, no floats). |
| Corpus | `corpus/diff`: 66 cases (62 diff, 4 stability; 5 negative); 28 seeded faults touch the file, including int64 range, set order, canonical bytes and event order. |
| Open | The relation to the conformance basis (section C); floats and integers beyond 64 bits (`DIFF_UNSUPPORTED_VALUE` is provisional); schema-dependent order of set values inside fragments; payload order across `item_choice` cases has no vector; patch is P4. |
| Class | basis: **P0 clarification**, solved by section C. Numbers: **P1+** (numeric contract). Fragment set order and `item_choice` payload order: **P1** (needs the schema; add a vector or keep the statement "untested"). Patch: **P4**. |
| Status | **Accepted** once section C's sentence is added. Reason: the contract is closed and verifiable; without the explicit statement a reader could take an empty `changes` for equivalence of evidence. |

### ADR 0009: Branch coverage measurement — candidate **Proposed**

| | |
|---|---|
| Decision | Strictly above 90%, per language, over all own source (exactly 90.00% fails; exact integer arithmetic); branch definition `cic-branch/1` (five categories, `select` and unconditional loops defined); the same requirement, not the same count; a valid measurement is a normalized `cic-coverage-report/1` with tool and version, commit, per-file digests and counts; the gate fails closed and requires exactly Rust and Go; diagnostics never pass; no automatic fallback to statement coverage or mutation score. |
| Corpus | `coverage/`: fixtures and 34 seeded faults covering all 15 failure reasons; a mutant that accepts exactly 90% turns the self-test red. `coverage/tools.yaml` has `selected: null`, so the real check is red by design. |
| Open | The tool per language, the adapters, the golden tests; whether Rust `#[cfg(test)]` modules are own source; the Go side, where no known candidate covers all five categories (`go test -cover` is statement only; `gobco` reports condition coverage). |
| Class | tool qualification: **P0 spike** (narrow, isolated; section G). `#[cfg(test)]`: decided on the spike sample. Go gap: **owner decision** if the contract cannot be met. |
| Status | **Proposed.** Reason: the measurement contract is complete, but point 10 asks for a method that both languages enforce identically, and no qualified method exists. It becomes `Accepted` with an approved tool choice, or by an explicit amendment. |

### ADR 0010: Conformance origin — candidate **Accepted**

| | |
|---|---|
| Decision | The six-key grammar is unchanged. Origin is a trusted context (`origin`, `attestation`, `attested_positions`), not a YAML key. An authored short form expands to a default that is not evidence; an authored long `implemented` is an unauthenticated claim; authored `not_implemented` or `deprecated` is `CONFORMANCE_NOT_AUTHORABLE`; a materialized document may carry all three values only at positions the trusted context attests. The `conformance_basis` (`default`, `authored_claim`, `binding_attested`) follows the context and never the writing; expansion creates no authority; collapse never shortens an attested position. |
| Corpus | `corpus/conformance`: 36 cases (16 validate, 16 basis, 4 collapse; 19 negative), including the three authority scenarios (materialized short stays `default` through expansion; explicit binding datum; identical six fields with different evidence); 16 seeded faults. |
| Open | Verifying the attestation and what makes a binding trusted; subtree entries and per-field signatures; the basis in the diff output; merging a materialized document back into an authored one. |
| Class | verification: **P1+ (P5/P6, Relay)**. Subtree entries and merging: **P1+**. Diff basis: section C. |
| Status | **Accepted**, with the P5/P6 boundary stated in the acceptance record: `binding_attested` is provenance asserted by the trusted input context, not cryptographic verification by the library. |

## Directives for the acceptance review

### A. Grammar digest (ADR 0002)

Proposal: the grammar identity is the immutable commit **plus** a file manifest.

- The manifest is the list of exactly the files the library reads, each with the
  SHA-256 of its **raw blob bytes as stored at that commit**. No YAML normalization,
  no line-ending or whitespace processing before hashing.
- The manifest digest is the SHA-256 of the manifest text: one line per file,
  `<sha256-hex>␠␠<path>\n` (the `sha256sum` format), sorted by the UTF-8 bytes of the
  path. It can be reproduced with `git show <commit>:<path> | sha256sum`.
- The file set is part of the identity: adding or removing a file changes the digest.
- A mismatch between the supplied digest and the content is an error (a context error
  without a source anchor; a code is added to the ADR 0007 registry at acceptance).
- The digest cross-checks the commit; it does not replace it, and it cannot detect a
  grammar that is simply the wrong version (the `item_choice` situation).

Candidate file set (`cic-primitives` at `5a26bd8`), measured now:

| SHA-256 (raw bytes) | Path | Role |
|---|---|---|
| `09fb8a8e…942d` | `schemas/atomic/access.yaml` | the key set `A`, symbolic defaults |
| `3faf92f5…bea7` | `schemas/atomic/shape.yaml` | Shape, presence, nullable, default |
| `71fc185a…9659` | `schemas/atomic/role.yaml` | Role axes |
| `f80fb77b…9912` | `schemas/aggregate/policy-surface.yaml` | passed through, evaluation order |
| `fdb5e4e5…8f0b` | `proposals/atom-grammar/instance-grammar.schema.yaml` | `item_key`, `item_choice`, collection types |
| `e6d013d9…248b` | `proposals/atom-grammar/check_grammar.py` | the C-rules (list key, C17) |

Full hashes are reproducible with the command above and are not copied here as
authority; the acceptance change records them in `pins.yaml`. **Decision for the owner:**
`proposals/atom-grammar/README.md` (`146006b1…1629`) is prose and is cited by ADR 0002 as
the source of the normative form. The recommendation is to leave it out of the digest
(a typo would change the identity) and to require that everything the library
implements is derivable from the machine-readable files; if some rule exists only in
that README, the rule must be moved or the README included.

### B. Access components in paths (ADR 0006)

Proposed sentence for ADR 0006 at acceptance:

> A logical path identifies a primitive position. `access`, `modify`, `inherit`,
> `default_injection` and `conformance` are attributes of that position, not path
> segments and not a further addressing level. No selector for them is introduced
> without demonstrated need and a separate contract.

This keeps the existing statement that the Access keys are not field segments, and it
leaves the diff's `policy_declaration_change` carrying the attribute name inside the
event (field `changed`), so no path level is needed.

### C. Diff and the conformance basis (ADR 0008)

Proposed sentence for ADR 0008 at acceptance:

> `conformance_basis` is provenance companion data of the LogicalView (like the authored
> form). It is not a `changes` event and not part of logical equality; the
> intent-observed adapter reads it separately. `changes == []` means **logical equality
> only**. It asserts nothing about provenance, attestation or device capability
> equivalence.

### D. Scope of ADR 0004 if it becomes Accepted

Two questions must never be merged:

1. *Can the library read `true`, `false`, `0` and the symbol `inherit` without loss?*
   This is what ADR 0004 specifies and what would be accepted.
2. *Which effective permission follows from them?* This is **not** decided. It needs the
   normative semantics of `false` versus `0`, the enum and the union type, all open in
   cic-primitives #17.

Proposed scope statement for the acceptance record:

> ADR 0004 is accepted as the library's **reading contract** for the `inherit` key and
> for the `access` / `modify` / `inherit_policy` values: type-strict, lossless, `false`
> never equal to `0`, an empty list never equal to `inherit`. It does **not** define the
> meaning of `false` versus `0`, an enum, the evaluation of inheritance or any effective
> permission; those wait for the upstream decision, and the codes `INVALID_ACCESS_VALUE`,
> `INVALID_CERT_PATTERN` and `INVALID_INHERIT` stay provisional. Accepting it must not be
> read as accepting an inheritance semantics.

Checkbox 8 of issue #3 should not be ticked on this basis: its text asks for the type of
the symbolic `inherit` in the union, which is upstream. Either the box stays open until
cic-primitives #17 decides, or its wording is narrowed to the library reading by the
owner.

## E. Mapping to the checkboxes of issue #3 (for the later change; nothing is ticked now)

| Box | Tick when |
|---|---|
| 1, 7 | ADR 0001 is `Accepted` |
| 2 | ADR 0006 is `Accepted` with the section B sentence |
| 3 | ADR 0003 is `Accepted` |
| 4 | ADR 0002 is `Accepted` with the digest contract |
| 5 | ADR 0008 is `Accepted` with the section C sentence |
| 6 | ADR 0007 is `Accepted` |
| 8 | upstream decision, or owner narrows the wording (section D) |
| 9 | ADR 0010 is `Accepted` |
| 10 | tool chosen and approved after the spike (section G) |
| minimal schema model | ADR 0002 is `Accepted` (the code-level model is P1) |
| corpus skeleton | ADR 0005 is `Accepted` |
| seeded faults | reworded as agreed: existing P0 gates have them (done); each implementation gate gets them when it enters CI |

## F. Open upstream dependencies and P1 obligations

Upstream (cic-primitives #17 and related): the `inherit` enum and the normative
`false`/`0` difference; the `inherit_policy` type contradiction; the `access` / `modify`
union; `item_choice` missing from the tag `primitives/@v0.2.0`; the normative grammar
for PolicySurface resolution.

P1 proof obligations, carried consciously:

- Rust and Go runners reproduce the whole corpus (the largest open risk: one author,
  throwaway models only).
- Source-position anchors agree on two YAML libraries; the parser equivalence of
  ADR 0006.
- A numeric contract (floats, integers beyond 64 bits, `number`, `bytes`).
- Schema-dependent set order in diff fragments; `item_choice` payload order.
- A case against the real pinned schema.
- Line coverage, mutation score, parity, property and fuzz gates, each with a seeded
  fault when it enters CI.

## G. Preparing the coverage spike (nothing is built now)

The spike is isolated, time-boxed and is **not** the start of the Primitive Forms
implementation: no production code, outside the scope manifest, discarded or archived
afterwards. It runs only after the status decisions above are recorded, against a closed
requirement set. Acceptance criteria, per language (Rust, Go):

1. A sample whose branches are counted **by hand** into a golden table per category:
   Rust `match` with guards, `if let`, `while let`, `loop` with `break`, `for`, the `?`
   operator, `&&` and `||`; Go `if`, `switch`, type switch, `select` with and without
   `default`, `for` (condition, range, unconditional), `&&` and `||`, `if err != nil`.
2. The tool pinned by name, version and a reproducible install in CI.
3. An adapter from the raw tool output to `cic-coverage-report/1`, with a golden test:
   the report equals the hand-counted table, category by category.
4. Determinism: two runs give byte-identical reports.
5. A real fault: removing one test takes the sample below the threshold and a real CI
   run turns red (the run is linked in the exit report).
6. The gate rejects a missing report, a disabled measurement and an unselected tool on
   the real workflow, not only on the fixtures.
7. Every category the tool does not measure is listed; an `approved_deviations` entry
   exists only with the owner's recorded decision.
8. If a language cannot meet the contract (the Go side is the likely one), the exit report
   states the gap and asks for the separate decision. No automatic fallback.
9. An exit report with the findings, the tool and version proposed, and what remains
   unproven. `coverage/tools.yaml` is changed only after the owner approves it.

Local note: this machine has neither `cargo` nor `go`; the spike needs a CI job or a
container, which is why no environment work starts with this document.

## H. Residual risks the acceptance record should name

- One author for all expectations; throwaway models are not committed. P1 runners are
  the independent check.
- Positions assume the same node start marks on two YAML libraries.
- The compatibility profile for `item_choice` is temporary.
- The corpus tooling is Python and is a P0 structural tool only.
- `Accepted` means *what to build*, not *proven built*; no checkbox may be read otherwise.

## Outcome of the review (owner decisions)

Recorded in the acceptance change that follows this document.

| ADR | Decision |
|---|---|
| 0001, 0003, 0005, 0007, 0010 | Accepted |
| 0004 | Accepted, narrowed to the reading contract (section D text) |
| 0006 | Accepted with the section B sentence |
| 0008 | Accepted with the section C sentence |
| 0002, 0009 | stay Proposed |

Section A is overridden on one point: **`proposals/atom-grammar/README.md` is part of the
grammar manifest** (seven files), as the normative source of the Shape structure; it can
leave only through a new versioned identity contract. The abbreviated hashes of section A
are not authoritative; the full manifest is reproduced in CI by the implementing change.
Box 8 of issue #3 is not ticked and its wording is not narrowed. ADR 0000 stays `draft`
and becomes a living index. The coverage spike stays a separate task.

