# P5-3: raw YAML and the parser boundary, Unit 3 proof contract

Status: **contract**, 2026-11-04. Documentation only. It adds no generator, no dump, no compare, no selftest, no workflow change and no production receipt API (which stays on hold). It is the contract for the part that `docs/P5-DIGEST-MAPPING-DIFFERENTIAL-PLAN.md` (#154, sections 3 to 5, "the raw class") left out of Unit 2 (`docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md`, #156/#157). Nothing here is evidence until an implementation exists, has its selftest shown red, and has passed its own review.

## 1. The question

Unit 2 shows what the three implementations of the **mapping** do with a record that is already a value tree. It says nothing about the step before it: whether the same **bytes** are read with the same meaning. The path to prove is

```
raw record bytes --> parser --> Node --> map_release --> three receipt digests (or one refusal)
```

and the security question behind it is the one the receipt exists for: *on exactly which input was the system entitled to decide?* A record can be authentic as bytes and still be read as two different documents by two readers. The mapping then runs on whichever reading won.

## 2. The requirement (and what is deliberately not required)

**Not required:** that Python, Go and Rust read every YAML construction in the same way. They do not, and making them do so is not the goal.

**Required:** that the set of inputs the production path accepts is **defined exactly**, and that **no parser can accept, unnoticed, an input on which a reader used in the verification chain would read something different**. That is stronger than parser parity: parity can hold on everything that matters and still let one reader differ in a place nobody looked at; a defined acceptance set cannot.

The acceptance set `P` (a definition, not an implementation) is:

> `b` is in `P` iff the Rust `parse` **and** the Go `Parse` both accept `b` as exactly one document **and** return the same tree (same structure, same scalar kinds and values, same order of mapping entries). The mapping is then applied to that one tree. Everything else is refused, whatever the reason: one accepts and the other refuses, the trees differ, either gives any error.

Consequences that the proof has to show and not merely assume:

* **Fail closed on disagreement.** A Rust/Go difference is never resolved by choosing a side. The 13 pinned `known_divergences` in `testdata/p1/parity.json` and the 299 inputs of `testdata/p1/parity-known.txt` (open Rust/Go parser divergences on the fixed-seed run) are, by this definition, **outside `P`**. They stay open as parser findings (their narrowing is not part of this unit); what Unit 3 shows is that none of them is admitted.
* **`P` is not the truth.** Two parsers can be wrong in the same way. Membership in `P` therefore also has to match an expectation that comes from **neither** parser (section 4).
* **The Python reader is not a member of `P`.** PyYAML is the reader the Python side of the ecosystem uses; it is a stated **oracle for difference**, never a vote in the admission (section 5).

## 3. Readers and outcomes

Three readers, each driven by the same input file, one result per case (the format of Unit 2, section 3, extended):

| Reader | Role | Parse step | Mapping step |
|---|---|---|---|
| Rust | member of `P` | `parse` (Rust crate) | `map_release` |
| Go | member of `P` | `Parse` (Go module) | `MapRelease` |
| Python | oracle of difference only | `yaml.safe_load` (version recorded) | `tools/p5/digest_mapping.py` reference |

Result line (JSON lines, exactly one per input line, in input order):

```
{"id", "ok": true,  "tree_sha256": "<digest of the canonical JSON of the tree>", "mapping": {three digests} | {"refuse": reason, "field": member}}
{"id", "ok": false, "phase": "parse", "code": "<parser code>"}
```

* The tree is compared through a canonical rendering fixed in the implementation PR (kinds kept: the integer 3, the float 3.0, the string "3", `null` and the empty scalar stay distinct; mapping order kept). Both the digest and, for the first red case, the rendering itself go into the report.
* `code` is one of the parser codes of `go/errors.go` (`DUPLICATE_KEY`, `UNSUPPORTED_SYNTAX`, `YAML_SYNTAX`, `LIMIT_EXCEEDED`, ...). For admission and for the construction verdict the code is reduced to its **class**: *duplicate key*, *syntax or unsupported syntax or limit* (the existing `compare.py` convention: two different refusal codes are the same answer with a different reason; the reasons are kept in the report).
* A crash, a panic, a hang or a resource blow-up is not a refusal. It is written as `CRASH` / `TIMEOUT` and is red. Each reader runs with a wall-clock limit and a memory limit (the earlier mutation work found 2 to 8 GB allocations from string and array mutants; the limit is a stated parameter of the run).
* Input is **bytes**: the input file carries the record as hex, so that invalid UTF-8, a BOM, a lone CR and a NUL reach the parser as they are and not as what a JSON string would make of them.

## 4. The inputs, and the independent expectation

One JSON-lines input file as in Unit 2 (identity: its SHA-256 plus the parameters; the runner regenerates and compares; unique ids). Three kinds of case.

**4.1 Built from a value (admit by construction).** The generator holds a record as a plain value tree and writes it as YAML with its **own small emitter**, independent of all three parsers and of the Forms emitter. The expected outcome is *admit, with exactly this tree*, and then the mapping outcome of Unit 2's yardstick for that tree. Styles varied on purpose, each a different spelling of the same tree: block and flow, quoted (single, double, with escapes) and plain scalars where YAML 1.2 makes them the same string, block scalars where they are, comments, blank lines, key order, a base64 value that starts with `+`, `-`, `/` or a digit (the real record has `+i4VBWNk...`), a value that ends in `=`, a very long key (past the 512 characters of the explicit-key form), the real `v0.2.1` record.

**4.2 Built by a text defect (refuse by construction).** One defect injected into a valid text; the expected class is written by hand from `docs/P1-SUPPORTED-SUBSET.md` ("Explicitly not supported" and the duplicate-key rule), together with the check order, not computed. Catalogue (each at every place it can apply, nested levels and artifact entries included, one at a time and in pairs whose order is fixed):

* **duplicate keys:** the same key twice at the root, in `release`, in `provenance`, in `artifacts`, in an entry of `artifacts.files`; spelled differently but equal (`a` and `"a"`, `'a'`); in block and in flow style; in a member the mapping does not read (a duplicate anywhere in the document refuses the whole document).
* **documents and markers:** a second document, a leading or inner `---`, `...`, a `%` directive, an empty document.
* **anchors, aliases, tags, merge keys, non-scalar keys.**
* **scalars:** the envelope as `3`, `+3`, `03`, `0x3`, `0o3`, `3.0`, `3e0`, `"3"`, `'3'`, `3 `, `!!int 3`, `true`; `null`, `~`, `Null`, `NULL`, the empty scalar, `"null"`; the YAML 1.1 spellings `yes`, `no`, `on`, `off`, `y`, `n` for a boolean position and for a digest position; an integer beyond 64 bits.
* **text level:** a tab, CRLF, a lone CR, a BOM (first and later), U+0085, U+2028, U+2029, NBSP, a zero-width character, NUL, other control characters, UTF-16, invalid UTF-8, an overlong encoding, a missing final newline.
* **block scalars and folding:** a digest written as `|` or `>` (a trailing newline makes it another string), a value continued on the next line, a trailing space, a `#` right after the value.
* **limits:** 1 MiB, depth 64, 100 000 nodes, one below and one above each.

**4.3 Pinned divergence inputs (not admitted by construction of `P`).** The inputs of `testdata/p1/parity-known.txt` and the `known_divergences` of `testdata/p1/parity.json`, with the expectation *not in `P`* (they are not required to be refused by each parser separately; they are required not to be admitted).

**Undetermined cases.** If the repository has no documented verdict for a construction, the case does **not** get an expected answer invented for it. It goes into a committed **undetermined list** (id, construction, why), is counted in the report, and is judged only by the fail-closed rule (it must not be admitted unless both parsers agree and the owner has classified it). The owner decides each entry; an undetermined list that grows is a visible event, not a pass.

## 5. Verdicts per case

Per case, four independent questions, all asked on every case:

1. **admission:** is `b` in `P` (Rust and Go accept and agree on the tree)?
2. **construction:** does the outcome match the one fixed by construction? An admitted case needs the constructed tree and the constructed mapping outcome; a refused-by-construction case admitted is **red even when both parsers agree** (the common-mode check). A case that must be admitted and is not is red too (a parser that is too strict is a finding, not a pass).
3. **fail closed:** every case where Rust and Go differ in any way is a **finding** listed by id, never normalized; and the harness shows that such a case is not in `P`.
4. **difference from Python:** for every admitted case, compare the Python reader's tree with the admitted tree **on the members the mapping reads** (`release.envelope`, `release.build_hash`, `provenance.envelope`, `provenance.grammar_*`, `artifacts.files` and what it holds) and on the rest. Classes:
   * *same*: nothing to report;
   * *different outside the members read*: counted and listed, not blocking;
   * *different on a member the mapping reads, on an admitted input*: a **blocking finding**. It is the dangerous case: the input is admitted, and a PyYAML-based reader of the same bytes sees something else. It turns the run red unless it is in the known-differences file with the owner's written acceptance and the exact answers;
   * *Python accepts, Forms refuses*: expected for the cases of the catalogue (PyYAML takes the last of two equal keys without an error; this is the first entry of the known-differences file, with its measured answers); counted on every run, red if it stops occurring or its answer changes.

A case is **green** when 1 to 3 hold and 4 raises nothing blocking.

## 6. Fail-closed rules

Each makes the whole run red, none can be waived by a flag (all of Unit 2, section 5, apply, plus):

* an input line whose bytes do not match its stated hex or digest; a reader that was not run or whose source digest could not be taken;
* a result line outside the vocabulary (a code that is not a parser code, a mapping reason outside the 11 of the contract, `CRASH` or `TIMEOUT`);
* a refused-by-construction case admitted, or an admitted-by-construction case not admitted;
* a case where Rust and Go differ that is not reported as a finding, or a pinned divergence input that is admitted;
* a blocking difference from Python that is not in the known-differences file; a known difference that no longer occurs or occurs with another answer;
* an undetermined entry whose case turns out to be admitted without an owner classification;
* a class below its floor (a thin run), with the floors written in the report.

## 7. The proof can fail (selftest)

`tools/parity/selftest_raw_yaml.py` (name to be fixed in the implementation) is part of the implementation and "complete" is not claimable before it is green.

* **Comparison breakage**: all of Unit 2, section 7 (dropped, duplicated, reordered, swapped, unknown, missing-from-all, truncated, another input set, unfinished last line), and for the raw class a hex line that was changed in one reader's copy.
* **Planted defects per parser**, in a copy of the source (never in the tree), as a committed list with the classes each must turn red, RED then GREEN after restore: a duplicate key accepted (first wins, last wins), a tab accepted, a second document accepted, an anchor or alias followed, a merge key followed, a BOM after the first character accepted, CRLF folded, `3.0` read as `3`, `0x3` or `03` read as `3`, `yes` read as a boolean, a block scalar's trailing newline stripped, a depth or size limit off by one, a control character accepted.
* **The admission rule saves the day (one parser wrong)**: the defect planted in Rust only, and in Go only, must show as a Rust/Go finding and the case must stay out of `P`.
* **Common mode (both parsers wrong the same way)**: the same defect planted in Rust and Go together must be red through the construction verdict, with admission green. This is the only protection against two parsers sharing a misreading.
* **The Python oracle**: a planted change of the oracle (a reader that fails closed on duplicates, a reader that reads `yes` as a string) must change the known-differences count, and the run must be red because the known-differences file no longer matches.
* **Equivalent changes** are listed with the reason and must stay green.
* **The harness itself**: a reader that returns the same canned answer for every case, a reader that echoes the expectation, and a reader that never runs must each turn the run red.

## 8. The report

One JSON document; the run is red if any field cannot be filled. As Unit 2, section 8, plus: the path and SHA-256 of the **parser** source files of Rust and Go (the list is fixed in the implementation) and of the mapping files; the PyYAML version; the SHA-256 of the input file, of the generator, of the known-differences file, of the undetermined list and of the selftest file (named as the identifier of the definitions of the planted defects and checks); the memory and time limits; per class: cases, admitted, refused, Rust/Go findings (ids), blocking Python differences (ids), known differences, undetermined; the counts of the pinned divergence inputs and that none was admitted; `evidence` true only for a clean tree, a green compare and a selftest that ran and passed.

## 9. Where it runs, and the order of the work

By **one local command**; **nothing under `.github/workflows` changes**. Whether, where and how often it runs in CI is a separate owner decision. Order, each a small change with its own review: **3a** the generator and the construction yardstick for the raw class (its own tests, planted defects against a reference reader); **3b** the Rust and Go raw dumps, the Python oracle dump and the compare (admission, construction, fail-closed, Python difference); **3c** the selftest, the known-differences file with the PyYAML duplicate-key entry, the undetermined list and the report. Nothing starts before the decisions of section 10 are given.

## 10. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | The acceptance set `P`: Rust and Go both accept with the same tree | yes; no tie-break between the two parsers |
| D2 | PyYAML is an oracle of difference only, never part of admission; a difference on a member the mapping reads, on an admitted input, is blocking | yes |
| D3 | Policy for CRLF, lone CR, tab, BOM, NBSP and the other text-level cases: refuse rather than support | refuse, fail closed; each is listed with the class it belongs to |
| D4 | A duplicate key anywhere in the document (also in a member the mapping does not read) refuses the whole document | yes, as the Forms parsers already do |
| D5 | The pinned Rust/Go divergences: stay open as listed findings outside `P` (not closed in this unit) | listed, none admitted; the narrowing of the flow scan is its own work |
| D6 | The undetermined list: who classifies, and whether an undetermined case may ever be admitted | the owner classifies; until then never admitted |
| D7 | Size, seed, floors and the memory/time limits | a few tens of thousands of cases, seeded; floors and limits in the report |
| D8 | Which parser decides in the production API: it must parse the **same bytes it authenticated**, with the Forms parsers, and never receive a tree parsed elsewhere | yes, and this stays the condition for ever lifting the hold on the production receipt API |

## 11. Not claimed

No harness exists. Nothing here shows that the parsers agree beyond the existing corpus and the fixed-seed parity run, or that `P` contains only what it should. The proof does not decide the authenticity of a release, the trust anchor or the time of a signature (`docs/P5-TRUST-MODEL-PLAN.md`), and it is not the production receipt API, which stays on hold and takes the **bytes** of an authenticated record.
