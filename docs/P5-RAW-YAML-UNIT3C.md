# P5-3: raw YAML and the parser boundary, Unit 3c (selftest and the evidence rule)

Status: **implemented in this change**, 2026-11-05, local only. It follows `docs/P5-RAW-YAML-UNIT3-CONTRACT.md` (section 7) on the dump and compare of Unit 3b (#160). It shows that the differential **can fail**: planted defects in the Rust and Go parsers, the same defect in both, a changed Python oracle, a broken comparison and a broken harness. No workflow file changes; D9 and the production receipt API stay on hold. `P_effective` remains an analysis result, not a production admission.

## What runs

```
python3 tools/parity/run_raw_yaml.py --selftest [--out DIR]     # or: python3 tools/parity/selftest_raw_yaml.py
```

It does the run of Unit 3b and then `tools/parity/selftest_raw_yaml.py`. The measured run has one finding that is red on purpose (F1, #161: the Go parser is quadratic on a long flow sequence). The selftest therefore sets aside the cases that are red in the unmodified run **and refuses to run if the unmodified run is red on anything that is not an accepted finding** (`ACCEPTED_FINDINGS`: only `limit-over-nodes-00001`); with those cases removed the unmodified run must be green, so that a mutant is red or it is not.

* **A. The comparison is broken on purpose** (53 checks): a dropped, duplicated, reordered, swapped, unknown, truncated and unfinished line, a complete dump without its final newline, a line that is not JSON, a word outside the vocabulary, a `CRASH` and a `TIMEOUT` of **each** of the three readers, an empty dump, a case missing from all three, another input set in one dump, an input line changed in one reader's copy (the Rust reader is run on other bytes), a duplicated id in the input, a vanished known difference, a known difference with another answer, without its acceptance or with an id that is not in the input, a changed pinned subtype, pinned original answers that changed, a thin class, the evidence rule, and the baseline guard.
* **B. Planted defects, one parser at a time**, in a copy of the Rust crate and of the Go module (never in the tree); RED on the mutant, GREEN again on the restored copy: a duplicate key accepted, a tab accepted, a second document accepted, a merge key followed, a byte order mark inside the text accepted, a float or radix number read as a string, a leading zero accepted, a control character accepted (Rust), a block scalar's trailing newline dropped, the depth limit gone, the size limit gone, a line separator accepted.
* **C. The same defect in Rust and Go together**: both parsers then agree and are both wrong; the run is still red, through the `allowed` label (a forbidden construct in `P_parity`) or the constructed tree, never through parity: a duplicate key, a tab, a merge key, a float, a BOM and a block scalar's trailing newline.
* **D. The Python oracle changed**: one that refuses a duplicate key (the 67 known differences stop occurring) and one that reads the envelope as a string (a blocking difference on a read member); both red.
* **E. The harness itself**: a reader that answers the same thing for every case, one that echoes the expectation, one that never runs; all red. The echo reader is caught **only** through the pinned divergences, which cannot be echoed (a pinned divergence that agrees is `pinned_no_longer_diverges`): a harness whose pinned list were empty would not catch it.
* **Equivalent changes**, kept green and explained: the Go control-character pre-scan is redundant for every control-character case of the input (yaml.v3 refuses them itself).

Two layers refuse the same construct in some cases, so a single edit stays green and both must go for the defect to get through: a second document is refused by the pre-scan (a marker line) and by the decoder, so the planted defect removes both (`EXTRA` in the selftest). A tab used as indentation is refused by the YAML library of both parsers; only a tab after a value needs the pre-scan, and that is the case the defect is judged on. Not planted, on purpose: CRLF, a lone CR, a leading BOM, `yes`/`no`/`on`/`off` and the other undetermined constructions are not judged by construction (decision D3), so a defect there is by definition not red; the depth limit is judged by a clear overrun (the exact boundary is undetermined).

## Measured (local; go 1.25.0, rustc 1.99.1, python 3.12.3, PyYAML 6.0.1; the 2223 cases of Unit 3b, 2222 after the accepted finding is set aside)

**Selftest passed**, in one clean full run: the unmodified baseline green; **53/53** comparison breakages red; **23/23** planted defects (11 Go, 12 Rust) RED on the mutant and GREEN again on the restored copy, each on the classes it names; **6/6** common-mode defects red through the `allowed` label or the constructed tree (67 duplicate-key cases among them were agreed by both parsers and still red); **2/2** oracle changes red; **3/3** harness checks red; the 1 equivalent change green. The run itself stays red on F1, so `evidence: false`.

## What the selftest found about the harness itself

An earlier run of this selftest gave the **real** Rust reader of the main run a build of a **mutated copy**: the mutants and the run under test shared one cargo target directory, cargo's output name does not depend on the path of the workspace member, and the sources of the real tree were older than the artifact, so cargo took the mutant for a fresh build. The main run then had four red `value-block-digest` cases (Rust and Go disagreed on a block scalar's trailing newline) that it did not have before. It was seen because of the baseline guard, not by luck: the guard now stops the selftest when the unmodified run is red on anything but the accepted finding, the mutants are built in a target directory of their own, and the report carries the SHA-256 of each reader binary. This is why "the readers are built from the tree they are said to be built from" is part of what the evidence rule relies on.

## The evidence rule

`evidence` is true only for a clean tree, a green compare **and** a selftest that ran and passed (`run_raw_yaml.evidence_of`, tested in the selftest). While F1 is open the compare is red, so **the report is not evidence**, whatever the selftest says. The selftest result is recorded apart (`selftest`, with the SHA-256 of `selftest_raw_yaml.py` as the identifier of the definitions of the planted defects and checks).

## Not claimed

That the Rust and Go parsers are right: the selftest shows the differential can see the defects it plants, not that no other defect exists; the cases of the undetermined list and the 32 unrepresentable Unit 1 cases are not judged. Not D9. The pinned divergences and F1 are not closed. The production receipt API stays on hold.
