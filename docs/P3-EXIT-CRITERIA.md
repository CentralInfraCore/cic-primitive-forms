# P3 exit-criteria check

Status: **check, not a closure.** All four implementation units are merged. This document compares what is merged with the exit
criterion of `docs/PHASES.md` and with the contract of `docs/P3-NORMALIZE-DESIGN.md`, and lists the gaps it found. **P3 is not marked
done here**: closing it, and deciding what to do with each gap, is the owner's call.

Exit criterion (`PHASES.md`): *the same input and schema give the same fully resolved result in both languages.*

## 1. The four units

| Unit | Content | PR | Merge commit | Post-merge on that commit |
|---|---|---|---|---|
| 1 | `policy_surface` and `identity.base` read by the compiled schema | #44 | `8be61b6` | 25 checks, 23 passing, 0 failing, 0 missing |
| 2 | Role (3 axes), schema defaults (ADR 0011), `ApplyDefaults` | #45 | `7f376a7` | 25 checks, 23 passing, 0 failing, 0 missing |
| 3 | `normalize` / `renormalize` / `denormalize`, origin carried as a side map | #49 | `3b76ed4` | 25 checks, 23 passing, 0 failing, 0 missing |
| 4 | `PolicyResolution` (`resolve_rule`, `resolve_policy`) | #53 | `30ceb18` | 25 checks, 23 passing, 0 failing, 0 missing |

Each unit has Rust and Go code and shared cases under `testdata/p1/`, and is under the branch coverage and mutation gates (the same
gates as P1/P2, no relaxation). Fuzz targets with a canary exist for defaults (unit 2), normalize (unit 3) and resolve (unit 4); unit 1
(reading the schema) has none. The pin that makes the resolution reference digest-bound is #52.

## 2. Rust-Go parity

| Evidence | What it shows | What it does not show |
|---|---|---|
| Shared case files (`policy`, `defaults`, `normalize`, `resolution`), run by both languages against the same expected values | on those inputs both give the expected result, hence the same | inputs nobody wrote down |
| The 86 golden vectors of `access-vectors/resolution.yaml`, read from the pinned checkout after the SHA-256 matched, run in both languages | the resolver agrees with the reference on every vector, in both languages | the Forms mapping (objects, carriers) |
| Properties and fuzz targets in both languages | the same invariants hold on generated inputs, in each language | that both languages give equal outputs on the same generated input |
| `tools/parity` (parser differential) | parse-level equality on generated inputs | normalize and resolution (it covers P1 only) |

## 3. The `NormalizationResult` contract (`P3-NORMALIZE-DESIGN.md` section 2)

| Contract point | State |
|---|---|
| `normalized_object`: the tree of positions, values untouched where authored | met (`object`) |
| `value_origin`: `authored` or `default`, never `inherited` | met, as a **side map** keyed by logical path (`value_origins`), not as a field of each position; the choice and its reason are in `P3-UNIT3-COMPOSITION.md` section 4 |
| `policy`: the six Access fields as written, explicit ones untouched | met (property: an explicit setting is never lost; the symbolic policy of an inserted position stays) |
| `provenance`: the P2 entry carried through, none for an inserted position | met |
| `policy_resolution` keyed by logical path, separate from the value tree, `{rule, layer, source_path}` for `access` and `modify` | met as a separate structure (`PolicyResolution`), with the kind, patterns and reason added |
| idempotent; never changes an authored value or an explicit setting | met (on the pair object and origins) |
| "one `NormalizationResult` produced by one deterministic operation, bound to that document and that schema" | **see gap G1** |

## 4. Gaps found

Nothing below is a failing test. Each is a place where the merged code or its evidence is narrower than the words of the design.

- **G1, no single bound result.** The design says one operation produces a result that includes the resolution and is bound to the
  document and schema. In the code, `normalize` returns the object, provenance and origins, and `resolve_policy` is a second call that
  takes the **object** (not the `NormalizationResult`) and the schema. Nothing checks that the object came from that schema or that
  result, beyond refusing undeclared fields and positions that are not expanded. Options: accept as designed (two calls) and amend the
  design text; or add a thin `normalize_with_policy` that returns both and carries the binding.
- **G2, order-freedom is tested for list elements only.** The design (`P3-NORMALIZE-DESIGN.md` section 7, `P3-UNIT4-POLICY-RESOLUTION.md`
  section 5) promises that reordering list elements **or mapping entries** changes no result. The tests reorder list elements for
  `normalize` and for the resolution. No test reorders the entries of a mapping for either. The code walks entries independently and
  keys everything by path, so this is expected to hold; it is not proved.
- **G3, parity is by shared expectations, not by a differential run.** The exit criterion is about equal outputs for the same input.
  Today equality follows from both languages matching the same written expectations (19 resolution cases, the normalize and defaults
  cases, 86 vectors). There is no run that feeds generated inputs to both languages and compares the outputs, as `tools/parity` does for
  the parser.
- **G4, the corpus kind.** `P3-NORMALIZE-DESIGN.md` section 7 plans a corpus kind `normalize` in ADR 0005 notation. The shared cases
  are JSON files under `testdata/p1/`, outside the `corpus/` accounting. Not a defect, but the design text and the layout differ.
- **G5, a promised property is shown by cases, not as a property.** "Local rules are never cut" (resolution design section 5) is
  covered by two shared cases and the vectors; there is no generated-graph property for it, unlike "unknown is never permissive".
- **G6, stale status text.** The `PHASES.md` P3 row said the unit 4 code was in review. This PR corrects it.

## 5. Process note (not a P3 criterion)

Forms #53 was merged on the owner's explicit chat instruction, run by the owner with `gh pr merge --match-head-commit`, after the whole
required set (23 checks) was green on the merged head. No approval (review or `merge-approved` comment) was recorded on GitHub, and the
reviewer had not yet given a merge GO. The session thread log records this unchanged
(`theads/2026-10-21_session-forms-p1-p3-ci-gate.md` in cic-factory).

## 6. Proposal

Do not close P3 yet. Closing needs the owner's decision on G1 (accept or add `normalize_with_policy`) and on whether G2, G3 and G5
must be closed first or may be recorded as known limits. G2 and G5 are small test additions in both languages; G3 is a new tool.
