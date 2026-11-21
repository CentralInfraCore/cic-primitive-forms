# Evidence: surface-level `validation` is listed as unevaluated (issue #167)

Scope, as decided by the owner: the **explicit meaning** of the surface-level `validation` entries is `unevaluated`. Evaluating them is **not** part of this change (it needs the `must` dialect, open point P2 of cic-primitives).

Base: `main` at `2f02a755`. Grammar profile `cic-primitives-73ae469-6a188078`, unchanged.

## The change

`CompiledSchema` gets one member, `unevaluated_surface_contracts` (Rust) / `UnevaluatedSurfaceContracts` (Go): the canonical paths of the declared surface-level validation entries, `<surface>.validation[<i>]`, or `<surface>.validation` when the member is present but is not a list. Read as a node contract is read: **presence counts, no entry is inspected**. `unevaluated_contracts` is untouched and keeps meaning the nodes (a consumer, and the cic-primitives conformance declaration, count 17 and 13 listed nodes there). No refusal is added: a schema that compiled before compiles now, with the same result plus the new list.

Decision recorded: option 2 of the issue (pass through and list as unevaluated), as a separate list rather than appended to the node list (appending would change the meaning and the count of an existing member).

## Evidence

| File | What it shows |
|---|---|
| `gen_cases.py` | adds 8 shared vectors `surface-validation.*` to `testdata/p1/schema.json`: absent, empty list, one entry, two entries (with a document that breaks the declared `must` and is still accepted), scalar member, null member, mapping member, entries that are not contracts (not inspected) |
| `probe-go-output.txt`, `probe-rust-output.txt`, `probe.go.txt`, `probe.rs.txt`, `go.mod.txt` | the real composed `PrimitiveRelease` and `ModuleRelease` schemas of cic-primitives #38 (same four cases as `release-schema-draft-probe`) compiled by the changed Form: 1 and 3 surface entries listed (`K01`: `config_surface.validation[0]`; `K02`: `[0]`, `[1]`, `[2]`); Go output and Rust output are **byte-identical** |
| (comparison) | with the new member removed, both outputs are **identical** to the pre-change `go-output.txt` and `rust-output.txt` of cic-primitives (`release-schema-draft-probe`): the node lists (17, 13, 13, 17) and every validate line are unchanged, and the documents that break a surface `must` are still accepted (not evaluated) |
| `seeded_faults.py`, `seeded-faults-output.txt` | four planted defects, each in Go and in Rust (8 runs): nothing listed, index off by one, a non-list member not listed, entries merged into the node list (Go) / dropped (Rust). Each turns the shared-vector test of that language red on a new vector; the baseline is green; the tree is restored after each run |

## Parity and regression protection

* Both languages read the same shared vector file (`testdata/p1/schema.json`) with the same assertion on the new member; the existing 78 cases (and the real-schema cases) expect an empty surface list implicitly (an absent key means empty in both runners), so a regression that starts listing something where nothing is declared is also red.
* The whole Go suite and the whole Rust suite pass with the pinned cic-primitives (`73ae469`, grammar identity PASS) and cic-schema-registry (`6db77d4d`, sha256 matches) checkouts. `make corpus` passes (structural only).

## Not shown

* No evaluation of any validation entry, and no statement about the validity of an expression (the dialect is not specified).
* The CI workflows (p1, nightly FULL, mutation gates) were **not** run locally; the delta/mutation gate for the new lines is for CI.
* Other surfaces than the selected one are not read (the compiler reads one surface per call, as before).
