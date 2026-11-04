# P5-3: digest mapping differential, Unit 2 proof contract

Status: **contract**, 2026-11-04. Documentation only. It adds no dump, no compare, no selftest, no workflow change and no production receipt API (which stays on hold and must take the **bytes** of the authenticated record). It refines sections 2 to 7 of `docs/P5-DIGEST-MAPPING-DIFFERENTIAL-PLAN.md` (#154) for Unit 2, the unit that runs the three implementations on the case set of Unit 1 (`tools/parity/gen_digest_mapping.py`, #155). The implementation of Unit 2 is a separate pull request with its own review; nothing here is evidence until that exists and its selftest has been shown red.

## 1. What Unit 2 has to be able to say

"In every case of the input set, Python, Go and Rust gave the same value or the same refusal (reason and field), and that answer is the one fixed by construction." And, equally important, that the run **would have said otherwise** had one of them been wrong. A statement of the first kind without the second proves nothing.

## 2. The input set, and its identity

* The input set is the concatenation, in this order, of (a) the 83 shared cases of `testdata/p5/digest-mapping.json` and (b) the output of `gen_digest_mapping.py` at a stated seed and counts. Written as one JSON-lines file, bytes exactly as `gen_digest_mapping.render` produces them for (b) and a fixed rendering of (a).
* The **identity of the set** is the SHA-256 of that file. It is computed once by the runner, written into the report, and passed to every consumer; a consumer that is handed a file with another digest refuses to run.
* Parameters (seed, values, pairs, valid) are part of the identity: they are in the report next to the digest, and the runner recomputes the set from them and requires the same digest (the generator is deterministic; a mismatch is red).
* Every case carries a unique `id`. The id is the only join key between the input, the three results and the expectation. Ids are never reused across classes.

## 3. The dump files

One JSON-lines file per implementation, **exactly one result line per input case**, in input order:

```
{"id": "<case id>", "ok": true,  "value": <digest string | {three digests}>}
{"id": "<case id>", "ok": false, "reason": "<contract reason>", "field": "<member or null>"}
```

* `value` is the receipt digest (a conversion) or the three receipt digests (a mapping), in the spelling of the contract (`sha256:` plus 64 lowercase hex).
* A refusal names the contract reason and, for a mapping, the member (`field`; `null` for a conversion). A crash, a panic or an exception is not a refusal: the dump writes `{"id", "ok": false, "reason": "CRASH", "detail": "<type>"}` and the case is red against any expectation. The dump never swallows an unknown error into a defined reason.
* Rust: `rust/tests/parity_digest_mapping_dump.rs`; Go: `go/parity_digest_mapping_dump_test.go`; Python: the reference `tools/p5/digest_mapping.py` driven by the compare tool itself. Rust and Go read the input path from `PARITY_DIGEST_MAPPING_IN` and write to `PARITY_DIGEST_MAPPING_OUT`; **without the variable they do nothing**, as the existing parity dumps do, so adding them changes nothing the present CI runs.
* Dumps read the **record or input as JSON**, mapped through each language's own Node model. The raw YAML class (plan section 4) is outside Unit 2 and gets its own contract.

## 4. The comparison

`tools/parity/compare_digest_mapping.py` takes the input file and the three dumps and gives two verdicts per case:

1. **agreement**: the three answers are identical (value, or reason **and** field);
2. **construction**: the common answer equals the expectation in the input (`expect`, or `refuse` and `field`). A case whose expectation has `field: null` for a refusal is compared on the reason only, and the report counts such cases.

Either verdict alone is not enough; a case is green only when both hold. The report states, per case class, how many cases were green and, for every red case, its id, the three answers and which of them disagrees with the construction.

## 5. Fail-closed rules

Each of these makes the whole run **red**, not a warning, and none can be waived by a flag:

* a dump has a line whose `id` is not in the input (**unknown id**);
* an input id has no line in a dump (**missing**), including when it is missing from all three (comparing the dumps among themselves cannot see that; the input is the reference);
* an id appears twice in a dump or twice in the input (**duplicate**);
* the order or the number of lines differs from the input;
* a dump line is not valid JSON, lacks `id`/`ok`, or has a value or reason outside the contract's vocabulary (the 11 reasons, the digest grammar);
* the input file's digest is not the one the runner computed, or the regenerated set differs from the file;
* a class has fewer cases than its stated floor (a **thin run**; floors are those asserted by `test_gen_digest_mapping.py` and are part of the report);
* a known difference (section 6) no longer occurs, or occurs with another answer.
* any of the three implementations is not present, did not run, or its source digest could not be taken.

## 6. Known differences

A deliberate, understood difference is allowed only as an entry in `tools/parity/known_differences_digest_mapping.json`: the input class, the exact answers per implementation, and the reason. Each run counts and lists the entries; the file's SHA-256 is in the report. Unit 2 works on JSON input, so the first anticipated entry (PyYAML takes the last of two equal keys, the Forms parsers refuse) belongs to the raw class and is **not** in Unit 2's list. If Unit 2 starts with an empty list it must say so in the report ("0 known differences"), and an entry added later is a reviewed change to that file.

## 7. The proof can fail (selftest)

`tools/parity/selftest_digest_mapping.py` is part of Unit 2, and "complete" is not claimable before it is green:

* **comparison breakage**, each of which must turn the run red: a dropped line, a duplicated line, a reordered line, a swapped answer between two cases, a case missing from all three dumps, a different seed in one dump, an unknown id, a truncated file, a wrong input digest.
* **planted defects per implementation**, made in a copy of the source (never in the repository tree): the list of the local mutation checks already made for the Go and Rust code (shape by prefix match, canonical spelling not required, `3.0` read as `3`, provenance or pin not cross-checked, duplicate path accepted, the field order reversed, `null` judged as a type error), as a **committed list** with the case ids or classes each must turn red. For each: RED on the mutated copy, GREEN on the unmodified copy, both recorded. A defect that stays green is a finding about the case set.
* **the same defect in all three at once** must be red as well; that is the construction verdict's job and the only protection against a common-mode error.
* a defect that cannot change any answer is listed as **equivalent** with the reason, not silently dropped.

## 8. The report (reproducible evidence)

One JSON document, written by the runner; if any field cannot be filled the run is red.

* the repository commit and whether the tree is clean (a dirty tree is stated, and a dirty tree cannot produce a report marked as evidence);
* for each of Python, Go and Rust: the path and SHA-256 of the implementation source file, and of its dump file;
* the SHA-256 of the input file, the generator's SHA-256, seed, parameters, counts per class;
* the SHA-256 of the known-differences file and of the selftest list;
* versions of `python3`, `go`, `rustc`, `cargo`, and the command lines used;
* counts: cases, agreed, green, red, known differences, and the ids of every red case;
* the selftest result and, for each planted defect, the RED then GREEN record.

The report is shaped for `docs/TEST-EVIDENCE.md` (producing commit and named input digests), but making it an evidence record is that machinery owner's step.

## 9. Where it runs

By **one local command** (target to be named in the implementation PR) that builds the input, runs the three, compares, runs the selftest and writes the report. **No file under `.github/workflows` is changed by Unit 2.** Wiring it into CI is a separate owner decision; until then its result is the report of a local run, to be treated as such, never as a CI statement.

## 10. What counts as Unit 2 done

Done means all of: the two dumps and the compare exist; every fail-closed rule of section 5 has a selftest case that turns red; every planted defect of section 7 was shown RED and then GREEN; the report is produced from a clean tree with all fields; and the implementation PR has passed its own review. Until then nothing is claimed about the agreement of the three implementations beyond the 83 shared cases and the targeted tests already merged.

## 11. Not claimed

This document proves nothing about the implementations. It does not cover the raw YAML class, the parser comparison, the authenticity of a release, the trust anchor or the time of a signature (`docs/P5-TRUST-MODEL-PLAN.md`), or the production receipt API.
