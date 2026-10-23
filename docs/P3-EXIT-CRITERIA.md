# P3 exit-criteria check

Status: **closure proposed, with the evidence below.** The first version of this document (#54) listed six gaps and did not close P3. The gaps were closed in the
closing round (#55, #56, #57, #59, #61, #64) and a full run of the whole gate on the merged `main` is green. **Merging this change is the owner's act that closes
P3**: it flips the P3 row of `docs/PHASES.md` to done. If the owner does not want to close it, this change is closed and the row stays as it is.

Exit criterion (`PHASES.md`): *the same input and schema give the same fully resolved result in both languages.*

## 1. The four units, and the closing round

| Unit | Content | PR | Merge commit | Post-merge on that commit |
|---|---|---|---|---|
| 1 | `policy_surface` and `identity.base` read by the compiled schema | #44 | `8be61b6` | 25 checks, 23 passing, 0 failing, 0 missing |
| 2 | Role (3 axes), schema defaults (ADR 0011), `ApplyDefaults` | #45 | `7f376a7` | 25 checks, 23 passing, 0 failing, 0 missing |
| 3 | `normalize` / `renormalize` / `denormalize`, origin carried as a side map | #49 | `3b76ed4` | 25 checks, 23 passing, 0 failing, 0 missing |
| 4 | `PolicyResolution` (`resolve_rule`, `resolve_policy`) | #53 | `30ceb18` | 25 checks, 23 passing, 0 failing, 0 missing |
| G1 | `normalize_with_policy`: the normalization and the resolution of its object from one compiled schema | #55 | `9a44344` | green (full set at the time) |
| G2, G5 | mapping-order freedom and "local rules are never cut" as properties | #56 | `95d3fb0` | green; one check-run stuck in progress on GitHub's side and was re-run by the owner |
| G3 | the Rust-Go differential for normalize and the resolution | #57 | `d9d24c1` | 25 checks, 23 passing, 0 failing, 0 missing |

Each unit has Rust and Go code and shared cases under `testdata/p1/`, and is under the branch coverage and mutation gates. Fuzz targets with a canary exist for defaults
(unit 2), normalize (unit 3) and resolve (unit 4); unit 1 (reading the schema) has none. The pin that makes the resolution reference digest-bound is #52.

## 2. The full run on the merged main

The nightly workflow (`nightly.yml`, #59) was run by hand by the owner on `main` at `73be2a3` (run 39855232817), after all of the units and the closing round were merged:
**25 of 25 jobs green**, including all 12 mutation shards (6 Rust, 6 Go), the fuzz targets with their canary, the seeded defects, the evidence self-test, the branch coverage, the parity
(parser and the P3 differential) and the `quality` gate. It is judged by `tools/ci-required/cic-primitive-forms.nightly.txt` of cic-factory (25/25). This is not the post-merge check of a merge
(that is the light set since #64 and does not prove the mutation); it is the evidence of the whole gate, on the merged state, in one run.

## 3. Rust-Go parity: what shows it

| Evidence | What it shows |
|---|---|
| Shared case files (`policy`, `defaults`, `normalize`, `resolution`), run by both languages against the same expected values | on those inputs both give the expected result, hence the same |
| The 86 golden vectors of `access-vectors/resolution.yaml`, read from the pinned checkout after the SHA-256 matched the pin, run in both languages | the resolver agrees with the reference on every vector, in both languages |
| Properties and fuzz targets in both languages | the same invariants hold on generated inputs, in each language |
| **The P3 differential (#57):** `tools/parity/gen_p3.py` (fixed seed) feeds 3000 generated schema/document/unknown combinations to both languages, `compare_p3.py` compares the normalized object, every value origin and the rule, layer, source path and reason of every position | the languages answer alike for the same input. Locally 2754 accepted, 246 refused at the resolution, 0 differences. With one defect put into the Rust resolver the same run reports 848 differences. A selftest breaks the outputs six ways and requires red. It runs in the required `parity` job |

The differential covers one family of schemas (the shared schema body with a drawn `policy_surface`) and documents of that shape; it does not cover arbitrary schemas. The parser parity (P1) has 291 known open
differences in documented classes (`docs/P1-SUPPORTED-SUBSET.md`, Parser parity), unchanged by P3.

## 3b. The `NormalizationResult` contract (`P3-NORMALIZE-DESIGN.md` section 2)

| Contract point | State |
|---|---|
| `normalized_object`: the tree of positions, values untouched where authored | met (`object`) |
| `value_origin`: `authored` or `default`, never `inherited` | met, as a **side map** keyed by logical path (`value_origins`); the choice and its reason are in `P3-UNIT3-COMPOSITION.md` section 4 |
| `policy`: the six Access fields as written, explicit ones untouched | met (property: an explicit setting is never lost) |
| `provenance`: the P2 entry carried through, none for an inserted position | met |
| `policy_resolution` keyed by logical path, separate from the value tree, `{rule, layer, source_path}` for `access` and `modify` | met as a separate structure (`PolicyResolution`), with the kind, patterns and reason added |
| idempotent; never changes an authored value or an explicit setting | met (on the pair object and origins) |
| "one result produced by one deterministic operation, bound to that document and that schema" | met by `normalize_with_policy` (#55): one compiled schema in, the normalization, the resolution and the digest of the schema out; no half result; the separate calls stay |

## 4. The six gaps of the first version

| Gap | State | Closed by |
|---|---|---|
| G1 no single bound result | closed | #55 |
| G2 order-freedom for list elements only | closed: the entries of every mapping, at any depth, are reordered over the 19 shared cases with many seeds; the normalization and the whole resolution do not change | #56 |
| G3 parity by shared expectations only | closed: a direct Rust-Go differential on generated inputs (section 3) | #57 |
| G4 the corpus kind | closed by documenting what was built: `P3-NORMALIZE-DESIGN.md` section 7 now says the shared cases are JSON files under `testdata/p1/` and not a `corpus/` kind | this change |
| G5 "local rules are never cut" shown by cases | closed: a generated-graph property in both languages | #56 |
| G6 stale status text | closed: the P3 row of `PHASES.md` and the README say what is merged | #54, this change |

## 5. What is not claimed

* P3 is the semantics of normalize and of the effective rule. It decides nothing about a request (the Relay does), and it does not resolve references or materialize (P5).
* The differential's input space is narrow (section 3). A defect that needs another schema shape is found only by the other tests.
* The two freshness checks of a merge are not atomic, and a green on a base that has moved is not the merge result; the final guarantee would be branch protection or a merge queue, which the repository does not have.
* The first scheduled nightly run has not happened yet; the full run above was started by hand.

## 6. Process record (not a criterion)

* #53 was merged on the owner's explicit chat instruction, run by the owner with `gh pr merge --match-head-commit`, after the whole required set (23 checks) was green on the merged head. No approval (review or `merge-approved` comment) was recorded on GitHub, and
  the reviewer had not yet given a merge GO. The session thread log records this unchanged (`theads/2026-10-21_session-forms-p1-p3-ci-gate.md` in cic-factory).
* #55, #56 and #57 were landed by `tools/land-chain.sh` on the owner's instruction, in that order, each on its exact head, each only after its CI and the post-merge of the one before it were green.
* GitHub states that stuck during the round and were not failures of the code: a `corpus` run and a `seeded-defects` check-run on the merge commit of #56 stayed in progress after their jobs had finished (the check-run was re-run by the owner), and the push run of
  `73be2a3` was cancelled when the runner queue was cleared; the nightly run above is the full run of that commit.

## 7. Proposal

Close P3. P3b has its design (`P3B-SOURCE-PRESERVING-EDIT.md`), its accepted ADR (0012) and its first vectors (E1, #68); the P3b implementation is E2 onwards.
