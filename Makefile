.PHONY: corpus
# Structural corpus check plus the seeded-fault self-test (ADR 0005).
corpus:
	python3 tools/corpus_check.py
