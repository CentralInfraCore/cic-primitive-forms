#!/usr/bin/env bash
# Measure the typed graph (lab/extractor/typeddeps) and the knowledge base's graph (go.meta.gen.py) against per-test coverage of
# the Go module, then plant a defect (5% of the typed graph's edges dropped) and require the measurement to see it.
# Needs go on PATH and the CIC_*_ROOT variables the Go tests read. Writes the reports into $1 (default: lab-out).
set -euo pipefail
here=$(cd "$(dirname "$0")/../.." && pwd)
out=$(realpath -m "${1:-lab-out}")
mkdir -p "$out/meta"
(cd "$here/lab/extractor/typeddeps" && go build -o "$out/typeddeps" .)
"$out/typeddeps" "$here/go" > "$out/typed.json"
(cd "$here/go" && go test -c -cover -covermode=set -coverpkg=github.com/CentralInfraCore/cic-primitive-forms/go -o "$out/forms.test" .)
cp "$here"/go/*.go "$out/meta/"
python3 "$here/lab/extractor/relay/go.meta.gen.py" --dir "$out/meta" --parser ast > /dev/null
python3 "$here/lab/measure/graph_vs_coverage.py" --typed "$out/typed.json" --test-bin "$out/forms.test" --pkg-dir "$here/go" \
  --meta-dir "$out/meta" --out "$out/report.json" --expect sound > /dev/null
python3 "$here/lab/measure/graph_vs_coverage.py" --typed "$out/typed.json" --test-bin "$out/forms.test" --pkg-dir "$here/go" \
  --drop-edges 0.05 --seed 1 --out "$out/planted.json" --expect misses > /dev/null
python3 - "$out" <<'PY'
import json, sys
r = json.load(open(sys.argv[1] + "/report.json")); p = json.load(open(sys.argv[1] + "/planted.json"))
for g, v in r["graphs"].items():
    print("%-5s tests with a miss %3d/%d, missed pairs %5d, distinct missed functions %3d, precision mean %s"
          % (g, v["tests_with_a_miss"], r["tests"], v["missed_test_function_pairs"], v["distinct_missed_functions"], v["precision_mean"]))
print("rerun share per changed function:", r["share_of_tests_rerun_per_changed_function"])
print("planted defect (5%% of edges dropped): tests with a miss %d" % p["graphs"]["typed"]["tests_with_a_miss"])
PY
# the second planted defect is a rule of the extractor switched off: without the interface conversion edges (fmt calling String,
# errors.Is calling Unwrap) the graph must miss
"$out/typeddeps" -planted-no-conversions "$here/go" > "$out/typed-no-conversions.json"
python3 "$here/lab/measure/graph_vs_coverage.py" --typed "$out/typed-no-conversions.json" --test-bin "$out/forms.test" --pkg-dir "$here/go" \
  --out "$out/planted-no-conversions.json" --expect misses > /dev/null
python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["graphs"]["typed"]; print("planted defect (no conversion edges): tests with a miss %d, missed %s" % (v["tests_with_a_miss"], v["most_missed"]))' "$out/planted-no-conversions.json"
