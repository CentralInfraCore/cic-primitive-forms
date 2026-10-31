#!/usr/bin/env bash
# Data edges: record under strace which files every Go and Rust test reads, then check the record by corrupting data files
# (a test that starts failing must have read the file) and plant a defect (one path forgotten from every record: must be seen).
# Runs in the checkout itself: the corrupted files are restored after each run. Needs go, cargo (RUST_TOOLCHAIN), rust-analyzer,
# strace and the CIC_*_ROOT variables; CIC_FORMS_ROOT must be the checkout.
set -euo pipefail
here=$(cd "$(dirname "$0")/../.." && pwd)
out=$(realpath -m "${1:-lab-out}")
tc=${RUST_TOOLCHAIN:?}
mkdir -p "$out/tmp"
export TMPDIR="$out/tmp" GOCOVERDIR="$out/tmp" LLVM_PROFILE_FILE="$out/tmp/%p.profraw"
(cd "$here/lab/extractor/typeddeps" && go build -o "$out/typeddeps" .)
(cd "$here/lab/extractor/scipdeps" && go build -o "$out/scipdeps" .)
"$out/typeddeps" "$here/go" > "$out/typed.json"
(cd "$here/rust" && rustup run "$tc" rust-analyzer scip . --output "$out/rust.scip" > /dev/null)
"$out/scipdeps" "$out/rust.scip" "$here/rust" > "$out/rtyped.json"
(cd "$here/go" && go test -c -o "$out/forms.test" .)
(cd "$here/rust" && CARGO_TARGET_DIR="$out/target" cargo +"$tc" test --no-run --locked --message-format=json 2> /dev/null) | python3 -c '
import json, sys
for l in sys.stdin:
    m = json.loads(l)
    if m.get("reason") == "compiler-artifact" and m.get("executable") and m["profile"]["test"]:
        print(m["target"]["kind"][0], m["target"]["name"], m["executable"])' > "$out/rexes.txt"
python3 "$here/lab/measure/trace_data.py" --repo "$here" --go-bin "$out/forms.test" --go-graph "$out/typed.json" \
  --rust-exes "$out/rexes.txt" --rust-graph "$out/rtyped.json" --out "$out/data.json"
sound() { python3 "$here/lab/measure/data_soundness.py" --wt "$here" --data "$out/data.json" --go-bin "$out/forms.test" \
  --rust-exes "$out/rexes.txt" "$@" 2> /dev/null; }
sound --sample 30 --seed 1 --include testdata/p1/parity-known.txt --include testdata/p5/engine-pin.txt --include corpus/pins.yaml \
  --out "$out/data-soundness.json"
sound --sample 0 --planted-forget corpus/patch/vectors.yaml --out "$out/data-planted.json"
git -C "$here" diff --quiet || { echo "a corrupted file was not restored"; git -C "$here" status --short; exit 1; }
