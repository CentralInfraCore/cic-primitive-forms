# What P0 left to later phases, and where each item stands

P0 (ADR 0001 to 0010) was accepted on 2026-10-18 with the open items listed in `docs/reviews/p0-acceptance-review.md` ("Open" rows) and in the
decision index of ADR 0000. This note maps each of them to the phase that took it over, and says whether it is closed. It adds no decision: every row
points at the document that holds the decision or the evidence.

How to read the state column. **closed** means a later ADR or an implemented, proved unit settled it and the pointer shows where. **open** means nothing
settled it; the cause is named. **settled by decision, not by proof** means the implementation took a position that is tested but that no accepted ADR states.
The "open" rows were taken from the P0 review and checked against the registry (`corpus/codes.yaml`) and the ADR status lines on 2026-10-27; rows about
work that was not touched since were not re-derived from the code.

## Closed since P0

| P0 item | Source | Settled in | State |
|---|---|---|---|
| Role-dependent default eligibility; what a schema default may be | ADR 0003 rule 4, review (0003) | ADR 0011 (accepted 2026-10-21); P3 unit 2 (`ApplyDefaults`, three-axis role) | closed |
| Evaluation of inheritance: the effective access and modify rule of a position | review (0004) | P3 unit 4: `resolve_rule`, `resolve_policy`, `docs/P3-UNIT4-POLICY-RESOLUTION.md`; 86 pinned reference vectors | closed in both languages |
| Normalization as one composed operation, with the origin of every value kept | review (0003, 0004) | P3 unit 3 and the composed call `normalize_with_policy` | closed |
| The grammar digest was required but no document said which bytes it covers | review (0002) | `corpus/pins.yaml` carries the manifest digest; `tools/grammar_identity.py` reproduces it in CI | closed |
| No branch coverage tool was qualified; the Go side had no candidate for all five categories | ADR 0009, review (0009) | ADR 0009 accepted with `gobranch` and `rustbranch`, qualified on hand-counted samples (`coverage/tools.yaml`) | closed |
| Diff output: byte-identical, deterministic | ADR 0000 point 5, ADR 0008 | D1-D3: `diff` in Rust and Go, 66 vectors, Rust/Go/model differential (`docs/P4-DIFF-PATCH.md` sections 10 and 11) | closed |
| Patch and its invariant ("patch is P4") | ADR 0008 | ADR 0013 and P4-2, P4-3 (first unit only) | closed for the first unit; later units open (`docs/PHASES.md`, P4) |

## Settled by decision, not by proof

| P0 item | Source | Position taken | Where it is written |
|---|---|---|---|
| Schema-dependent order of the elements of a set inside a diff fragment | ADR 0008 acceptance record | null, booleans, integers, strings, then by value; deduplicated | `docs/P4-DIFF-PATCH.md` section 10, point 7; unit test in both languages; no corpus vector |
| Payload order of list elements across `item_choice` cases | ADR 0008 acceptance record ("no vector covers it") | the order of the case declarations, then the identity | same place; unit tests in both languages; no corpus vector |

## Still open

| P0 item | Source | Why it is open | Phase it waits for |
|---|---|---|---|
| The type of the symbolic `inherit` in `access` and `modify` (`list<CertPattern>` against a union) | ADR 0000 point 8, ADR 0004 | upstream decision, cic-primitives #17; this is the one item that keeps ADR 0000 at `draft` | upstream |
| The normative difference between `false` and `0`, the `inherit_policy` boolean-against-`0` contradiction, the final enum | review (0004) | the library keeps them different and never coerces (`docs/P3-UNIT4-POLICY-RESOLUTION.md`), but the upstream definition is not decided | upstream (#17) |
| Six error codes stay `provisional`: `INVALID_ACCESS_VALUE`, `INVALID_CERT_PATTERN`, `INVALID_INHERIT` (upstream #17), `PATH_INTEGER_RANGE`, `PATH_KEY_UNSUPPORTED`, `DIFF_UNSUPPORTED_VALUE` (no numeric contract) | ADR 0007, `corpus/codes.yaml` | acceptance does not change them | upstream; a numeric contract |
| Floats and integers beyond 64 bits | ADR 0008 | a diff refuses them (`DIFF_UNSUPPORTED_VALUE`); the document model has a kind for them only so that the refusal can be reached and tested (`docs/P4-DIFF-PATCH.md` section 10, point 4) | a numeric contract |
| Key encodings beyond string and integer in a path; addressing the components of an Access value; paths through a plain `choice` | ADR 0006, review (0006) | no encoding is defined; no real schema uses a plain `choice` | a later ADR |
| Verifying an attestation, and what makes a binding trusted; the basis in the diff output; merging a materialized document back into an authored one | ADR 0010, review (0010) | the library takes the trusted context from the caller and verifies nothing | P5 and P6 |
| The engine's mapping of authored-absent maps onto `Absent` | ADR 0003, review (0003) | belongs to the engine side | P5 and P6 |
| `item_choice` exists on `devel` of cic-primitives only, not in the tag `primitives/@v0.2.0` | ADR 0002, review (0002) | ADR 0002 calls it a temporary compatibility profile | an upstream release |
| Rust and Go parse some YAML differently (a known, listed set) | ADR 0006/0007, review (0006); `testdata/p1/parity-known.txt`, `docs/P1-SUPPORTED-SUBSET.md` ("Parser parity") | each class is allow-listed and a change of any of them fails the parity job; none is claimed equivalent | either parser library, or a narrower subset |
| Wording of messages; anchors of block sequences; text positions for `path_of` | ADR 0007, review (0007) | not part of the contract | not scheduled |
| The silent `opaque` case (a short map that equals the key set of `A`); a warning channel | ADR 0001, review (0001) | an accepted cost | not scheduled |
| ADR 0000 itself | `docs/adr/0000-p0-contract.md` | it is already an index of the decisions; its status stays `draft` only for the first row of this table | the upstream decision |

## What this does not claim

The "closed" rows are closed in the sense of the evidence they point to, not proved correct in general. The "open" rows were not re-derived from the code
today; if one of them has moved, the document it points at is the authority, not this table.
