#!/usr/bin/env bash
# Measure the Rust graph (rust-analyzer SCIP -> lab/extractor/scipdeps) against per-test LLVM coverage of the crate, then plant
# two defects (the type -> impl method edges left out; 5% of the edges dropped) and require the measurement to see each.
# Needs cargo with llvm-tools and rust-analyzer (RUST_TOOLCHAIN), go, and the CIC_*_ROOT variables the tests read.
set -euo pipefail
here=$(cd "$(dirname "$0")/../.." && pwd)
out=$(realpath -m "${1:-lab-out}")
tc=${RUST_TOOLCHAIN:?}
mkdir -p "$out"
llvm_bin=$(dirname "$(find "$(rustc +"$tc" --print sysroot)/lib/rustlib" -name llvm-profdata -type f | head -1)")
(cd "$here/lab/extractor/scipdeps" && go build -o "$out/scipdeps" .)
(cd "$here/rust" && rustup run "$tc" rust-analyzer scip . --output "$out/rust.scip" > /dev/null)
"$out/scipdeps" "$out/rust.scip" "$here/rust" > "$out/rtyped.json"
SCIPDEPS_PLANTED_NO_IMPL=1 "$out/scipdeps" "$out/rust.scip" "$here/rust" > "$out/rtyped-no-impl.json"
(cd "$here/rust" && CARGO_TARGET_DIR="$out/rcov" RUSTFLAGS="-C instrument-coverage" LLVM_PROFILE_FILE="$out/build-%p.profraw" \
  cargo +"$tc" test --no-run --locked --message-format=json 2> /dev/null) | python3 -c '
import json, sys
for l in sys.stdin:
    m = json.loads(l)
    if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
        print(m["target"]["kind"][0], m["target"]["name"], m["executable"])' > "$out/rexes.txt"
python3 "$here/lab/measure/collect_rust.py" --graph "$out/rtyped.json" --crate "$here/rust" --exes "$out/rexes.txt" \
  --llvm-bin "$llvm_bin" --out "$out/rruns.json"
cmp() { python3 "$here/lab/measure/graph_vs_coverage.py" --runs "$out/rruns.json" "$@" > /dev/null; }
cmp --typed "$out/rtyped.json" --out "$out/rust-report.json" --expect sound
cmp --typed "$out/rtyped-no-impl.json" --out "$out/rust-planted-no-impl.json" --expect misses
cmp --typed "$out/rtyped.json" --drop-edges 0.05 --seed 1 --out "$out/rust-planted-drop.json" --expect misses
python3 - "$out" <<'PY'
import json, sys
d = sys.argv[1]
r = json.load(open(d + "/rust-report.json")); v = r["graphs"]["typed"]
print("rust typed tests with a miss %d/%d, missed pairs %d, precision mean %s" % (v["tests_with_a_miss"], r["tests"], v["missed_test_function_pairs"], v["precision_mean"]))
print("rerun share per changed function:", r["share_of_tests_rerun_per_changed_function"])
for name in ("no-impl", "drop"):
    p = json.load(open(d + "/rust-planted-%s.json" % name))["graphs"]["typed"]
    print("planted defect (%s): tests with a miss %d, e.g. %s" % (name, p["tests_with_a_miss"], p["most_missed"][:3]))
PY
