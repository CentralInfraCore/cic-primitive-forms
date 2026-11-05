# P5-3: the admission contract (decisions D3 and D9): when may the system read a raw input, and when must it refuse

Status: **contract design**, 2026-11-05. Documentation only. It changes **no code, no accepted set, no workflow and no production receipt API**; the present behaviour of the parsers and of the differential stays exactly as it is. It builds on `docs/P5-RAW-YAML-UNIT3-CONTRACT.md` (#158: `P_parity`, `P_allowed`, `P_effective`, D1 to D10) and on the measured differential of Units 3a to 3c (#159, #160, #162; the Go parser fix #163). The aim is a contract from which one can derive, without a further conversation, **when the system is entitled to accept a raw input and when it is obliged to refuse it**.

## 1. The principle: three different things

> That two parsers read an input in the same way proves that the reading is shared. It does not prove that the reading is permitted, and it does not prove that the input is the one that was authenticated.

Three statements, kept apart. An input is admitted only when **all three** hold:

| Statement | Question it answers | Decided by | Evidence |
|---|---|---|---|
| **Permission** `P_allowed(x)` | Is this construction permitted by the normative contract? | the normative table (section 3) and the decisions of section 4: **never by a parser** | the table, one decision record per construction, the cases of the differential |
| **Interpretation** `P_parity(x)` | Do the readers we rely on read it as the same thing? | Rust and Go (semantic parity, D10); PyYAML as the oracle of difference | the differential: dumps, compare, selftest |
| **Entitlement** `VerifiedProvenance(x)` | Are these exactly the bytes that were authenticated, by whom, and when? | the trust model (`docs/P5-TRUST-MODEL-PLAN.md`), outside this contract | signatures, the countersignature, the time evidence |

```
Admit(x)  =>  P_allowed(x)  and  P_parity(x)  and  VerifiedProvenance(x)
```

This is a **necessary** condition, not the whole specification of an admission API. In particular: the three are about the **same bytes** (section 5, step 0 and decision D8 of the Unit 3 contract): the reader that interprets the record is run on the bytes whose authenticity was verified, never on a tree parsed elsewhere. `P_effective = P_parity and P_allowed` stays an **analysis result** until the mechanism of section 5 is built and proven; it is not an admission.

## 2. What is decided here, and what is not

Decided (as a contract, to be reviewed): the rule for classifying a construction (section 4), the structure of the enforcement (section 5), the proof obligations and the negative tests (section 6), and for each construction that is `undetermined` today **what a decision has to contain** and a **proposal** (the proposals are not decisions).

Not decided here: the production code, the language and location of the gate, the receipt API, the trust anchor, the time of a signature, the authenticity of a release, and the Rust performance issue (#164).

## 3. Permission: the normative table

`P_allowed` is a table, not a code path. The table is **data with a digest**: each row has an identifier, the construction in words, the verdict (`allow` or `refuse`), the refusal class, the source (the sentence of `docs/P1-SUPPORTED-SUBSET.md`, or the decision record that introduced it) and at least one case of the input set. The digest of the table is recorded in every report and, later, in every admission record, so that a verdict can be traced to the version of the rules that gave it. **A change of the table is a change of the contract**, with its own review; it is never a test exception.

**The table has two kinds of rows, `allow` and `refuse`, and covers the permitted constructions as explicitly as the forbidden ones.** A construction is allowed if and only if **an `allow` row matches it and no `refuse` row does**; a `refuse` row always wins over an `allow` row; a construction matched by no row is refused (the table is closed). So "no row, refuse" never shuts out something the contract supports, because everything it supports is a row.

* **`allow` rows** are exact *families of constructions*, never "any YAML". They are taken from the positive description of the accepted subset in `docs/P1-SUPPORTED-SUBSET.md` ("Supported subset"): one document; block and flow mappings and sequences (with the limits of depth and nodes); mapping keys that are the text of a scalar (and the emitter's explicit `? key` form for a key past 512 characters); quoted scalars (single and double, with the escapes the parsers support) and block scalars (`|`, `>` with their chomping), all read as strings; plain scalars `null`, `~` and the empty scalar, the boolean spellings, decimal integers that fit 64 bits (`+` and `-` allowed), and every other plain scalar as a string; comments, blank lines, indentation and key order; `---` as the first content line. Each family has an identifier, its exact scope in words and in the cases that define its edges, and is stated *with its limits*: a family does not stretch to what the readers happen to accept beyond it.
* **`refuse` rows** are the "Explicitly not supported" rows of the same document plus the rules the Unit 3 work made explicit (decoding, limits, duplicate keys anywhere in the document), and every later decision record that says `refuse`.
* **Coverage and conflict are tested, not assumed.** (i) *Positive coverage*: every `allow` row has a case that every reader must accept with the constructed tree, and every case of the valid classes (`built`, `tree`, `value`) is matched by an `allow` row; a valid case no row matches is red (the table would shut out what the contract supports). (ii) *Negative coverage*: every `refuse` row has a case. (iii) *No conflict*: no case is matched by an `allow` row and a `refuse` row of the same decision without the precedence being the stated one (refuse wins), and no two rows give different verdicts for the same case through an unstated overlap; a test lists every overlap. (iv) *Totality*: every case of the input set is classified by the table into `allow`, `refuse` or "no row (refused)", and the third class may contain only the cases labelled `undetermined`.
* **This is not an enlargement of the accepted set.** The `allow` rows describe what the documented subset says is accepted today and nothing more; no construction becomes allowed by being written here.

## 4. Classifying a construction: the rule for D3

No construction becomes `allowed` because the readers agree on it, because it is common, or because nothing went wrong in a test. The default for every construction that is not a row of the table is **fail closed**. A construction enters the table, as `allow` or as `refuse`, only by its **own decision record** containing:

1. **the original bytes** (a minimal case, as hex) and, for `allow`, the range the decision covers;
2. **the intended semantics**: what the construction is supposed to mean for a record, in one sentence;
3. **the behaviour of every reader in the chain**: Rust, Go, PyYAML, and any other reader that touches the same bytes (measured, with the run it comes from);
4. **the security consequence**: what an attacker or a mistake gains if it is allowed (a different value on a member the mapping reads; an invisible difference between what a reviewer sees and what is read; a different hash of the same meaning; a resource cost);
5. **the decision** and its test expectation: for `allow`, the tree and the mapping outcome that every reader must give, and a case for each side of the boundary; for `refuse`, the refusal class and the cases;
6. **the owner's acceptance** (who, when, where recorded).

**Reader-chain rule.** Every member the mapping reads is checked against the oracle, and a difference is **blocking by default**. A construction on which the readers of the chain differ on such a member may be `allow`ed only if every reader that differs is brought into agreement by the decision (it is not allowed to rely on one reader being the one that counts), or by an **exception that is itself a decision record**: the exact answers of every reader, the interpretation risk that is accepted, and the owner's explicit acceptance of that risk. An entry in the known-differences file is **not** a permission; it is a record of a measured difference, and it never turns a blocking difference into an admission. Otherwise the construction is `refuse`. (PyYAML reads `yes`, `no`, `on`, `off` as booleans and the Forms parsers read strings: on the envelope or on a digest this is exactly such a difference.)

The decisions are **not taken in bulk**. One record per construction, reviewed one at a time; a record may say "refuse, because nothing in the intended semantics needs it", which is the expected outcome for most of them.

### 4.1 The constructions that are `undetermined` today

Measured in the differential run on the content of `d43ffc2` (the run was made on the fix commit `3c88e82` before the squash; go 1.25.0, rustc 1.99.1, python 3.12.3, PyYAML 6.0.1; the case `undetermined:*` of `tools/parity/gen_raw_yaml.py`, record with the construction applied). "Accept" means the reader accepted the text and read the same tree as for the plain record; "mapping" is the Unit 2 outcome on that tree. Each row is a decision to be taken on its own; the last column is a **proposal** for the decision, not the decision.

| # | Construction | Rust | Go | PyYAML | What a decision has to settle | Proposal (not a decision) |
|---|---|---|---|---|---|---|
| U1 | text without a final line break | accept | accept | accept | the signed bytes differ from the emitter's; a trailing block scalar is already refused | refuse: records are emitted with a final LF |
| U2 | CRLF line breaks | accept | accept | accept | the bytes (and any digest of them) differ from the LF record; values do not | refuse: the emitter writes LF |
| U3 | a lone CR as a line break | accept | accept | accept | CR alone is a line break to YAML 1.2 and not to every tool; the hazard of two line structures | refuse |
| U4 | a leading BOM (U+FEFF) | accept | accept | accept | a BOM changes the bytes but not the value; a BOM inside the text is already refused | refuse: the record is UTF-8 without BOM |
| U5 | NBSP in a comment | accept | accept | accept | invisible difference between what a reviewer sees and what is read | refuse (or refuse comments altogether, see U12) |
| U6 | a zero-width character in a comment | accept | accept | accept | as U5 | refuse |
| U7 | UTF-16 (BOM, LE or BE) | refuse | refuse | refuse | all three refuse **only because the harness decodes UTF-8 first**; there is no rule that says so | allow nothing but UTF-8: make "the input is valid UTF-8" a row of the table |
| U8 | invalid UTF-8 (a stray byte) | refuse | refuse | refuse | as U7 | the same row as U7 |
| U9 | an overlong UTF-8 encoding | refuse | refuse | refuse | as U7 | the same row as U7 |
| U10 | an empty document | accept | accept | accept | the tree is `null`; the mapping then refuses `field_missing` | refuse: a record is a mapping |
| U11 | a document of one comment | accept | accept | accept | as U10 | refuse |
| U12 | comments in general | accept | accept | accept | not undetermined (comments are in every style of the valid cases), but U5 and U6 depend on whether comments may carry anything but printable ASCII | allow comments of printable ASCII only, or none: decide with U5 |
| U13 | the plain scalars `Null`, `NULL` | accept (null) | accept (null) | accept (null) | the Forms code reads them as null, the subset document names `null`, `~` and the empty scalar | decide whether the spelling case is part of the contract; a refuse is the cautious reading |
| U14 | the plain scalars `yes`, `no`, `on`, `off`, `Yes`, `NO` | string | string | **boolean** | a difference on a read member (the envelope) between the chain's readers | refuse (reader-chain rule) |
| U15 | the plain scalars `y`, `n` | string | string | string | no difference today; the same family as U14 | decide with U14 |
| U16 | a text of exactly 1 MiB | accept | accept | accept | the limit says "more than 1 MiB" | write the boundary in the table: at the limit is allowed or refused, with a case on each side |
| U17 | nesting 64 deep | refuse | refuse | accept | the limit says "depth 64"; both Forms parsers refuse this case, PyYAML accepts | write the boundary in the table, with a case on each side |

U7 to U9 and the "input must be valid UTF-8" row are the one place where today's agreement is an accident of the harness (the parsers take text; the bytes are decoded before they see them). The production path has to refuse non-UTF-8 itself, by a rule.

The pinned parser-divergence inputs (`testdata/p1/parity-known.txt`, 201 distinct) are classified apart in `tools/parity/pinned_classification_raw_yaml.json` (72 allowed, 13 refused by a documented rule, 116 undetermined: 79 where only PyYAML finds the text invalid, 25 bare-scalar documents, 12 demoted by conservative triggers). They are outside `P_parity` by the Unit 3 contract and are not admitted whatever their `allowed` label; the same decision-record rule applies if one of them is ever to be allowed.

## 5. Enforcement: the structure of the admission (the D9 design)

The direction the review chose is **two parsers compared, plus an independent normative gate** (option (a) of D9). The structure, in the order in which the checks run; the first refusal ends the admission and names its reason class.

```
bytes (the authenticated record)
 0. provenance      the bytes are the ones whose signatures, countersignature and time evidence were verified (outside this contract)
 1. decode          valid UTF-8, within the size limit (a row of the table); otherwise refuse
 2. subset gate     the normative table, applied to the BYTES by a reader that is independent of both parsers: every refuse row, every construction outside the table -> refuse
 3. parse           the Rust and the Go parser, each on the same bytes, in its own killable process with the resource limits; either refuses, crashes or times out -> refuse
 4. parity          the two trees are semantically identical (D10: positions are not compared for admission) -> otherwise refuse
 5. read members    the oracle's reading of EVERY member the mapping reads equals the admitted tree -> otherwise refuse (an exception exists only as a decision record, section 4; the known-differences file alone is not one)
 6. map             the mapping (Unit 2) is applied to the one common tree; its refusals are refusals
 -> Admit(tree, mapping, evidence)  with  the table digest, the versions and digests of the readers, the digest of the input
```

* **The gate (step 2) is a third, independent reader.** "Independent" means: not derived from the code of either parser and not a thin wrapper of either YAML library; the table is its specification. Its language and location are an implementation choice for a later decision; this contract fixes what it has to be proven to do (section 6). The reference reader that the Unit 3a tests use (`tools/parity/raw_yaml_subset_ref.py`) is **not** this gate: it is a test yardstick on top of PyYAML and says so.
* **Everything fails closed.** A crash, a timeout, a memory limit, an unknown error, a disagreement, an input outside the table: refusal with a named class. There is no "warning" outcome and no fallback to one reader.
* **The limits are execution limits.** Each reader runs as a separate process under a wall-clock and a memory limit and is killed when it overruns; this is what the differential already does and what production has to do as well.
* **The decision is auditable.** An admission record carries the digest of the table, the versions and the binary digests of the readers, the digest of the input, and the three statements of section 1 with their evidence. A refusal carries its reason class and the step.
* **Cost.** Two parsers and a gate on a record of a few kilobytes is cheap; the performance and integration decision (in-process or as a service, caching, concurrency) is a separate production decision and is **not** made here.

## 6. Proof obligations and negative tests

The gate and the structure are **mechanisms under test of the differential**, not an addition to be trusted. Before any of this is called an admission:

1. **Every refuse row has a case that the gate refuses**, and every `allow` row has a case it admits (the coverage and conflict tests of section 3 are part of this); the input set (`tools/parity/gen_raw_yaml.py`) gets the cases of each decision record, built by construction, one case on each side of every boundary.
2. **The gate is proven to be able to fail.** The selftest of Unit 3c is extended: a gate with a planted defect (a refuse row removed, a boundary off by one, a construction outside the table admitted) turns the run red on the named classes; a defect planted in **both parsers and the gate together** is still red through the construction labels; a gate that echoes its expectation is red; a gate that never runs is red. (The mutation work of Unit 3c is the pattern.)
3. **Each stage is shown to be necessary.** For each of the stages 1 to 5 there is at least one case that only that stage refuses (disabling it in a copy admits the case): the gate does not hide a defective parser, and a defective gate does not hide behind the parsers.
4. **The table and the cases cannot drift apart.** A test regenerates the cases from the table and the table digest from the file, and a row without a case, or a case without a row, is red.
5. **The report states what it proves.** `evidence` stays true only for a clean tree, a green compare and a passed selftest, and the admission claim is made only for the mechanism that passed these obligations, with the digest of the table it was proven against.
6. **No silent change of the accepted set.** Until the mechanism is proven and reviewed, the behaviour of `parse`, `Parse` and the mapping is unchanged; this contract adds no accepted construction.

## 7. Decisions asked of the owner

| # | Decision | Recommendation |
|---|---|---|
| A1 | The principle of section 1: permission, interpretation and entitlement are three statements, all three necessary; parser agreement never grants permission | yes |
| A2 | The table is closed data with a digest; anything outside it is refused; a change of it is a contract change | yes |
| A3 | The rule of section 4 for D3: one decision record per construction, fail closed by default, the reader-chain rule, no bulk decisions | yes |
| A4 | Take the 17 constructions of 4.1 one at a time, in this order: U7 to U9 (UTF-8) first, because the present refusal is an accident of the harness; then U14/U15; then the rest | yes |
| A5 | The structure of section 5 (steps 0 to 6) as the design of the enforcement, option (a) of D9, with the gate as a third independent reader | yes; the language and location of the gate in a later decision |
| A6 | The proof obligations of section 6, in particular that the gate is a mechanism under test of the differential, with its own selftest | yes |
| A7 | Whether the oracle's reading (step 5) is a condition of admission for all members the mapping reads or only for those a decision names | **Decided (review of #165): every member the mapping reads** (`release.envelope`, `release.build_hash`, `provenance.envelope`, `provenance.grammar_*`, `artifacts.files`); a difference is blocking by default; an exception only by a decision record with the exact answers and the explicit acceptance of the interpretation risk; being in the known-differences file is not a permission |
| A8 | The production receipt API stays on hold until sections 5 and 6 are met for the table it will use | yes |

**Positions recorded in the review of #165.** A1, A3, A4 (UTF-8 first), A5 (as a design direction), A6: accepted. A2: accepted, with the positive coverage of section 3 (the table's `allow` rows). A7: decided as above. A8: confirmed. These decisions are about the structure of the contract; **none of them allows a single construction of 4.1**: each of U1 to U17 still needs its own decision record.

## 8. Not claimed

This is a design. No gate exists, no construction of 4.1 is decided, and `P_effective` is not an admission. The measurements in 4.1 are of one local run of the differential on the content of `d43ffc2` and show what the readers do, not what they should do. Nothing here proves the authenticity of a release, the trust anchor or the time of a signature (`docs/P5-TRUST-MODEL-PLAN.md`), and nothing changes the production receipt API, which stays on hold and takes the **bytes** of an authenticated record.
