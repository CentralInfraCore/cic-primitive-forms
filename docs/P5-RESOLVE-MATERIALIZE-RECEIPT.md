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
* The reasons are of this module, not codes of ADR 0007. The order of the checks is fixed so that the two languages give the same first reason.

**Proof.** `testdata/p5/materialize.json` is written by `tools/p5/materialize_cases.py`, which computes every expected projection and digest itself (Python's JSON writer and `hashlib`, a third reader that has seen neither implementation); 6 accepted and 13 refused cases. Rust and Go compare against it (`rust/tests/materialize_corpus.rs`, `go/materialize_corpus_test.go`, plus hand-built shapes for what a compiled schema cannot give). Two harnesses build the **engine's own** object from the same file and compute its digest: `tools/p5/engine-harness-rust` (`MaterializedObject::try_new`, `materialized_object_digest`) and `tools/p5/engine-harness-go` (`NewMaterializedObject`, `MaterializedObjectDigest`), both against the pinned engine in CI. They also refuse one key more and one key less (`E_INCOMPLETE_OBJECT`) and see a changed value, capability or provenance change the digest. The first run of the Python-computed digests against both engines agreed. 24 planted defects (12 per language).

**What this does not prove yet.**
* The library's output is compared to the engine **through the file**, not field by field on generated documents: the differential with the engine as the third reader, on generated flat documents, is the next step of P5-2.
* The independence of `expected_keys` is one way so far: the case file lists them from the generator's side. The generated differential is where a key lost on both sides would be seen.
* No fuzz target yet on the new input.
