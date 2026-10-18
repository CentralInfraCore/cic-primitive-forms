.PHONY: corpus
# Structural corpus check plus the seeded-fault self-test (ADR 0005).
corpus:
	python3 tools/corpus_check.py

.PHONY: coverage
# Proof that the branch coverage gate fails when it must (ADR 0009). Measures no code yet.
coverage:
	python3 tools/coverage_gate.py selftest
