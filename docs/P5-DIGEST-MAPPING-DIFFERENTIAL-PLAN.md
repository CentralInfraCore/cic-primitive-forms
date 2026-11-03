# P5-3: digest mapping, the differential proof of the three implementations (plan)

Status: **plan**, 2026-11-03. Design only. It adds no harness, no generator, no test, no workflow change and no production receipt API; the harness is a separate unit and gets its own review. It follows `docs/P5-DIGEST-MAPPING-CONTRACT.md` (#150) and its two implementations (`go/digestmapping.go` #151, `rust/src/digest_mapping.rs` #152, the parser boundary #153) and the Python reference `tools/p5/digest_mapping.py`.

**The question.** The 83 shared cases show that three readers can give the right answer to cases that someone wrote down. They do not show that the three give the **same** answer to an input nobody wrote down, that they refuse a wrong input by the same rule and name the same field, or that the proof would notice if one of them were wrong. This plan defines the proof that does, and what it has to demonstrate before it is believed.

## 1. Acceptance conditions

Taken from the review of 2026-11-03; each is a thing the harness has to show, not a promise.

| # | Condition | Shown by |
|---|---|---|
| A1 | the 83 original cases give the same answer in all three implementations | section 3, the shared class |
| A2 | in every generated case the three give the same value or the same defined refusal (reason **and** field) | sections 3 and 4 |
| A3 | no difference is accepted as a warning; a known, deliberate difference is listed, counted on every run, and **red if it changes** | section 5 |
| A4 | every check is traceable to the source commit of each implementation and to the digest of the case set | section 7 |
| A5 | the proof turns red when an implementation is wrong, and that is demonstrated, not claimed | section 6 |

## 2. What already exists, and is reused

The repository already has the pattern, and the plan follows it rather than inventing a second one:

* **dump**: `rust/tests/parity_*_dump.rs` and `go/parity_*_dump_test.go` read a JSON-lines file named by an environment variable and write one result line per input; without the variable they do nothing.
* **compare**: `tools/parity/compare_*.py` compares the two dumps with each other **and** with a reference computed in Python. Its principles (header of `compare_receipt_fuzz.py`) are the ones this plan needs: the two rounds must have drawn the same inputs; a record missing from **both** languages is as red as one missing from one (comparing two dumps cannot see it); a known difference is **counted and listed on every run, not normalized away**.
* **selftest**: `tools/parity/selftest_*.py` breaks the dumps in the ways that must turn the comparison red ("a gate that cannot fail proves nothing").
* **evidence**: `docs/TEST-EVIDENCE.md` binds a result to the commit that produced it and to named input digests.

The mapping gets its own dump, compare and selftest in the same three-part form, `parity_digest_mapping_dump` in each language, `tools/parity/compare_digest_mapping.py`, `tools/parity/selftest_digest_mapping.py`, and a generator `tools/parity/gen_digest_mapping.py`.

## 3. The inputs, and what makes the expected answer independent

Three classes, one JSON-lines file per run:

1. **shared**: the 83 cases of `testdata/p5/digest-mapping.json`, unchanged.
2. **generated**: deterministic, seeded, from a **defect catalogue** (section 4).
3. **raw**: YAML **bytes** of a record, parsed by each language's own parser and then mapped. This is the class the review asks for: the parser behaviour compared, not only the mapping.

**Common-mode failure is the real risk**: three implementations written from one contract can be wrong in the same way, and a comparison of them only shows that they agree. Two things keep the expected answer independent of all three:

* the expected answer of a generated case is fixed **by construction** from the contract's table (one named defect applied to a known-good record has the reason that the contract's order of checks gives it), written in the catalogue by hand and not computed by any of the three;
* the values (the hex of a base64 digest) are written from raw bytes, as the shared file does, and the real ones are checked against `openssl base64 -d | od` and `corpus/pins.yaml`, as the existing test does.

The comparison then has two independent verdicts per case: the three **agree with each other**, and they **agree with the construction**. Either alone is not enough.

## 4. The generated cases

* **Defect catalogue**: every refusal of the contract (section 5 of it) applied to the good record at each place it can apply, one defect at a time, and **pairs** of defects whose order the contract fixes (the earlier check must win, in all three).
* **Base64 shapes around the canonical form**: for seeded random 32-byte values, every single-character substitution over the alphabet, the padding, whitespace, `-`, `_`, NUL and a non-ASCII character; every position (the first and the last included); length 0 to 90; the non-canonical trailing-bit variants of each value. The expected verdict is the contract's rule (43 standard characters and one padding, 32 bytes, the one canonical spelling), written independently of the implementations as a small table.
* **Types**: every member the mapping reads, replaced by each kind of value (null, boolean, integer, a float spelling, string, list, mapping, an empty one of each).
* **Duplicates and absences**: each key absent, null, repeated; each list element absent, null, not a mapping; each artifact path absent, repeated, spelled differently.
* **Pins**: absent, one bit different, with the prefix.
* **Raw class**: the record as YAML bytes with the defects that exist only as text: a duplicate key, a BOM, a tab, CRLF, an anchor, an alias, a tag, a float where an integer is expected, a quoted number, an integer beyond 64 bits, a second document.
* Size: the seed and the count are parameters and part of the report (section 7); a run states how many cases it drew and from which classes, and a **thin run is red** (a class with fewer cases than a stated floor).

## 5. Differences: nothing becomes a warning

* Any difference between the three on a case is a **failure**, and the report names the case, the three answers and which of them disagrees with the construction.
* A deliberate, understood difference is allowed only as an entry of a **known-differences file** with the reason, the exact input class and the exact answers. Each run counts and lists them; an entry that no longer occurs is **red** (the difference closed, the entry has to go), and so is an occurrence with a different answer.
* The first entry is already known from the measured work and has to be written down as such, not found later: **PyYAML takes the last of two equal keys without an error**, while both Forms parsers refuse it (`DUPLICATE_KEY`). The Python side of the raw class therefore does not use PyYAML as its reader; it uses it only as the stated **verifying-side oracle**, and the case is the proof that the two readers differ and that the Forms parsers fail closed. This is the difference the production API (bytes in, parsed by the same rules as the authentication) has to remove.
* Differences found between the Rust and the Go **parsers** on the raw class are not to be fixed inside this unit. They are listed, and each becomes a finding with an issue; the quarantine of `compare_receipt_fuzz.py` is the precedent.

## 6. The proof can fail

* **Planted defects per language**, in copies of the implementation, never in the repository: the catalogue of the local mutation checks already made for the Go and the Rust code (shape not checked, canonical spelling not required, `3.0` read as `3`, provenance or pin not cross-checked, a duplicate path accepted, a prefix match on the digest, the length guard, `null` judged as a type error, the order of the fields) becomes a **committed list** with the case ids each must turn red. The Python reference gets the same.
* **RED then FIX, reproducible**: for each planted defect the selftest runs the comparison (RED, with the named cases), then on the unmodified copy (GREEN), and records both. A defect that stays green is a finding about the cases, not about the defect.
* **A defect made in all three the same way** (common mode) must also be shown red: the construction verdict of section 3 is what catches it, and the selftest plants the **same** defect in all three at once and requires red.
* **The comparison itself is broken on purpose** the way `selftest_*.py` does (a dropped line, a swapped answer, a case missing from both dumps, an input that was not drawn in one language) and has to turn red.
* The base64 canonical rule is checked against the **standard libraries of Python and Go** as an extra reader in the generated class, to keep the measured disagreement between them (non-canonical spelling, a final newline) visible and tied to the rule that rejects it; this is information about the decoders, not an oracle.

## 7. Traceability

The report is one JSON document, and the run is red if any of it is missing:

* the commit of the repository, and for each of the three implementations the path and the SHA-256 of its source file;
* the SHA-256 of the shared case file, of the generated case file, the seed, the counts per class, and the SHA-256 of the known-differences file;
* the versions of `rustc`, `go` and `python3`, and of the tools the values were checked with;
* the three answers per case are not kept (the files are), but the counts of agreement, of listed differences and of red cases are, with the ids of every red one.

It is shaped to be taken by the evidence machinery (`docs/TEST-EVIDENCE.md`: the producing commit and the named input digests), but making it an evidence record is that machinery's owner's step, not this plan's.

## 8. Where it runs

The CI pipeline is frozen and its wiring is the owner's. So the harness is written to run **locally by one command** and produce the report, and **nothing in `.github/workflows` changes** with the harness unit. Whether, where and how often it runs in CI, and whether the report is kept as an artifact, is a separate decision (D3 below). The dump tests are inert without their environment variable, as the existing parity dumps are, so adding them does not change what the present CI runs.

## 9. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | The raw class: compare the **parsers** too, or only the mapping on the same Node | both; the parser comparison is where the duplicate-key and number-spelling questions live |
| D2 | PyYAML as a stated verifying-side oracle in the raw class | yes, as a listed known difference, never as a reader of the proof |
| D3 | Where the run lives: local command only, or a CI step | local command first; the CI wiring as its own change, by the owner |
| D4 | Size of the generated set and the floors per class | a few tens of thousands of cases, seeded; the floors written in the report |
| D5 | Whether a Rust or Go parser difference found by the raw class blocks the unit or becomes a quarantined, listed finding | quarantined and listed with an issue, as the receipt fuzz did |

## 10. Order of the work, after the decisions

1. the generator and the catalogue (with the construction table), reviewed on its own; 2. the two dumps and the compare against the reference; 3. the planted-defect selftest; 4. the raw class and the known-differences file; 5. the report and, if wanted, the CI wiring. Each is a small change with its own review. None starts before D1 to D5 are decided.

## 11. Not claimed

No harness exists. Nothing here shows that the three implementations agree beyond the 83 shared cases and the targeted tests already merged. The proof does not decide the authenticity of a release, the trust anchor, or the time of a signature (`docs/P5-TRUST-MODEL-PLAN.md`), and it is not the production receipt API, which stays on hold and has to take the **bytes** of the authenticated record.
