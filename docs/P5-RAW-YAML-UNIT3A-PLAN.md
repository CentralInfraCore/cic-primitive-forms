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

**Pinned divergence inputs and `allowed`.** The 299 inputs are required to be outside `P_parity` (and so `P_effective`); that is a fact about the two parsers. Their `allowed` label is a fact about the documented subset and does not depend on the parsers: `tools/parity/classify_pinned_raw_yaml.py` reads each input with the documented-subset reader and the result is committed in `tools/parity/pinned_classification_raw_yaml.json` (the test regenerates it and compares). Measured on the current table: **84 `true`** (parses, no documented refusal applies: a permitted text that two parsers read differently), **13 `false`** (a refusal the subset names: 10 directive or document-end lines, 2 duplicate keys, 1 non-scalar key), **102 `undetermined`** with a named reason (79 where PyYAML alone finds the text invalid, which the subset document itself lists as places where the libraries disagree on YAML 1.2; 25 where the reference reader refuses a bare scalar as the whole document, a restriction the subset document does not name).

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
