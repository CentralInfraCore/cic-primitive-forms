# P5-3: raw YAML and the parser boundary, Unit 3a plan (generator and construction yardstick)

Status: **implemented in this change**, 2026-11-04, local only. It is the first of the three units of `docs/P5-RAW-YAML-UNIT3-CONTRACT.md` (#158): the byte-level generator whose labels are fixed by construction. It adds **no dump, no compare, no selftest of the proof, no workflow change** and no production receipt API. Nothing here is evidence about the Rust or Go parsers; they are not run.

## Parameters (contract D7)

| Parameter | Value |
|---|---|
| seed | 20261104 (`gen_raw_yaml.DEFAULT_SEED`) |
| valid records x styles | 40 x 12 (`built:*`) |
| Unit 1 record cases written as YAML | every defect and pair case, in 2 styles (`tree:*`; a float is written plain and labelled a syntax refusal: `tree-float:*`) |
| seeded pairs (one duplicate key plus one syntax defect) | 100 |
| pinned Rust/Go divergence inputs | all 299 of `testdata/p1/parity-known.txt`, each with its own normative `allowed` label (below) |
| cases | 2223 |
| coverage floors | `tools/parity/floors_raw_yaml.json`, asserted by the tests: a thin run is red, and so is a class without a floor |
| resource limits for the readers of Unit 3b | per case 10 s wall clock, 6 GiB memory, 2 MiB per case, 16 MiB for the input file (`gen_raw_yaml.LIMITS`); a crash, a hang or a blow-up is a red case, never a refusal |

## What the labels are, and what they are not

Three separate fields per case (`parse_expect`, `allowed`, `mapping_expect`), as the contract requires; the tests check that a refused text carries no mapping expectation and that an accepted one is `allowed: true`. The mapping expectations of the tree-level cases are those of the Unit 1 yardstick; the text-level labels are written by hand from `docs/P1-SUPPORTED-SUBSET.md`. A pair of a duplicate key and a syntax defect is labelled "either refusal" because the documents fix which wins only through "a syntax error anywhere wins".

**Undetermined** (`tools/parity/undetermined_raw_yaml.json`, 24 entries, committed and checked against the generator): missing final newline, CRLF, lone CR, leading BOM, NBSP, zero-width character, UTF-16, invalid and overlong UTF-8, empty document, comment-only document, the YAML 1.1 spellings `Null`, `yes`, `no`, `on`, `off`, `y`, `n` in a position the subset reads as a string, and the two exact limits (1 MiB, depth 64). They have no expected answer; they are not in `P_allowed` until the owner classifies them (contract D3, D6).

**Pinned divergence inputs and `allowed`.** The 299 inputs are required to be outside `P_parity` (and so `P_effective`); that is a fact about the two parsers. `testdata/p1/parity-known.txt` has **299 lines (occurrences) and 201 distinct byte sequences**; the generator writes one case per occurrence (299 cases, as the file counts them) and the classification is per distinct input (201). Reports must show both numbers. Their `allowed` label is a fact about the documented subset and does not depend on the parsers: `tools/parity/classify_pinned_raw_yaml.py` reads each distinct input with the documented-subset reader, and the result is committed in `tools/parity/pinned_classification_raw_yaml.json` (a test regenerates it and compares). Measured, 201 distinct inputs: **72 `true`**, **13 `false`** (a refusal the subset names: 10 directive or document-end lines, 2 duplicate keys, 1 non-scalar key), **116 `undetermined`**, each with a named reason: 79 where PyYAML alone finds the text invalid (the subset document itself lists such spellings as places where the libraries disagree on YAML 1.2), 25 where the reference reader refuses a bare scalar as the whole document (a restriction the subset document does not name), 12 demoted from `true` by a conservative trigger (6 marker-like lines, 4 indicator words, 2 empty implicit keys).

**What `true` (rule `in-table`) does and does not cover.** The reference reader recognizes only part of the refusal table of `docs/P1-SUPPORTED-SUBSET.md` ("Explicitly not supported"). `COVERAGE` in `classify_pinned_raw_yaml.py` lists every row and how it is met: by the reader (anchors, aliases, tags, merge keys, non-scalar keys, second document, float, hex, octal, leading zero, integers beyond 64 bits, the characters, tab, BOM, directive, document end, duplicate keys, the limits) or by a **conservative trigger** that can only demote a `true` to `undetermined` (a marker-like line at any indentation, a word starting with `!`, `&`, `*` or `?`, `#` right after `]` or `}`, a block scalar at the end of the text or with nothing after it, an empty implicit key). The row "text the YAML library rejects" is deliberately not applied with PyYAML (it is not the Go or Rust library the row names). The row about a scalar where a mapping is declared is schema-level and not a property of the text. So `true` means: *the reference reader accepts it and no row of the table is known to apply*; it is conditional on the table as listed, and a later row or a better detector can only move a case out of `true`. A test fails if a `true` input fires any trigger.

**Skipped** (same file, `skipped_unrepresentable`, 32 entries): Unit 1 record cases with a newline or another non-printable character inside a string. Writing them needs an escape, and whether the parsers support the escape is not documented, so no text is invented for them. This is a coverage gap, listed.

## The yardstick

`tools/parity/raw_yaml_subset_ref.py` is an executable reading of the documented table on the PyYAML event stream. It is used only by the tests of this generator (`tools/parity/test_gen_raw_yaml.py`): every judged case agrees with it, and with the Unit 2 mapping reference on the accepted trees; 14 planted defects in a copy of it each turn the named classes red; two changes that cannot alter an answer (PyYAML refuses a tab and control characters itself) stay green and say why; a reader that answers "syntax" to everything is found out. It is **not** one of the readers of the proof and **not** the subset gate of decision D9; it makes no claim about the Forms parsers.

## Local run

```
python3 -W error::ResourceWarning -m unittest tools/parity/test_gen_raw_yaml.py
python3 tools/parity/gen_raw_yaml.py --out cases.jsonl --undetermined-out /tmp/list.json --summary
```

Not wired into a workflow; where it runs is the owner's decision.

## Not claimed

The Rust and Go parsers are not run on these cases (Unit 3b). `P_parity`, `P_effective` and the findings do not exist yet. The enforcement of `P_allowed` in production (D9) is not decided. The production receipt API stays on hold.
