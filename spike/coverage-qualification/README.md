# Branch coverage tool qualification (spike, ADR 0009)

**Isolated and temporary. This is not the Primitive Forms implementation.** The samples are not product code,
sit outside `coverage/scope.yaml`, and are meant to be archived or deleted once the owner has decided. Nothing
here changes `coverage/tools.yaml`, the coverage gate or any ADR.

Question: can the candidate tools measure the five categories of `cic-branch/1` (ADR 0009) so that an adapter
can produce a `cic-coverage-report/1`?

- `rust/`, `go/`: small samples whose branches are counted **by hand** (`golden.json`, and the table at the end
  of each source file), each with a full test set and a reduced one whose uncovered branches are also counted by hand.
- `run_tools.sh`: runs both samples under both test sets with the candidate tools.
- `evidence/`: the tool outputs of the runs (Rust trimmed to branches and regions, no absolute paths), the
  comparison with the hand counts (`comparison.md`) and the outputs of two further tool modes (`cargo llvm-cov --branch`, `gobco -branch`).
- `compare.py`, `threshold.py`: spike helpers. `threshold.py` works on a tool's own numbers and is not the gate.
- `EXIT-REPORT.md`: findings, status of the nine acceptance criteria, and the decision requested from the owner.
- `.github/workflows/coverage-spike.yml`: the pinned, reproducible run on GitHub (spike branches and on demand).
