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

## 9. Risks

* The canonical form (point 3) is a byte-exact contract against a repository that evolves: a change of section A there moves a digest here. The pin and the vectors are the defence; the
  cost is a migration when the pin moves, as the grammar pin already is.
* The mapping of a tree to flat keys may not have a faithful answer for list elements (point 1); that would be a finding about the contract, not a defect to patch around here.
* `engine_identity` makes two correct receipts of one input differ; a consumer that compares receipts by bytes will see that.
* No signature: the receipt proves consistency, not authorship (the engine says so). Anything that needs authorship is a separate envelope and another phase.

## 10. P5-1: what is built (2026-10-30)

`canonical_bytes` / `canonical_digest` (Rust, `rust/src/canonical.rs`) and `CanonicalBytes` / `CanonicalDigest` (Go, `go/canonical.go`): engine section A (A1-A7) over the value of this library, within the range of decision G.
A float, an integer beyond 64 bits, a repeated key (and, in Go, a string that is not UTF-8) have no canonical bytes: the refusal is a `CanonicalError` with the reason `unsupported_value`, `duplicate_key` or `not_utf8`; none is a code of the registry of ADR 0007.

**Proved:** the table `testdata/p5/canonical-vectors.tsv` (26 lines) is read by both languages. Its 7 in-range lines from the engine's `conformance/canonicalize` are checked against the engine at the pinned commit
(`testdata/p5/engine-pin.txt`, `tools/p5/engine_vectors.py`, a step of the `rust` job); a new engine vector fails that check until it is classified, so the range is never widened or narrowed unseen. Its 13 own lines are checked against a
reference written apart in that tool (a third reader). The 6 engine vectors outside the range are listed by name as `out_of_range`, and both languages assert that the parser refuses them. The Rust side passed the table and 23 of 23 mutants of `canonical.rs`
(run locally); the Go side has not been run here (no Go toolchain), so its first run is CI.

**The rest of P5-1 (2026-10-30):** a Rust/Go differential on 20 000 generated values (`tools/parity/gen_canonical.py`, `compare_canonical.py`, `selftest_canonical.py`, the dumps `rust/tests/parity_canonical_dump.rs` and
`go/parity_canonical_dump_test.go`). Each value comes with the answer of a reference written apart from both implementations, so a defect shared by the two languages is seen too; there is no list of known differences. The reference
also fixes which fault is met first (a repeated key of a map before its members, then the members in the order of the sorted keys). The fuzz targets `FuzzCanonical` (Go) and `canonical` (Rust): a text that parses has canonical bytes
that are one line of valid UTF-8 (JSON, in Go), carry the digest of A7, and are unchanged by `emit` and `parse`. Run here: the differential agrees on all 20 000 and its self-test turns red on all 12 breaks; the Go fuzz target ran 296 000
executions without a failure; the Rust target's logic ran on its seeds as a test (no nightly toolchain here for `cargo fuzz`, so CI is its first real run).

**Not yet:** planted defects for the canonical bytes in `tools/seeded_defects.py` (the diff and the patch have them), and the engine's `digest` function checked beyond the empty mapping. The order of a set (decision H) is not touched: a sequence is written as given.
