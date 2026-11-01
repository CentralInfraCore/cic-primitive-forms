# P5: materialize and receipt - the first units

Status: **design, no code.** It follows the accepted ADR 0001-0013 and the merged P1-P4 behaviour; it re-opens none of them and changes **no** existing API. It names what P5 has to
produce, finds what the sibling repository `cic-primitive-engine` has already decided about it, and proposes an order of work and a first, narrow unit. The points that are the
owner's are in section 8. Nothing here is evidence that a producer exists.

P5 in `PHASES.md`: *Resolve (from supplied snapshot), materialize, receipt. Exit: Complete, validated object with provable input/output binding.* The row leaves open who checks
the binding and what "provable" is proved against. This note answers both from the contract that already exists (sections 2 and 3), and keeps Resolve out of the first units (section 5).

## 1. What this library has, and what the row needs

| Piece | State |
|---|---|
| parse, validate against a pinned schema, the access-policy reading | merged (P1) |
| expand / collapse with the origin (`authored` or `materialized`) and the attestation (ADR 0010) | merged (P2) |
| `normalize`: the declared defaults of absent fields, with the **origin of every value** (`value_origins`: authored or default, per position, in path order) | merged (P3) |
| the effective access / modify rule per position (`PolicyResolution`); `unknown` as a caller input, "a P5 snapshot-reference not resolved" (`docs/P3-UNIT4-POLICY-RESOLUTION.md`) | merged (P3) |
| diff, patch, the reader of the canonical bytes of a diff | merged (P4) |
| a canonical byte form of a **document** for digesting; a digest; a receipt; resolving a reference; a snapshot | **none** |

`normalize` already keeps what a receipt must list as `applied_defaults`: which values the schema, and not the author, put there. That is the one piece of P5 that exists today.

## 2. What `cic-primitive-engine` has already decided (checked at commit `93c793a`)

The engine repository holds the contract of the object that crosses into a module and of the receipt that binds it. It does **not** build the pipeline that produces them.

* **The scope correction** (`docs/MATERIALIZATION-SPEC.md`): `Parse`, `Normalize`, `Resolve` and `Validate` are "permanently the environment's job". The engine receives a candidate
  object, checks it (`MaterializedObject::try_new`, the Complete property), canonicalizes it (section A) and digests it. This library is that environment.
* **No receipt producer exists** in the engine, "or, as far as this repository's own code can show, anywhere else in the CIC ecosystem" (`docs/RECEIPT-SCHEMA.md`, status note;
  `docs/RECEIPT-VERIFIER.md`: "No receipt producer exists in this crate"). Producing it is the open obligation, and P5 is the place that can meet it.
* **The receipt contract is closed** (`docs/RECEIPT-SCHEMA.md`): a sibling artifact bound by digest, under one wrapper key `materialization`, 15 required fields, none omitted by
  absence: `receipt_schema_version` (integer 1); `grammar_sha256`, `grammar_schema_sha256`; `primitive_release`, `primitive_source_commit`, `primitive_build_hash`; `schema_name`,
  `schema_version`, `schema_digest`; `engine_identity`, `engine_version`; `input_digest`, `output_digest`; `applied_defaults[]` (`path`, `rule` = the literal `schema-default`,
  `value_digest`) and `derived_values[]` (`path`, `rule` = `name-vN`, `inputs` = a deduplicated sorted path set, `value_digest`). Both lists are required arrays, `[]` when empty, sorted
  ascending by `path` in raw UTF-8 byte order, a repeated path is a rejection. No signature field (a signature, if one is ever wanted, is a separate envelope).
* **A verifier exists** in Rust (`engine/src/receipt.rs`) and Go (`go/receipt`), with a shared differential corpus (`conformance/differential/receipt`, 35 vectors: a changed
  digest, a missing field, unsorted or repeated evidence, a missing path binding, evidence that disagrees with the object). A success establishes consistency of the receipt with
  the object it is handed and nothing about the execution: "`rule` and `inputs` are the producer's statements".
* **The byte form is decided** (section A of the spec, A1-A7; `engine/src/canonical.rs`, `go/canonical`; vectors in `conformance/canonicalize`): sorted keys, number forms, string escaping,
  array order as given, `sha256:` + lowercase hex of the canonical bytes. `input_digest` is the digest of the canonical form of the **authored input as a processed value, not the raw source
  bytes**; `output_digest` is `materialized_object_digest` of the object, which includes capability, coverage and provenance (B7).

## 3. The six places where the contract and this library do not meet

These are found, not guessed; each is a question for section 8, and none is decided here.

1. **Flat keys against a tree.** The engine's `MaterializedObject` is "keyed by flat field names"; a receipt names a field by a `$.`-rooted path. "The mapping between them is not
   specified anywhere in the contract", so the verifier takes a `PathBinding` (receipt path to object key) from the caller and refuses an unbound evidence path (`E_RECEIPT_UNBOUND`).
   The documents of this library are trees (composites, lists identified by a key). Who defines the binding, and how a nested position or a list element becomes a key, is open.
2. **The path form of a list element.** Logical paths here (ADR 0006) write `$.routes[{to="b"}].metric`. The receipt says "`$.`-rooted with non-empty segments". Whether the verifier
   accepts the selector form is not stated; it has to be measured before a design depends on it.
3. **A canonical form of a document does not exist here.** This library has a canonical writing of a diff (ADR 0008) and of nothing else. `input_digest` and `output_digest` need
   the engine's section A over a processed value, bit for bit; numbers (A3: integers beyond 64 bits, exact decimal ties) and strings (A4: no Unicode normalization, named gap) are where two
   implementations diverge. This is the largest accuracy risk of P5, and the part that can be proved first and alone.
4. **Which facts an intent-side document carries.** The object has `capability` (implemented, not_implemented, deprecated), `coverage` (observed, absent, unobserved, unknown) and
   `provenance` (authored, schema_default, derived) per field, with `FieldEvidence` allowing exactly three combinations. This library knows `provenance` (authored or default, from
   `value_origins`) and, through ADR 0010, the `conformance` capability and who may author it. `coverage` is observation-side; an intent document has none. `derived` needs a derivation
   rule that does not exist in this library at all (the engine's example, `effective_state`, is state-side).
5. **The identifiers (D1).** `grammar_sha256` and `grammar_schema_sha256` are the engine's hashes of two files of a pinned `cic-primitives` release (base64 in the engine's own
   `dependency.yaml`, `sha256:` hex in the receipt: a re-spelling the verifier checks is "equal bytes" and nothing else). This library pins the grammar by commit and a manifest of ten files
   (`corpus/pins.yaml`), another identity. `engine_identity` names "the implementation that materialized the data".
6. **The value range.** The value of this library holds `null`, a boolean, a signed 64-bit integer, a string, a sequence and a map (`Value` in `rust/src/node.rs`, `Kind` in `go/node.go`); a float or an integer
   beyond 64 bits is `Unsupported`, which the parser never produces. The engine's canonical form carries both (`Value::BigInt`, `Value::Float`; its vectors `large_integer_beyond_i64`, `float_exact_decimal_tie`,
   `float_extreme_magnitude`, `negative_zero`, checked at the same commit). P5-1 therefore cannot reproduce all of the engine's `canonicalize` vectors without widening the data model. Also, section A keeps
   array order "as given": a collection that is a set here has to have a defined order before it is digested, and this note does not know that order yet (decision H).

## 4. What P5 has to produce, in the engine's terms

Given an authored document, a schema and a pinned grammar, produce **(a)** a candidate `MaterializedObject` (with its evidence), **(b)** its canonical bytes and digests, **(c)** a receipt that
the engine's verifier accepts for exactly that input and that output, and refuses when either is changed. Resolve (a reference found in a supplied snapshot) is a step before (a) and is the
last of the units.

## 5. Order of work

The order is "the part that can be proved from outside first", because the engine's verifier and vectors are the oracle and do not depend on the rest.

| Unit | What | Proved against |
|---|---|---|
| **P5-1** | the canonical byte form (A1-A7) and the digest, in Rust and Go, for a **processed value** of this library (the `Node`) | the engine's `conformance/canonicalize` vectors, byte for byte; a Rust/Go differential on generated values; the engine's `digest` vectors |
| **P5-2** | `materialize`, without Resolve: a document that has no reference becomes the candidate object and its evidence; `applied_defaults` from `value_origins`; `derived_values` is `[]` | the object's own invariants (Complete); a differential Rust/Go; the engine's `try_new` on it |
| **P5-3** | the receipt: the 15 fields, the ordering rules, the two digests | **the engine's verifier accepts it**, and refuses a changed input, a changed output, a reordered or repeated evidence, a wrong version; the engine's `receipt` vectors reproduced |
| **P5-4** | Resolve from a supplied snapshot (needs a decision on what a snapshot is) | not planned here |

P5-1 is first because everything else is an equation over its bytes; it has an external oracle and no other dependency; and the most likely finding of the whole phase (a number or a string
that the two languages, or this library and the engine, write differently) shows up there at the smallest cost.

## 6. The proof of the phase

* **The external check is the gate.** A receipt produced here is verified by the engine's own verifier in CI, against a pinned engine commit (as `cic-primitives` is pinned today). A producer that is only consistent with its own
  reading of the contract proves nothing; the verifier is the second reader. The tamper cases (a digest changed, an entry moved) must be seen to turn it red.
* **Two implementations, one corpus, and a differential** on generated documents, as in P3 and P4; a self-test that breaks the dumps; planted defects; fuzz targets on the new inputs.
* **The quality gates** of the repository (line, branch, mutation above 90% in both languages) apply from the first code.
* **A difference between the two languages, or between this library and the engine, is decided before it is fixed** (which side is wrong, or the contract is unclear), and is never put on a list
  of known differences.

## 7. What this note does not do

It does not resolve a reference, define a snapshot, decide a request, verify an attestation or sign anything. The attestation of a materialized origin stays the caller's (ADR 0010). It does
not change the engine's contract; where the contract is silent (section 3, points 1 and 2) it asks, it does not fill the silence. It writes no code.

## 8. The decisions that are the owner's

| # | Question | Proposed here |
|---|---|---|
| A | Is the engine's verifier, at a pinned commit, the gate of P5, run in this repository's CI? | **Yes.** It needs a small harness (the engine has no command-line entry for it: `cli` does not call the receipt verifier), in Rust against the pinned checkout, and in Go the same |
| B | Who defines the `PathBinding`, and how does a tree position become a key? | **The engine side**, in its contract, with this library asking: the binding is part of what the receipt means. Until then P5-3 uses the case the verifier accepts without one (no defaulted and no derived field), and says so |
| C | The path form of a list element in a receipt | **Measure first** with the verifier (section 3, point 2); if it refuses the selector form, ask the engine side |
| D | `engine_identity` and `engine_version` | the identity of **this** implementation, one per language (`cic-primitive-forms-rust`, `cic-primitive-forms-go`) and its version; the two receipts for one input then differ in these two fields, and the digests do not |
| E | The grammar identifiers (`grammar_sha256`, `grammar_schema_sha256`, `primitive_*`) | taken from the same pinned release the engine names; where this library's pin (a manifest of ten files) is not that identity, the receipt carries the engine's, and the mapping is written down |
| F | What a snapshot is, for P5-4 | **Not decided, not needed before P5-3.** A statement that it is out of the first units is what this note and the ADR ask for |
| G | The value range of P5-1 (point 6) | **Narrow it, with a written limit:** P5-1 covers the values this library holds (null, boolean, `i64`, string, sequence, map). The engine vectors outside it (`BigInt`, float) are listed as out of range, each by name, and a value that is `Unsupported` is refused by the canonicalizer with a named code, never approximated. Widening the data model is a separate decision of its own |
| H | The order of a set in the canonical form | **Open.** Measure what the engine's vectors and `MaterializedObject` expect, then decide; P5-1 may not digest a set until it is decided |

### Record of the answers (review of PR #117, 2026-10-30)

An external review of this PR answered the decisions; the owner's merge is what accepts them. A: accepted (pinned commit, Rust and Go). B: the engine's contract defines the binding, and it is settled before P5-2/P5-3 are closed.
C: proved by a targeted verifier test. D: one identity per language accepted. E: same pinned release, with a checked mapping. F: deferred to P5-4. G: as proposed above. H: open.

### Record of the owner's decisions (2026-10-30, after P5-1)

* **H, the order of a set: A, fail closed.** The first unit of P5-2 handles no collection of set topology. It is not enough to leave sets unsorted: two documents that differ only in the order of the elements of a set are one logical state and
  would get two digests (two identities for one state). So P5-2 must (1) read the topology of every collection from the schema, (2) stop with a controlled error, before any semantic digest is made, at a set, and (3) never skip a set silently.
  The generic, order-keeping canonical writer (P5-1) is unchanged: byte-level and semantic canonicalization stay apart. The final rule for sets belongs to the engine's contract and to an ADR of its own.
* **A to G of section 8:** accepted as recorded above. **B and C** need, before P5-3 is closed, a machine-checked contract each: that the verifier accepts a path form is not a proof that the path is bound to the right key.
* **Go `CanonicalBytes(nil)`:** an error, not a panic: the reason `nil_node`, for a nil node at the top and inside a sequence or a map (not a code of ADR 0007; Rust cannot hold a nil reference). New code relies on no panic.
* **The nightly FULL on a change of `p1.yml`:** stays as it is (fail closed). Narrowing it needs a classifier that proves a workflow change does not touch the FULL, and runs the FULL when it cannot tell: a task of its own.
* **#111:** deferred. **#115:** a separate fix, before P5-2 (pull request #121).

## 9. Risks

* The canonical form (point 3) is a byte-exact contract against a repository that evolves: a change of section A there moves a digest here. The pin and the vectors are the defence; the
  cost is a migration when the pin moves, as the grammar pin already is.
* The mapping of a tree to flat keys may not have a faithful answer for list elements (point 1); that would be a finding about the contract, not a defect to patch around here.
* `engine_identity` makes two correct receipts of one input differ; a consumer that compares receipts by bytes will see that.
* No signature: the receipt proves consistency, not authorship (the engine says so). Anything that needs authorship is a separate envelope and another phase.

## 10. P5-1: what is built (2026-10-30)

`canonical_bytes` / `canonical_digest` (Rust, `rust/src/canonical.rs`) and `CanonicalBytes` / `CanonicalDigest` (Go, `go/canonical.go`): engine section A (A1-A7) over the value of this library, within the range of decision G.
A float, an integer beyond 64 bits, a repeated key (and, in Go, a string that is not UTF-8) have no canonical bytes: the refusal is a `CanonicalError` with the reason `unsupported_value`, `duplicate_key` or `not_utf8`; none is a code of the registry of ADR 0007.

**Proved:** the table `testdata/p5/canonical-vectors.tsv` (27 lines) is read by both languages. Its 7 in-range lines from the engine's `conformance/canonicalize` are checked against the engine at the pinned commit
(`testdata/p5/engine-pin.txt`, `tools/p5/engine_vectors.py`, a step of the `rust` job); a new engine vector fails that check until it is classified, so the range is never widened or narrowed unseen. Its 13 own lines are checked against a
reference written apart in that tool (a third reader). The 6 engine vectors outside the range are listed by name as `out_of_range`, and both languages assert that the parser refuses them. The Rust side passed the table and 23 of 23 mutants of `canonical.rs`
(run locally); the Go side has not been run here (no Go toolchain), so its first run is CI.

**The rest of P5-1 (2026-10-30):** a Rust/Go differential on 20 000 generated values (`tools/parity/gen_canonical.py`, `compare_canonical.py`, `selftest_canonical.py`, the dumps `rust/tests/parity_canonical_dump.rs` and
`go/parity_canonical_dump_test.go`). Each value comes with the answer of a reference written apart from both implementations, so a defect shared by the two languages is seen too; there is no list of known differences. The reference
also fixes which fault is met first (a repeated key of a map before its members, then the members in the order of the sorted keys). The fuzz targets `FuzzCanonical` (Go) and `canonical` (Rust): a text that parses has canonical bytes
that are one line of valid UTF-8 (JSON, in Go), carry the digest of A7, and are unchanged by `emit` and `parse`. Run here: the differential agrees on all 20 000 and its self-test turns red on all 12 breaks; the Go fuzz target ran 296 000
executions without a failure; the Rust target's logic ran on its seeds as a test (no nightly toolchain here for `cargo fuzz`, so CI is its first real run).

**Planted defects (2026-10-30):** 20 entries "canonical:" in `tools/seeded_defects.py` (10 per language: keys unsorted, sorted by UTF-16 code unit, a repeated key not refused, an unsupported value written as null, `<` and U+2028 not escaped, the last control character raw, the newline escaped as `\u000a`, the digest without its prefix, a sequence reversed or, in Go, a non-UTF-8 string written). Each turned the table and the unit tests red (the first run showed one Go entry that did not build, an unused import; it is fixed). The UTF-16 entry needed a vector the table did not have: `own_key_order_is_bytes_not_utf16`.

**Not yet:** the engine's `digest` function checked beyond the empty mapping, (`CanonicalBytes(nil)` in Go is no longer a panic: it returns `nil_node`, PR #122). The order of a set (decision H) is not touched: a sequence is written as given.

## 11. P5-2 pre-check (2026-10-30, before any code)

Read at the engine commit `93c793a`: `engine/src/materialized.rs`, `digest_projection.rs`, the differential vectors `conformance/differential/{materialized,digest}`; and on this side `normalize` / `value_origins`, ADR 0010.

**What the engine requires of the object.** A `MaterializedObject` is a map from a **flat field name** to a `MaterializedField { capability, evidence }`; `capability` is one of `implemented`, `not_implemented`, `deprecated` (no "unknown"); `evidence` for an intent-side field is `Authored(Option<value>)`, `SchemaDefault(value)` or `Derived(value)`. `try_new(fields, expected_keys)` accepts only when the key set **equals** the expected one (the Complete property; `E_INCOMPLETE_OBJECT`). The `output_digest` is the digest of the projection `{"fields": [{key, capability, provenance, value?}]}` sorted by key; an `Authored(None)` has no `value`. (The prose of the spec says the value is "wrapped in the Access atom's structure"; the type and the projection, which are what the verifier and the vectors check, carry the plain value. The code is followed.)

**What this library already gives.** `provenance`: `value_origins` says `authored` or `default` per declared position, which are `Authored` and `SchemaDefault`. Whether an optional field the author left out and that has no default is the engine's `Authored(None)` is **not established**: the engine type can hold it, but its spec (B2) leaves the representation of an authored absence open, and `provenance` is part of the digested claim. See point 6. Everything outside the collections is in the supported value range (P5-1).

**Where they do not meet** (each is a question; none is decided here):
1. **Capability has no honest value for an intent document.** ADR 0010: an authored position's capability is not evidence ("never taken to be `implemented` automatically", basis `default` or `authored_claim`); only `binding_attested` establishes one. The engine's enum has no "unverified", and a field must carry one. Writing `implemented` for every authored position would turn a default into a claim inside a digest (B7 commits the capability to the `output_digest`).
2. **The flat key.** A document here is a tree. The engine's key is a flat field name and "the mapping is not specified" (section 3, point 1). A first unit cannot invent it.
3. **The expected key set.** `try_new` needs the keys the schema declares. That is the set of declared positions of the compiled schema, including an optional field the author left out; this library has the declared positions (they are what `apply_defaults` walks), but not yet as a list a caller can hand to the engine.
4. **The oracle needs a harness.** The engine has no command that runs `try_new` or `materialized_object_digest` (its CLI only reads), so the check against the engine is a small program per language in this repository's CI, built against the pinned engine checkout (decision A).
5. **Collections.** A set stops the materialization (decision H, fail closed); a list element has no flat key (point 2).
6. **An omitted optional field (open contract question).** A field left out of the document with no schema default is not shown to be the same thing as an authored absence (`Authored(None)`); the engine spec B2 leaves it open. Until the engine settles it, the first unit does not guess: a document with such a position is refused (controlled error), and only positions that are present or filled by a schema default are materialized.
7. **Complete is circular on its own.** `try_new` only compares two key sets; it does not check that the expected set comes from the schema. If this library drops an optional position from both the fields and the expected keys, the engine accepts the incomplete object.

**Proposed first unit of P5-2** (a proposal, not a decision): materialize a **flat** document, that is a mapping whose declared positions are all top-level scalars with no collection, the key being the field name. A nested composite, a list and a set stop with a controlled error before any digest (the set by decision H, the others because point 2 is open). Capability is **an input of the caller** (a map from field to one of the three values, from the binding data), and a field for which the caller gives none stops the materialization (fail closed, as point 1 requires). The output is the candidate object with its evidence and the `output_digest` of the projection, plus the projection as JSON, so that the harness can build the engine's object from it.

**Proof of the first unit:** the harness (Rust and Go, against the pinned engine) builds `MaterializedObject::try_new` from the JSON and computes `materialized_object_digest`; this library's digest must equal it, and a key set that differs (a missing optional field, an extra key) must be refused by `try_new` and by this library alike. The expected key set is derived from the compiled schema and checked independently of the materializer that builds the object (a second derivation, compared), so that the engine's green `try_new` is not the only witness of completeness. Injected defects: a declared required or optional key dropped from both sets, and an extra key added, must each be caught. The engine's `digest` differential vectors are reproduced on the projection. A Rust/Go differential on generated flat documents, with the engine as the third reader instead of a reference of our own; planted defects; the quality gates.

**Answers (owner, after review of this note)**: **I** a caller input, but checked adapter/binding data, fail closed when missing or unattested; no `implemented` is derived from an authored field (and the caller's value and its authenticity are two claims; the digest records the first only). **J** yes, flat first: top-level scalars; nested, list, set and an unresolved omitted field are controlled refusals. **K** the harness lives in this repository's CI: pinned engine, Rust and Go, with negative tests of its own.

**Questions for the owner (asked)**
* **I.** Capability as a caller input with fail-closed on a missing value (the proposal), or a fixed rule (for example `implemented` for every position not attested, which is the `default` basis of ADR 0010 but writes a non-evidence into a digest)?
* **J.** A first unit limited to flat documents, with the controlled error for everything nested, or wait for the engine side to define the flat key (decision B) before any code?
* **K.** The harness inside this repository (a small Rust program with a path dependency on the pinned engine checkout, and a Go program), as decision A already says, or a CI job of the engine that runs against a fixture file this library produces?

## 12. P5-2 first unit: what is built (2026-10-30)

`materialize_flat` / `MaterializeFlat` (`rust/src/materialize.rs`, `go/materialize.go`): the flat document of section 11, with the owner's answers I, J and K. The module comments are the contract; in short:

* **Flat only.** Every declared position of the schema is a top-level scalar. A composite, a list and a declared list are `not_flat`, a set is `set` (decision H); the refusal is of the schema, so a nested position that the document leaves out still refuses.
* **Capability is the caller's.** A map from field to `implemented`, `not_implemented` or `deprecated`; `capability_missing`, `capability_unknown` (also `unverified`, which the engine does not have), `capability_undeclared`. Nothing is derived from the authored document. The digest records what the caller gave; the caller's entitlement to it is a second claim that the digest does not make.
* **An omitted optional field with no default is refused** (`omitted_optional`), because the engine's `Authored(None)` is not shown to be the same thing (point 6 of section 11). Provenance is `authored` or `schema_default`; a value written equal to its default is `authored`.
* **Output:** the fields sorted by key, `expected_keys` (read from the schema, sorted), the projection as canonical JSON and the `output_digest`.
* **A nil node (Go only).** `nil_node`, naming the top-level field that holds it, wherever it is below it. The Go validation reads a nil value as an absent one and `ApplyDefaults` would then dereference it (a panic found in the review of #124), so it is refused before the defaults. Rust cannot hold a nil reference: there is no Rust peer of this check and no differential case for it; it is a representation of the Go API, as for `CanonicalBytes` (`nil_node`, #122).
* The reasons are of this module, not codes of ADR 0007. The order of the checks is fixed so that the two languages give the same first reason.

**Proof.** `testdata/p5/materialize.json` is written by `tools/p5/materialize_cases.py`, which computes every expected projection and digest itself (Python's JSON writer and `hashlib`, a third reader that has seen neither implementation); 7 accepted and 13 refused cases. Rust and Go compare against it (`rust/tests/materialize_corpus.rs`, `go/materialize_corpus_test.go`, plus hand-built shapes for what a compiled schema cannot give). Two harnesses build the **engine's own** object from the same file and compute its digest: `tools/p5/engine-harness-rust` (`MaterializedObject::try_new`, `materialized_object_digest`) and `tools/p5/engine-harness-go` (`NewMaterializedObject`, `MaterializedObjectDigest`), both against the pinned engine in CI. They also refuse one key more and one key less (`E_INCOMPLETE_OBJECT`) and see a changed value, capability or provenance change the digest. The first run of the Python-computed digests against both engines agreed. 28 planted defects (13 for Rust, 15 for Go).

**What this does not prove yet.**
* *(Closed by section 13.)* The library's output is compared to the engine **through the file**, not field by field on generated documents: the differential with the engine as the third reader, on generated flat documents, is the next step of P5-2.
* *(Closed by section 13.)* The independence of `expected_keys` is one way so far: the case file lists them from the generator's side. The generated differential is where a key lost on both sides would be seen.
* No fuzz target yet on the new input (still open, see section 13).

## 13. P5-2: the generated differential (2026-10-30)

`tools/parity/gen_materialize.py` writes 6 000 cases from a fixed seed (reproducible under any hash seed, as for the canonical bytes). A case is a **structure** (the fields with their kind, presence, nullability and default; which fields the document writes; at most one fault of the document; the capability map with its fault) from which the schema text and the document text are written. The reference is computed from the structure, not from the texts, so it is never a second parser; the projection is written by `tools/parity/gen_canonical.py`, a writer of the canonical form that shares no code with either language. The families reach every answer: accepted (1 787 of 6 000), and `not_flat`, `set`, `omitted_optional`, `invalid_document`, `unsupported`, `capability_missing`, `capability_unknown`, `capability_undeclared`; the families `layers` and `mixed` put faults of different layers in one case, so that the order of the checks is compared. Field names are drawn so that the UTF-16 order differs from the byte order (a character from U+E000 to U+FFFF against one beyond U+FFFF; the byte order of valid UTF-8 is the code point order).

* **Rust and Go** (`rust/tests/parity_materialize_dump.rs`, `go/parity_materialize_dump_test.go`) answer as the reference and as each other, with no list of known differences (`tools/parity/compare_materialize.py`). `tools/parity/selftest_materialize.py` breaks the dumps in 19 ways and each turns the comparison red, among them two languages that **both** lose the same key from the expected keys, and a record id repeated in the inputs or in either dump (the loader refuses it: a later record would replace an earlier one and a dump of 6 000 unique records could hold a 6 001st).
* **The engine is the third reader.** For every accepted case, `tools/p5/engine-harness-rust` and `tools/p5/engine-harness-go` (`differential`) build the engine's own `MaterializedObject` from the projection a language wrote, under **the key set the generator declared** (not the one the language reports), and the engine's digest must be the one the language gave. One key less must be refused by `try_new`. This closes the independence of `expected_keys` that section 12 left open.
* **The Unicode order of the names.** The byte order of valid UTF-8 is the code point order, so the UTF-16 order differs from it only for a character from U+E000 to U+FFFF against one beyond U+FFFF. The first version of the generator claimed this coverage and had no such pair (found in the review of #125); it now draws U+E000, U+FFFD, U+1F600 and U+10000 as names, a test (`test_gen_materialize.py`) asserts that a pair exists and that many accepted cases order differently by byte and by UTF-16, and the shared corpus has the case `flat.names-sort-by-bytes-not-by-utf16`. A Rust that sorts the fields by UTF-16 code unit is seen by the corpus (a planted defect in both languages), by the comparison (140 of 6 000 inputs divergent) and by the engine harness.
* Seen to fail: with the sort of the fields removed from the Rust, the comparison reports 1 127 inputs divergent and the engine harness fails; with the last declared key dropped from both the fields and the expected keys, 1 787 divergent, 3 574 broken promises, and the engine harness fails.
* **What the generated inputs cannot reach:** no document at all, a nil node (Go only) and a value outside the canonical range cannot be written as text; the unit tests cover them. A first run found no difference between the two languages and the reference on any of the 6 000 inputs.

**What this does not prove yet.** Nested, list, set and an omitted optional field stay refusals (sections 11, 12); the differential proves the refusals, not a materialization of them.

## 14. P5-2: the fuzz targets (2026-10-30)

`FuzzMaterialize` (`go/fuzz_test.go`) and the `materialize` target (`rust/fuzz/fuzz_targets/materialize.rs`) hand YAML text, as the document, to `MaterializeFlat` / `materialize_flat` against a fixed flat schema (seven fields: a mandatory string, optional ones with and without a default, two nullable, names that need no escaping and one beyond U+FFFF) and a fixed, complete capability map, so that no refusal of the schema or of the capabilities can occur. **The schema order is not the byte order of the names**, so a materializer that forgets to sort the fields or the expected keys is seen (a first version of the Rust schema had the two orders equal and a removed sort went unseen; found by planting it).

On every input that parses: no panic and the input is left alone; a refusal is one of `invalid_document`, `unsupported`, `omitted_optional`, and `omitted_optional` names a declared optional field with no default that the document does not write; an acceptance has one field per declared name in byte order, the expected keys are the declared names in byte order, a field is `authored` exactly when the document writes it (with that value) and `schema_default` with the declared default when it does not, the projection is one line that holds every key (valid JSON, checked in Go) and the digest is that of its bytes; the same document gives the same answer twice, and written as YAML and read again it gives the same answer. The canary works as in the other targets (`CIC_FUZZ_CANARY`, a committed canary seed in Go, the shared `fuzz/canary/CANARY` in Rust), and `tools/fuzz_matrix.py` lists both.

**What was run.** Go: 40 s of exploration, about a million inputs, no failure; the seeds and the canary are red/green as intended. Rust: `cargo-fuzz` needs a nightly that the local machine does not have, so the body of the target was run, unchanged except for the entry point, over the ten seeds and 3 000 random mutations of each (30 000 inputs): no failure. In both languages, planting a defect (fields not sorted, expected keys not sorted, a default reported as authored, an authored value replaced) turns the properties red. The CI job runs `cargo fuzz` for 60 s per target.

**What a fuzz target here cannot reach:** the schema and capability refusals (fixed away), a nil node (Go only, covered by a unit test) and a nested value of a scalar field (the validation refuses it before the check; the hand-built Opaque tests cover that guard).

**What this does not prove yet.** No fuzz target on `materialize_flat` / `MaterializeFlat`. Nested, list, set and an omitted optional field stay refusals (sections 11, 12); the differential proves the refusals, not a materialization of them.

## 15. P5-3 pre-check: the receipt (2026-10-30, before any code)

Read at the engine commit `93c793a`: `engine/src/receipt.rs` (the verifier), `docs/RECEIPT-SCHEMA.md`, `docs/MATERIALIZATION-SPEC.md` section D, the 36 vectors of `conformance/differential/receipt`; and on this side `corpus/pins.yaml`, `CompiledSchema`, `Grammar`, P5-1 and P5-2. Nothing was run.

**What the engine requires.** A receipt document is `{"materialization": {...}}` with the 15 fields, none optional. The verifier (`verify_receipt(receipt, object, authored_input, binding)`) checks, in this order: shape and form (declared field order), the version (`1`), every digest as `sha256:` and 64 lowercase hex, `applied_defaults` and `derived_values` strictly ascending by `.path` in raw UTF-8 byte order, `derived_values[].inputs` strictly ascending; then `input_digest` = the A7 digest of the canonical form of the authored input it is handed; then `output_digest` = `materialized_object_digest` of the object; then each evidence entry through an **explicit path binding** (receipt path to object key, fail closed with `E_RECEIPT_UNBOUND`): the field must exist, have the provenance the list requires, and its value digest must be the digest of the canonical form of its value; then every defaulted or derived field of the object must be listed. A success establishes **structure and content consistency and nothing more**: not that a default was applied, who produced the receipt, or that any identifier is genuine. `engine_identity` names the implementation that **materialized**, and the spec says it is *supposed* to differ between the Rust and the Go side, so two receipts of the two languages are not byte-equal by design (section G of the engine spec: the value bytes, the receipt without that field, and that field are three different equality questions).

**What this library already gives for the 15 fields.**

| Field | Source here |
|---|---|
| `receipt_schema_version` | the constant `1` |
| `input_digest` | `canonical_digest(canonical_bytes(document))` of the parsed authored document, **before** the defaults (P5-1) |
| `output_digest` | the `output_digest` of P5-2 |
| `applied_defaults` | the fields with provenance `schema_default`: path, the literal `schema-default`, the digest of the value |
| `derived_values` | always `[]` (no derivation exists in this library) |
| `schema_version` | `identity.version` of the compiled schema |
| `schema_digest` | the digest of the schema file bytes that `compile_schema` verifies |
| `schema_name` | `identity.namespace` and `identity.kind` |

**Where they do not meet** (each is a question; none is decided here):

1. **The primitive release identity is not known to the library.** `primitive_release`, `primitive_source_commit` and `primitive_build_hash` name a signed `cic-primitives` release bundle (the tag, its `provenance.source_commit`, its `release.build_hash`). The grammar pin of this repository is a **commit of `devel`** (`73ae469`), not a release, and `corpus/pins.yaml` holds no tag and no build hash. The library cannot state them; whether the pinned commit belongs to any signed release at all is not shown here.
2. **The two grammar digests are in the pin, not in the library.** `grammar_sha256` (of `check_grammar.py`) and `grammar_schema_sha256` (of `instance-grammar.schema.yaml`) are entries of the pin in `corpus/pins.yaml`; the code knows only the pin **name** (`cic-primitives-73ae469-6a188078`), through a table of profiles. A table of the two digests per pin name in each language would duplicate `pins.yaml`; a test can compare them, but it is a second copy.
3. **`schema_digest` is a name that the engine does not pin down.** D1.3 says "canonical schema digest (content hash)". The library verifies and holds the digest of the **exact schema file bytes**. Whether the engine means that, or a digest of a canonical form of the schema, is not stated; a YAML file has no section A form without a reader, so the first is the only one this library can state.
4. **`schema_name` is a convention.** The example is `cic:storage:StorageResource`; the schema has `identity.namespace` (`cic:storage`) and `identity.kind` (`StorageResource`). Joining them with a colon is the obvious reading and is not written anywhere.
5. **`engine_identity` and `engine_version`.** Two implementations need two identity strings (the spec's own example is `cic-primitive-engine-rust` / `-go`), and a version: the Rust crate is `0.0.0` and the Go module has none. Any version must be one fact for both languages, or the receipts of one materialization differ in two fields instead of one.
6. **The path of a field.** The verifier requires a `$.`-rooted path whose dot-separated segments are all non-empty, and reads a path only through the explicit binding. A field name that contains a dot, or that the path writing of ADR 0006 escapes, is not a single segment in that form. P5-2 already refuses a name with no path at all (`unsupported`); the dotted and the escaped names are the next case.
7. **The order is by path, not by key.** `applied_defaults` is ordered by the path text; with a common `$.` prefix this equals the order of the names unless the writing of the path changes the bytes (escapes), so the receipt must sort by path.
8. **What a receipt proves.** A green `verify_receipt` is a consistency check. It is not evidence that the library did what the receipt says (the `agent_done` is not `done` case once more); the proof must still come from the cases where a wrong receipt is refused.

**Proposed first unit of P5-3** (a proposal, not a decision): a receipt for a **flat** document, the unit of P5-2, in Rust and Go.
* `Receipt` with the 15 fields; `receipt_flat(materialization, document, schema identity, release identity, grammar identity) -> receipt`, and the receipt document `{"materialization": ...}` as a node and as canonical bytes (and their digest). It takes the identifiers it cannot know as **inputs of the caller, all required, fail closed** (questions L and M), and refuses a field name whose path is not one plain segment (point 6).
* `input_digest` over the parsed document; `applied_defaults` from the `schema_default` fields, sorted by path; `derived_values` `[]`.
* **Proof:** the engine's verifier (Rust `verify_receipt` and Go `receipt.Verify`, pinned engine) accepts the receipt of every case; the same receipt with one change is refused with the code the engine gives (a changed input, a changed output, an evidence entry moved, repeated or removed, a changed value digest, a wrong version, an unbound path), seen to fail; the shared cases and the engine's own `receipt` vectors; then a Rust/Go differential of the receipts on the generated cases of section 13, where the two sides are equal **except** `engine_identity` (and `engine_version` only if the two must differ); planted defects; fuzz.
* Resolve (P5-4) is not touched.

**Questions for the owner**
* **L.** The release identity (`primitive_release`, `primitive_source_commit`, `primitive_build_hash`) and, if you prefer, the two grammar digests: **inputs of the caller, fail closed when missing** (the proposal, as for the capability), or does the pinned commit have a release that the repository can name?
* **M.** `engine_identity` as `cic-primitive-forms-rust` / `cic-primitive-forms-go`, and one `engine_version` for both languages from a single file that a test compares with the crate and the module, so that the two receipts differ in the identity field only?
* **N.** `schema_digest` as the digest of the exact schema file bytes (what the library verifies), and `schema_name` as `namespace:kind`: both as **stated claims**, to be confirmed by the engine side, or ask the engine first?
* **O.** A field name with a dot or an escaped path: a controlled refusal in the first unit (the proposal), or define the path writing the engine expects before any code?

### 15.1 Owner's decisions after the review of this note (L-O)

The receipt joins statements whose authentication has different sources. The verifier can check that they are **consistent**; it cannot check that they are **genuine**. A well-formed identity and an identity of verified origin are not the same thing, and a receipt must not let cryptographic consistency look like authenticity.

| Question | Decision |
|---|---|
| **L** release identity | **Yes.** A required, **verified** input of the caller; a missing or unverified release identity fails closed. |
| **M** engine identity and version | **Partly.** Separate identities for the two implementations (`cic-primitive-forms-rust`, `cic-primitive-forms-go`); **no** mandatory shared version. |
| **N** `schema_digest`, `schema_name` | **Ask the engine first.** No normative rule is made from an assumption; no normative receipt is produced before it is answered. |
| **O** dotted or escaped names | **Yes.** A controlled refusal in the first unit. |

* **L.** The release identifier and the build hash must match an **authenticated release-metadata record** whose link to the source and to the grammar is checked. The grammar pin is a commit of `devel` and `pins.yaml` holds the hashes of the grammar files, which is not evidence of a signed release bundle, so a receipt must never be produced with an invented release tag. If no such record exists, the **authentic** receipt production stops. A test fixture may use clearly **marked, unauthenticated** data. What this means in code, as far as it can be said before any: the receipt layout has no field that could carry such a mark, so the two cases are two different functions (a production one that takes only a verified record, and a fixture one that is not exported as the production path), not a flag in the receipt. The library does not verify a signature (ADR 0010: attestation stays the caller's); it takes a record (release, source commit, build hash, the two grammar digests) and **checks the part it can**: that the two grammar digests of the record are those of the pin the schema was compiled under, and that the record's source commit is the pin's commit. This needs the pin's commit and the two digests in the code, as a table compared with `corpus/pins.yaml` by a test in both languages (the second copy of point 2, accepted for that reason). *Consequence stated as a fact about data, not about code:* the pin is `73ae469` on `devel`; a release cut from that commit is not shown to exist, so until one does (or the pin moves to a release commit) **no authentic receipt can be produced**, and the unit is proved with fixtures only.
* **M.** The names are accepted. The engine spec D2 lets Rust and Go be versioned independently, so a single shared version file is **not** a contract rule: the receipt carries the actual version of the implementation that materialized. The two languages may share a version in the first release, but nothing may depend on it. The differential compares the shared semantic identifiers (everything except the two engine fields) and treats the implementation identity and version separately.
* **N.** Two questions go to the engine side (filed as [cic-primitive-engine#63](https://github.com/CentralInfraCore/cic-primitive-engine/issues/63), 2026-10-30, and nothing else is asked there), and until they are answered the library may offer the first value only as a clearly named proposal, never as a claim that it satisfies the engine contract: (1) is `schema_digest` the SHA-256 of the whole, original schema file, or of a normalized or canonicalized schema representation? (2) is `schema_name` exactly `identity.namespace + ":" + identity.kind`? The engine spec (D1.3) calls the first a "canonical schema digest" and defines no byte-forming algorithm. **This blocks the implementation of P5-3.**
* **O.** A field name is accepted only if it is one unambiguous segment. The `PathBinding` is explicit and comes from the schema, `$.field` to `field`. Everything else is a controlled refusal, with no automatic escape and no assumed path resolution. (The verifier would accept `$.a.b`; it cannot tell two segments from one field named `a.b`.)

**Next step.** It is not the serialization of the 15 fields; it is closing the identity and digest contracts: questions N(1) and N(2) to the engine side, and the shape of the verified release record (L). P5-3 implementation starts after both.

### 15.2 Two gates, kept apart (2026-10-30)

* **Contract conformance.** Does a receipt of this library mean what the engine's contract says (the bytes of `schema_digest`, the formation of `schema_name`)? This is what the engine issue #63 closes.
* **Authenticity of origin.** Is the release identity (`primitive_release`, `primitive_source_commit`, `primitive_build_hash`) and the grammar identity that of a verified release? This is not the engine's to answer, and the engine verifier does not check it: it checks form and consistency.

Closing the first does **not** authorize the second. After #63 is answered the implementation can be written and proved with fixtures; an **authentic** production receipt additionally needs the verified release record of L, and until a release cut from the pinned commit exists there is none. A consistent receipt must not look like an authenticated one.

### 15.3 The engine pin moved for the schema identity contract (2026-10-31)

`cic-primitive-engine#63` is answered by owner decision, and the contract is merged in the engine (PR #64, merge commit `a6a0680`): `schema_digest` is the SHA-256 of the raw bytes of the pinned schema source, `schema_name` is `identity.namespace + ":" + identity.kind`, with golden and negative vectors read by both languages (`docs/RECEIPT-SCHEMA.md`, "Schema identity contract"). `testdata/p5/engine-pin.txt` now names `a6a0680` (it was `93c793a`).

What the move changes, read from the diff `93c793a..a6a0680`: nothing under `engine/` or `go/` except the new module `schema_identity` (Rust) and package `schemaidentity` (Go), the two new rejection codes and the new test files; the other commits in that range are conformance fixtures (#62). The receipt verifier and `MaterializedObject` are byte-identical to what P5-2 was proved against.

What was run against the new pin, with the same commands as CI: the engine's canonicalize vectors against the table (27 vectors, 0 failures), both engine harnesses on the corpus (7 accepted cases each), and the 6000-case generated differential with the engine as third reader (1817 accepted cases agree in Rust and Go).

What this move does **not** do, and what comes next as a separate change:

- It adopts no code from the new module. The Forms receipt producer does not exist yet.
- The review of #64 makes one invariant mandatory for P5-3: the identity components (`namespace`, `kind`) and the digest must be bound to the **same verified source file**. The engine function checks that the claimed digest is the digest of the bytes it is given; it does not check that the components were read from those bytes. The Forms side (`SchemaInput` holds the bytes and the pinned digest, `CompiledSchema` the kind and namespace read from them) is the place to read both from one compile and to prove it, with a case where the components come from another file.
- The contract tests of Forms against the new pin (the golden vectors through Forms' own producers) belong to that change.
- The origin gate (a verified release record) stays closed; no production receipt follows from this.

## 16. P5-3, first step: the schema identity, Forms side (2026-10-31)

The engine side of D1.3 is merged (`cic-primitive-engine` #64) and the pin moved (#129, section 15.3). This step is the Forms side of that contract, and the binding that the review of #64 made mandatory for the receipt. It writes no receipt yet.

**What is built** (`rust/src/schema_identity.rs`, `go/schemaidentity.go`, the same names, rules and codes as the engine's module):

* `schema_digest(bytes)`: `sha256:` and the hex SHA-256 of the raw schema bytes. `schema_name(namespace, kind)`: `namespace:kind` as authored, with the engine's colon rules. `bind_schema_identity(source, claimed, namespace, kind)`: the engine's function on loose values, with the engine's check order.
* `schema_identity(input)` / `SchemaIdentityOf(input)`: the **same-source binding**. It takes the `SchemaInput` (the bytes and their pinned digest), compiles it (which verifies the bytes against the pin and reads `identity.*` **from those bytes**), and builds the name, the version and the digest from that one compile. There is no parameter through which a component of another file could enter. A schema whose own identity breaks the contract is refused with the engine's rule although its bytes are the pinned ones.

**How it is proved.** The engine's 23 vectors run through `bind_schema_identity` in both languages with the engine's code, rule and path (`rust/tests/schema_identity_contract.rs`, `go/schemaidentity_contract_test.go`); the two golden files are required to be the 314 and the 313 bytes with the digests written in the tests a second time; every one of the 314 bytes is flipped and the mismatch rule required. For the binding: the bytes of one schema under the digest of another are refused both ways with `SCHEMA_DIGEST_MISMATCH`, and each file under its own digest names its own kind. The vectors are a **snapshot** in `testdata/p5/schema_identity` (every job that runs the tests has them), and the `rust` job compares it byte for byte with the engine's own directory at the pinned commit (`diff -r`), so it cannot drift. 20 seeded defects (10 per language: CRLF-normalized hashing, a dropped final newline, an accepted mismatch, a missing claim taken as a match, a colon in the kind, an empty namespace component, a trimmed namespace, a case-folded kind, a lost version, swapped components) must each turn the suites red.

**What this does not do.** It builds no receipt; `receipt_flat` and the engine verifier as a gate are the next step. It does not establish where the source came from (the origin gate of section 15.2 stays closed). The binding is by construction (one compile), and it covers the three schema fields; that the `Materialized` object of P5-2 was made from the same schema is a separate check, planned for the receipt builder (its key set against the schema's declared names).

## 17. P5-3, second step: the flat receipt, fixture only (2026-10-31)

The Forms side of the schema identity is merged (#130, section 16), and the engine's `verify_receipt` and `receipt.Verify` are at the pin `a6a0680`. This step builds the receipt of a flat document and puts the engine's verifier in front of it as the gate. It builds nothing for the origin gate of section 15.2: **no receipt of this step is authentic**, and none can be until a verified release record exists.

**What is built** (`rust/src/receipt.rs`, `go/receipt.go`, the same names and the same order of checks):

* `receipt_flat_fixture` / `ReceiptFlatFixture(schema input, document, access keys, capabilities, release fixture, engine identity)` returns a `FlatReceipt`: the canonical bytes of `{"materialization": {15 fields}}`, their digest, the explicit path binding, the canonical bytes of the authored input, and the `Materialized` object the receipt describes.
* **One compile.** The function takes the `SchemaInput`, not a compiled schema and not an identity. The name, the version and the digest, and the shape the document is materialized against, come from that one compile. The key set of the object is therefore the key set of the schema that the identity names, and no parameter lets another file's component or key set enter (the invariant of the review of the engine's #64, now carried through the materialization).
* `input_digest` is the digest of the canonical bytes of the parsed authored document, before the defaults. `output_digest` is the one of P5-2. `applied_defaults` lists the `schema_default` fields: path `$.<field>`, rule `schema-default`, the digest of the canonical bytes of the value; the order is the order of the fields, which is the order of the paths (one common prefix, no escape). `derived_values` is `[]`.
* The refusals (a `ReceiptError` / `*ReceiptError`, never a guess): the errors of the schema identity and of the materialization unchanged, and, new here, an **empty or missing `identity.version`** (the engine's verifier refuses an empty `schema_version`, so the builder must not make a receipt it knows will be refused), a release or engine field that is empty, a release digest that is not `sha256:` and 64 lowercase hex, and a **field name that is not exactly one plain path segment** (decision O: a dot, a backslash, a bracket, a brace, `=` or `,`). Order of the checks: the schema identity, the version, the release fields in struct order, the engine fields, the presence of the document, the materialization, the field names.
* **Fixture only, by the name of the function** (decision L). The three release fields and the two grammar digests are inputs of a `ReleaseFixture`, checked for form and for nothing else; the grammar pin is the `cic-primitives` commit `73ae469` of `devel` (`corpus/pins.yaml`), for which no verified release exists (this is NOT the engine pin `a6a0680` of `testdata/p5/engine-pin.txt`: the engine commit is where the verifier comes from and says nothing about whether a release exists), so there is no production function and none is exported under a neutral name. The layout has no field to mark a fixture, so the separation is in the function, not in the document. The shared cases use data that says what it is: the release `fixture/@v0.0.0`, the commit `0000...`, and digests that are the SHA-256 of the words "fixture build hash", "fixture grammar" and "fixture grammar schema".

**How it is proved.**

* `testdata/p5/receipt.json` holds one case for each of the 7 accepted cases of `materialize.json`. The expected canonical bytes of the receipt are computed by `tools/p5/receipt_cases.py` in Python (its own JSON writer, `re` for the identity, `hashlib`), a reader of the form that has not seen the Rust or the Go code. Both libraries must produce those bytes exactly, with their own `engine_identity` in the one place where the two may differ (`cic-primitive-forms-rust` / `cic-primitive-forms-go`), and the digest and the path binding.
* **The engine is the gate.** The harnesses of P5-2 (`tools/p5/engine-harness-rust`, `tools/p5/engine-harness-go`) now also run the engine's own verifier (`verify_receipt`, `receipt.Verify`, at the pinned commit) over each of those receipts, against the object built from the shared projection and the authored input, with the binding. All 7 are accepted by both. Then each is altered and must be refused with **the code and the rule of the engine**, written in the harness from the engine's contract and not read back from the library: a version the verifier does not support, a changed input digest, another authored input, a changed output digest, another object, an empty `schema_version`, a `schema_digest` of a wrong form, a missing field, and, for the cases that default a field, a changed value digest, a rule that is not the literal, an evidence entry removed, repeated or swapped, a path with no binding, and an object whose provenance was changed. 82 alterations per language over the 7 cases.
* 26 seeded defects (13 per language) turn the suites red: the layout version, the output digest, the input digest taken after the defaults, every field listed as defaulted, a value digest over a part of the value, a binding of a path to itself, a rule that is not the literal, an empty schema version accepted, a wrong digest form accepted, an empty release field accepted, a dotted field name given a receipt, the schema version written as the name, the engine version written as the identity.

**What this does not do, and what is next.**

* A green verifier is a consistency result (section 15): it does not show that the default was applied, who made the receipt, or that any identifier is genuine. The origin gate (a verified release record) stays closed; `cic-primitive-engine#63` stays open until the end-to-end proof is written.
* The receipt is proved on the 7 shared cases. The generated differential of section 13 does not yet cover the receipt (Rust and Go equal except `engine_identity`, with the engine as a third reader on the 1817 accepted cases), and there is no fuzz target for it. Those are the next units, as P5-2 was split.
* Not decided here: the shape of the verified release record and the production function (question L); `Materialized` for a document with a nested position (decision H and the flat restriction stay).

## 18. P5-3, third step: the generated receipt differential (2026-10-31)

Section 17 proved the receipt on 7 shared cases. This step asks whether the contract holds over the whole range of the generated inputs of P5-2, with no second generator: the 6 000 seeded cases of `tools/parity/gen_materialize.py` (`--count 6000`, fixed seed) are the inputs.

**What is compared** (`tools/parity/compare_receipt.py`, run in the same CI step family as the materialization differential):

* `rust/tests/parity_receipt_dump.rs` and `go/parity_receipt_dump_test.go` dump `receipt_flat_fixture` / `ReceiptFlatFixture` for every case: `["ok", document, authored_input, binding, expected_keys, projection]` or `["err", reason, field]`. The fixture release and the engine version come from `testdata/p5/receipt.json`; the engine identity of each language is its own (`cic-primitive-forms-rust`, `cic-primitive-forms-go`).
* **A reference that shares no code with either language.** For an accepted case it is `receipt_for` of `tools/p5/receipt_cases.py` (the same Python that wrote the 7 shared cases, now a function): the receipt computed from the **schema text** (name, version, digest) and from the **projection and output digest of the generator's own reference** (`want`). For a refused case the reference is the refusal of the materialization: a receipt is refused where the materialization is, with the same reason and field.
* **Rust and Go may differ in nothing but `engine_identity` and `engine_version`** (decision M). The comparison replaces only those two fragments of the document text, after checking that the identity is the one its language is declared to write and that the version is a text of its own (not empty, not an engine identity); every other byte of the document, the authored input, the binding, the expected keys and the projection is compared as it is. There is no list of known differences.
* The inputs must reach every refusal of the materialization and a receipt with no applied default, with one and with several (ordering evidence).

**Result at the commit of this step:** 6 000 inputs; 1 817 receipts (1 115 with no applied default, 522 with one, 180 with several) and 4 183 refusals (`not_flat` 914, `omitted_optional` 759, `invalid_document` 729, `capability_undeclared` 556, `set` 437, `capability_missing` 268, `unsupported` 261, `capability_unknown` 259). Rust, Go and the Python reference agree on every one.

**The engine is the third reader again** (`receipt-differential` in `tools/p5/engine-harness-rust` and `-go`): the engine builds its own object from the projection each language wrote, under the key set the **generator** declared, and its own verifier (`verify_receipt`, `receipt.Verify`, at the pin `a6a0680`) must accept every one of the 1 817 receipts of each language with that language's authored input and binding. The 4 combinations (receipts of Rust or of Go, engine in Rust or in Go) give the same numbers: 1 817 accepted, 7 557 altered receipts refused with the exact engine code and rule (input digest, output digest, empty schema version, value digest, evidence entry removed, a path with no binding). The Go harness reads the projection with `UseNumber`: an integer of 64 bits must not pass through a float (a first run of the harness without it refused case 7, which was the harness, not the engine).

**The comparison is shown to fail** (`tools/parity/selftest_receipt.py`): the dumps are broken in 59 ways, each in one language (only the comparison between the two sees it) and in both (only the reference sees it): the layout version, the output digest taken as the schema digest, the input digest, the schema digest, name and version, an applied default dropped / repeated / with a changed value digest or rule, the evidence order, a listed derived value, release fields in each other's place, the authored input, the binding (path to itself, path missing), the projection, the expected keys, the two engine fields (the other language's identity, an empty identity, an empty version, the identity as the version), an acceptance turned into a refusal and back, a refusal named or its field changed, a reason outside the set, an answer missing, extra or in no known form, a repeated id, and a generator that stops reaching a receipt with defaults. A different engine version must NOT turn it red, and does not. The first run of the self-test found a real gap of the comparison itself: the engine version was only required to be a non-empty text, so the identity text was accepted as a version; it is now refused.

**What this does not prove, stated plainly:**

* The engine's verifier cannot see the schema identity (`schema_name`, `schema_version`, `schema_digest`): it has nothing to compare them with. They are guarded by the Python reference and by the self-test, not by the engine.
* The generated schemas have only plain field names and one fixed fixture release, so `path_not_one_segment` and the form of the release fields are proved by the unit tests of section 17, not by the generated cases.
* A green engine verifier is a consistency result (section 15). The origin gate stays closed: the grammar pin is the `cic-primitives` commit `73ae469` of `devel` with no verified release (this is the pin of `corpus/pins.yaml`, not the engine pin `a6a0680`), so there is no production receipt. `cic-primitive-engine#63` stays open until the end-to-end proof is closed.

### The six survivors of the shadow mutation run of #131

The shadow run (`cargo-gamma`, not a gate) on the new Rust code of #131 killed 99 of 105 mutants (94.3 %). Each survivor was examined, not assumed equivalent. After this step the same tool (`cargo-gamma@0.3.0`, the mutators of the CI) on the whole of `receipt.rs` gives 77 mutants, 74 killed, 3 survived (96.1 %): the three are the two reversed `vec![...]` and the unwrap of the body, all classified below as equivalent or unreachable (the fourth, the default value, is listed as killed for the spurious reason below).

| Mutant | Verdict | Evidence |
|---|---|---|
| `receipt.rs` digest check: `all` taken for `any` | **Missing negative test**, closed here | A digest with one good character passed. `a_digest_has_only_lowercase_hex_characters_at_every_position` puts a wrong character at the first, a middle and the last position; a seeded defect for each language |
| `canonical_bytes(doc)?` of the authored input taken for `unwrap()` | **Reachable, missing negative test**, closed here | A node built by a caller with a repeated key passes the materialization and reaches the canonical error: the receipt answers `Canonical(duplicate_key)`. Test in both languages and a seeded defect for each |
| `canonical_bytes(&f.value)?` of a default value taken for `unwrap()` | **Unreachable through the public function**, left unkilled | The defaults come from a compiled schema (text): the compiler refuses a float and an integer beyond 64 bits, the parser a repeated key, and the flat fields are scalars. The text half of the test above is the evidence. The local `cargo-gamma` run lists this mutant as killed by `a_late_duplicate_is_still_found_and_many_keys_do_not_cost_quadratic_time` (`behaviour.rs`), a test that never calls the receipt: a timing test that took 10 s under the load of the parallel run, so a spurious kill, not evidence |
| `canonical_bytes(&map(... body))?` taken for `unwrap()` | **Unreachable** | The body is made of texts and the integer 1 under fixed keys |
| `vec![("path", ...), ("rule", ...), ("value_digest", ...)]` reversed | **Equivalent** | The canonical form orders the keys (A7); the order the entries are given in is not in the bytes (`the_order_of_the_entries_given_does_not_change_the_bytes_but_the_order_of_a_sequence_does`, `rust/tests/canonical_corpus.rs`) |
| the 15 pairs of the body `vec![...]` reversed | **Equivalent** | The same |

The two unreachable ones are a decision to keep `?` (a library returns an error, it does not panic) and not to shrink the code to fit the metric.

**Next:** the fuzz targets of the receipt (a separate, smaller PR, as in P5-2), then the machine-checked B/C contracts and the end-to-end closing of P5-3.

## 19. P5-3, fourth step: the receipt fuzz targets (2026-10-31)

Sections 17 and 18 proved the receipt on the shared cases and on the 6 000 generated documents. This step puts the receipt's own API under fuzzing: `FuzzReceipt` (`go/receipt_fuzz_test.go`) and the `receipt` target (`rust/fuzz/fuzz_targets/receipt.rs`). Fixture only; no production receipt, no new origin mechanism; engine `#63` stays open.

**One source, no second contract.** `tools/p5/receipt_fuzz_seeds.py` derives everything from files the receipt already has (check mode in CI): the schema text of the first accepted case of `testdata/p5/materialize.json`, the fixture release and engine version of `testdata/p5/receipt.json`, the grammar and the access keys of `testdata/p1/defaults.json`, the capability map of that case. It writes `testdata/p5/receipt-fuzz/{schema.yaml,fixture.txt,seeds.json}` and the same seeds as the Rust corpus `rust/fuzz/corpus/receipt/`. The property is written once per language (`rust/fuzz/receipt_check.rs`, shared by the fuzz target and the replay test through `#[path]`; `receiptFuzzCheck` in Go).

**The input** is FLAGS, KIND and the document (YAML text). The flags make one fault each: the schema binding (the claimed digest changed in its last character, the bytes edited after the claim, an empty claim), the schema without a `version`, a bad build hash (a wrong length, a good digest whose last character is not hex, one whose first is not), an empty engine version, the capability map (a capability removed, misspelled, an undeclared one added), a document a caller built that repeats its first key (the parser never produces that), and a field named `la.bel`, which is not one plain path segment. The document is fuzzed freely: its values, its keys, its structure.

**The property** (on every input that is UTF-8 and parses; what does not is the parser's, and is counted as `Left`): nothing panics, the input is left alone, the same input gives the same answer twice. **The error boundary is the order of the checks and the layers do not mix**: a broken binding is an `Identity` error whatever the rest is; then the empty version, the release fields and the engine fields (a refusal of the receipt, naming the field); only then the document: a refusal of the materialization has a reason that the document (and, with the capability fault, the capability map) can cause, a name that is not one segment is `path_not_one_segment` (only with that fault, only for the field it was made on), and a repeated key is a `Canonical(duplicate_key)` error, never a panic. A receipt is made only when no fault was made, and then it is exactly the contract: one line `{"materialization":{...}}` whose digest is that of its bytes; the values of `schema_name` (`namespace:kind`), `schema_version`, `engine_identity`, `engine_version` and the five release fields; the schema digest of the bytes; the input digest of the canonical authored input; the output digest of the materialization; the applied defaults in the order of the paths with the value digests of the declared defaults (none, one or several); a binding of every path to its field; the fields in byte order with `authored` exactly where the document writes the field; and the document written as YAML and read again gives the same receipt.

**The seeds** (31): receipts with four, none, one and two applied defaults and an escaping one; documents the materialization refuses (unknown field, mandatory missing, wrong scalar type, nested value, empty mapping); the three binding faults, also in front of a bad document; the version, the hash (three ways), the engine; each of those in front of the next (the order of the checks: binding over version over hash over engine over document); the three capability faults; the dotted name, also in front of a refused document; a repeated key in an accepted document, in a refused one, and with nothing to repeat. The canary is `CANARY` (`fuzz/canary/CANARY`, shared; `go/testdata/fuzz/FuzzReceipt/canary-seed`) with `CIC_FUZZ_CANARY` as in the other targets, and `tools/fuzz_matrix.py` lists both targets.

**The replay, in every build.** `rust/tests/receipt_fuzz_seeds.rs` and `TestReceiptFuzzSeeds` run the seeds and a **deterministic mutation round** (5 000 inputs, xorshift64* from a fixed state, the same algorithm in both languages: a byte changed, a token inserted, a byte deleted, the flags or the kind set) through the property, require every end to be reached, and with `CIC_FUZZ_RECEIPT_DUMP_DIR` set write the receipts they made. The CI then (1) runs `tools/parity/compare_receipt_fuzz.py`: the two rounds must have drawn the same inputs, ended the same on every one (but the quarantined parser case below), made the same receipt bytes (the engine identity apart), and have exactly the records their own traces call for, and (2) runs the pinned engine's own verifier on every receipt each language made (the `receipt-differential` mode of the harnesses): all accepted, the altered ones refused with the exact engine code and rule.

**A finding of the comparison, quarantined to the proven case and not normalized away.** On the first run of the mutation round the two languages drew the same inputs and ended differently on a few of them: the Rust parser ends `Left` where the Go parser reads the document. The class is a quoted scalar continued on a line that starts at column 0 (`a: "x`, newline, `y"`): Go reads `x y`, Rust refuses with `invalid indentation in quoted scalar`. It is a difference of the parsers, outside the receipt, and it was in the code before this step; nothing here decides which is right. The comparison lets through **one direction of this one difference and nothing else**: the trace of each round records why an input was left to the parser, and an input may end as `Left` in Rust and as anything else in Go only if the Rust reason is exactly `syntax: invalid indentation in quoted scalar` (taken from the Rust trace, not guessed) **and** the input has a line break inside a quote followed by a character that is not a space. The other direction (Go refuses, Rust reads), the same direction for another Rust reason, and an input without that line break are red. It is counted and printed on every run (5 of the 5 000 inputs, all with that Rust reason; none in the other direction). The parser differential is the place that has to close it.

**The records are bound to the traces, in each language.** Comparing the two dumps with each other cannot see a record that is missing from both. The comparison therefore calls for the records itself: every seed has its record, and every mutation that a language's OWN trace says ended as a receipt has its record (up to the cap of the dump, 1 500, the same constant in both replays; there are 74 and 79 of them here), the record is a receipt, and there is no record that the trace does not call for. A record missing from both languages, or only from one, or an extra one, is red (`tools/parity/test_compare_receipt_fuzz.py`, which was itself shown to fail by planting a comparison that does not call for the mutation records, one that does not call for the seeds, and one with the wide quarantine; the first version of the test for the seeds passed on the broken comparison by counting problems instead of reading them, and was rewritten).

**The fuzz can fail, and the property can find a fault.**

* *The canary.* Go: `FuzzReceipt/canary-seed` is green without the switch and red with `CIC_FUZZ_CANARY=1`. Rust: `cargo fuzz run receipt fuzz/canary/CANARY` exits 0 without the switch and 1 with it (`canary: the fuzz job reports a failure`).
* *Fault injection into the receipt builder, against the replay test only.* The seeded defects of the receipt (30: the 26 of section 17 and the 4 of section 18, two per language: the digest check and the canonical error of the authored input) were run with the test command replaced by the replay test alone (`cargo test --test receipt_fuzz_seeds`, `go test -run 'TestReceiptFuzzSeeds|FuzzReceipt'`): **30 of 30 are caught by the fuzz property itself.** The first run caught 22: the property checked that `schema_name`, `schema_version` and `engine_version` appear once as keys and not their values, and the hash fault was only a wrong length, so the digest check taken to `any` or started at the second character, and the identity texts written in each other's place, went unseen; and the fixed schema had only plain names, so the dotted-name defect was out of reach. The property now checks the values, the hash fault has three shapes, and the dotted name is a flag. The rewritten property was run again: 30 of 30.

**What was run.** Go: 45 s of exploration, 362 061 executions, no failure. Rust: `cargo-fuzz` 0.13.2 on the stable toolchain with `RUSTC_BOOTSTRAP=1` (the pinned nightly is the CI's), 60 s, 179 979 executions, no failure, the corpus the fuzzer grew was not committed. The CI job runs `cargo fuzz` for 60 s per target as for the others.

**What this does not prove.** The schema is the one flat schema of the first case; other shapes are the generated differential's (section 18) and the refusals of section 12. The release fields are one fixture with one fault at a time; the engine verifier cannot see the schema identity (section 18). The product code did not change in this step, so there is no new product mutant; the fuzz code is test code. The parser class above is open.


## 20. P5-3, fifth step: B and C measured against the pinned engine verifier (2026-11-01)

The owner's record of 2026-10-30 asks, before P5-3 is closed, a machine-checked contract for **B** (who defines the `PathBinding`, how a tree position becomes a key) and **C** (the path form of a list element), because "the verifier accepts a path form" is not a proof that the path is bound to the right key. This step is the **measurement** half: what the pinned verifier (`a6a0680`, Rust `verify_receipt` and Go `receipt.Verify`) says, as a table the two engine harnesses check in CI. It designs nothing and changes no product code.

**The table:** `testdata/p5/receipt-paths.json`, 16 cases. Each takes the receipt of a case of `receipt.json`, replaces the paths of its `applied_defaults`, and verifies it with a given binding. Both harnesses (`tools/p5/engine-harness-rust`, `tools/p5/engine-harness-go`, run by the existing `receipts` step) read it and assert the verdict; a verdict that differs fails the build. Seen to fail: a changed expectation turned red in Rust and in Go, for an accepted and for a refused case. The table itself is guarded: each harness carries the 16 case ids, each once, and stops on a table that has lost one, repeated one in place of another, or grew one it does not know (the count of the table is not a check of the table); the self-tests (`cargo test` and `go test` in the harness directories, run in CI) show each of the four refusals, and a real table with no cases, one dropped and one repeated was refused by both harnesses.

**What it shows (all 16 as written, both languages, first run):**

| Finding | Cases | Consequence |
|---|---|---|
| The form check refuses only an empty segment or a missing `$.` root (`$.a..b`, `$.a.`, `$.`, `enabled`, `$enabled`: `E_INVALID_RECEIPT`, `R-RECEIPT-EVIDENCE-FORM`) | 5 | the writing is nearly free |
| The selector form of ADR 0006, `$.routes[{to="b"}].metric`, **passes** the form check and is accepted when bound; without a binding entry it is `E_RECEIPT_UNBOUND` | 2 | **C: the verifier does not refuse a list-element path, and does not interpret it** |
| A dot inside a selector value (`{to="a.b"}`) splits the path into two non-empty segments and passes | 1 | the form check cannot tell a selector from a nested path; a key value with a dot is not safe in this writing |
| The path is opaque text: `$.label` bound to the key `enabled`, and a path with a trailing space, are accepted | 2 | **B: the verifier does not relate a path to a key; only the binding does** |
| A binding to a key that is not in the object, or to a key of another provenance, is `E_RECEIPT_MISMATCH`, `R-RECEIPT-EVIDENCE-MEMBERSHIP` | 2 | the binding is checked for existence and provenance |
| Two defaulted fields bound crosswise (different values) are `R-RECEIPT-EVIDENCE-VALUE` | 1 (+1 control) | a wrong binding is seen **only through the value digest** |

**The limit that follows (read from `check_entry`; executed in section 21):** the verifier compares the digest of the value only. Two defaulted fields with **equal** values can have their bindings swapped and the receipt is still accepted. So the verifier cannot prove that a path is bound to the right key; that proof stays with the library's own derivation, which is the half that is not done.

**Scope decisions (accepted by the review of this PR; the measured table above is the engine's behaviour, the lines below are what Forms commits to):**

* **B, the library side.** Today the binding is `$.<field>` to `<field>`, one plain segment, derived from the schema and compared with the Python reference in every generated case (section 18). Whether a nested position or a list element needs a binding at all depends on materializing a non-flat document, which P5-2 does not do (decision H, and flat documents first). Proposal: P5-3 closes **flat only**, with the writing of a key that is not one plain segment refused (already so, `path_not_one_segment`), and a non-flat binding is a unit of its own after the engine's contract defines it.
* **C, the list element.** The verifier accepts the selector form and does not interpret it; ADR 0006's escaping of a dot inside a selector value is not seen by it. Proposal: this library writes **no** list-element path in a receipt until the engine's contract says how a selector becomes a key; the measured table is the pinned behaviour that a later contract has to confirm or change.
* **The end-to-end proof** (a document from text through parse, resolve, materialize and receipt to the engine verifier, in one run) is the next step; production receipt and Engine #63 stay on hold.

**Recorded:** B closes for the flat `$.field -> field` binding only, derived from the same schema/materialization source and checked against an independent reference; a swapped binding of equal-valued defaults is not correct either, although the verifier would accept it. C: no list-element selector is written as a receipt evidence path until the engine's normative contract defines selector and key; unsupported input is refused explicitly. The non-flat binding is a contract unit of its own. The end-to-end proof is a separate PR and has to use values from one connected execution, not separately built fixtures.


## 21. P5-3, sixth step: the end-to-end proof, without reference resolution (2026-11-01)

**P5-3 end-to-end proof without reference resolution.** The owner withdrew the requirement of a reference resolve (the instruction of 2026-11-01): there is no snapshot or reference contract in this library, and none is invented here. The chain is

`schema source bytes -> verified compile and identity` and `authored document -> parse -> materialize -> receipt_flat_fixture -> the pinned engine verifier`

with the capability map an **explicit, checked caller input** (the P5-2 contract). This step does not say where that map comes from, and does not call its source authenticated. `ResolveRule` is not connected to the materialization. Decision F and P5-4 stay open; the release record is the fixture of section 17, the production receipt is on hold, and engine issue #63 stays open.

**What is built (tests and tools only, no product code):**

* `tools/p5/end_to_end_cases.py` writes `testdata/p5/end-to-end.json`: four cases (no default, one default, four defaults, and `twin-defaults`: two defaulted fields of one value), each run twice, with its capability map (`base`) and with ONE capability changed (`alt`). The expected values of both runs are computed in Python from the fields each case lists: the canonical bytes of the projection and its digest, the authored input and its digest, the default evidence, the `$.field -> field` binding and the whole receipt.
* `rust/tests/receipt_end_to_end.rs` and `go/receipt_end_to_end_test.go`: ONE chain per run. The schema is compiled and its identity bound (`schema_identity`, `compile_schema`), the document is parsed once, and that very node is what the standalone `materialize_flat` and `receipt_flat_fixture` are given. Each step's output is checked against what the next step carries: the receipt names the identity of the first step, the input digest of the parsed document, and the output digest of the standalone materialization; its evidence and binding cover exactly the defaulted fields.
* `tools/parity/check_receipt_e2e.py` (+ 23 self-tests) reads the dumps of both languages and the Python reference. Every record carries `chain`, a digest over the schema digest, the input digest, the output digest, the receipt digest, the digest of the canonical form of the path binding (the engine takes it as an input apart from the receipt) and the capability map of THAT run; an artefact of another run, another binding or another capability map breaks it. A self-test changes the binding and nothing else and reads the chain-digest message; with the reference's binding check switched off the chain digest still catches the swap, and a defect planted in the digest (the binding left out) turns exactly that test red.
* `end-to-end` mode of both engine harnesses: the engine verifier reads the records of both languages (four combinations: Rust or Go producer, Rust or Go verifier).

**Two materializations, stated.** The receipt builder materializes inside, so a chain holds TWO executions of the materialization: the standalone step and the builder's. They are compared field by field (key, capability, provenance, canonical value bytes, projection, expected keys, output digest) in the tests and, from the dump, by the checker. One execution is never claimed where two happened.

**Three schema compiles, stated.** The schema is likewise compiled more than once in a chain: `schema_identity` compiles inside, the chain then calls `compile_schema` for the root it hands to the standalone materialization, and `receipt_flat_fixture` compiles again for the receipt. The three get the same source bytes and the same pinned digest, and the identity and digest they produce are compared (the receipt carries the identity of the first step), but **one compiled schema instance shared by the steps is not claimed or proved**. Sharing one instance would be a change of the product API, which this step does not make.

**What the proof shows (run locally, Rust and Go):**

| Requirement | How it is shown |
|---|---|
| One chain, same compile and input for every step | the chain function above; the checker rejects a record whose chain digest is not the digest of its own artefacts |
| A capability change moves the object, the output digest and the receipt | tests in both languages and the checker: object, `output_digest` and receipt differ, and the receipt differs in `output_digest` alone |
| ... and not the input digest | the authored input, the input digest, the schema identity and the binding are equal across the two runs |
| The old receipt is not valid for the changed object | the engine refuses the base receipt against the alt object, and the alt receipt against the base object, with `R-RECEIPT-BINDS-OUTPUT` (all four combinations) |
| A missing, unknown (`Implemented`, `unverified`) or undeclared capability | refused with the reason and the field in the standalone step **and** by the receipt builder alone, which makes no receipt |
| Binding correctness | the binding is compared with the Python reference, field by field, for every case |
| Swapped binding of two defaults of EQUAL value | **the engine accepts it** (executed, both verifiers): 1 swap of equal values accepted; and refuses 6 swaps of different values with `R-RECEIPT-EVIDENCE-VALUE`. The Python reference rejects the swapped binding (a self-test builds it and reads the message) |
| Altered schema source, document, output, digests, evidence, binding | an altered schema source stops at the identity step; the engine refuses a changed input digest, output digest, authored input, default evidence value, a removed evidence entry, an unbound path and a path bound to a missing key |
| A schema that is not flat | refused with `not_flat` in the chain, never walked into |

**The proof of the proof.** The checker is shown to fail: its 23 tests build a clean pair of dumps from the reference and break one thing at a time, reading the message and not the count: a case missing from one language and from BOTH, a repeated case, an invented one, a skipped or invented step (a missing `standalone`, `receipt`, `chain`, `authored_input`, `schema_digest`; an added `resolve`), a wrong schema identity, another authored input, a changed standalone object, a builder object that is not the standalone one, a receipt that differs from the reference even with its own digest, an artefact or the capability map of another run, the receipt of another case, the swapped binding, a wrong or missing binding, the twin case that lost its two equal values, a capability change that moves nothing, that moves the input, or that moves more than the output digest. Ten defects planted in the checker itself (the chain digest, the binding, the two-objects check, the twin point, the output-digest-only check, the cross-language check, the artefact and run key checks, the receipt-against-reference check and the missing-case check) each turned a test red. The harness side was seen to fail the same way (a swap case that stops being accepted, a mixed run that stops being refused).

**What this does not prove.**

* Where the capability map comes from. It is a caller input, checked for form; no source is authenticated.
* The schema source and the grammar identity. The engine cannot see `schema_name`, `schema_version` or `schema_digest` (section 18); they are the identity step's, the Python reference's and the checker's.
* The release record is a fixture (section 17): a well-formed receipt here is not an authentic one.
* Reference resolution, a snapshot, a non-flat document and a list-element selector: not done, refused explicitly (section 20 for the selector).
