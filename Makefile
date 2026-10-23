.PHONY: corpus
# Structural corpus check plus the seeded-fault self-test (ADR 0005); the edit suite must be what its authoring tool writes (ADR 0012).
corpus:
	python3 tools/edit-vectors/author.py --check
	python3 tools/corpus_check.py

.PHONY: coverage
# Proof that the branch coverage gate fails when it must (ADR 0009). Measures no code yet.
coverage:
	python3 tools/coverage_gate.py selftest

.PHONY: grammar-selftest grammar-verify
# Proof that the grammar identity verifier can fail (ADR 0002). Needs no network.
grammar-selftest:
	python3 tools/grammar_identity.py selftest

# Reproduce the declared grammar identity from a checkout of cic-primitives at the pinned
# commit: make grammar-verify UPSTREAM=path/to/cic-primitives (CI does this on every run).
grammar-verify:
	python3 tools/grammar_identity.py verify --pins corpus/pins.yaml --repo $(UPSTREAM)

.PHONY: quick
# The cheap local checks while writing (docs/DELTA-DIFF-GATE.md, section 9): the seeded-defect texts still match the code, and the working
# tree's own mutants, targeted. CI is the authority for everything else. Optional: TESTS=go-regex RUST_TESTS=name JOBS=n
quick:
	tools/quick-check.sh
