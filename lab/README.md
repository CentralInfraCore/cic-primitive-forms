# Verification lab

A long-lived branch (`lab/verification`) for trying content-addressed reuse of test evidence on this repository's own code and
history. It is not a product branch and is never merged into `main`; no pull request is opened against `main` from it.

* The gate workflows of `main` are in `lab/ci-reference/workflows/` (moved, unchanged), so their parts can be reused without them
  running here. `.github/workflows/lab.yml` is the only active workflow: it runs on a push to `lab/**`.
* Results that are worth keeping go back to `main` (or to the repository that owns the code) as ordinary pull requests.
* Tools that read `.github/workflows/*` (`tools/test-evidence/`, `tools/diff_gate.py`, `tools/inherited_tree.py`, `tools/corpus_check.py`
  and their tests) still name the old paths. They are not run here; a tool taken into the lab is pointed at its new inputs when it is taken.
