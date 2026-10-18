#!/bin/sh
# Run both samples under both test sets with the candidate tools and write the trimmed outputs to $1 (default out/).
# Needs, on PATH: a nightly cargo with llvm-tools-preview, cargo-llvm-cov, go and gobco.
set -eu
cd "$(dirname "$0")"
OUT=${1:-out}
mkdir -p "$OUT"
for mode in full reduced; do
  feat=""
  [ "$mode" = reduced ] && feat="--features reduced"
  (cd rust && cargo llvm-cov clean --workspace >/dev/null 2>&1 \
    && RUSTFLAGS="-Zcoverage-options=condition" cargo llvm-cov $feat --json --output-path "../$OUT/rust-$mode.raw.json" >/dev/null)
  python3 trim_rust.py "$OUT/rust-$mode.raw.json" "$OUT/rust-$mode.json"
  rm "$OUT/rust-$mode.raw.json"
done
(cd go && gobco -stats "../$OUT/gobco-full.json" . >/dev/null && COV_REDUCED=1 gobco -stats "../$OUT/gobco-reduced.json" . >/dev/null)
