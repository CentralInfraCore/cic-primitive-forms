"""RED reproduction: a Go constant that repeats the previous spec's expression (implicit iota) has no edge to what its value
comes from, so a change of that value leaves its Merkle key, and the key of every test using it, unchanged.

    python3 lab/measure/repro_iota.py TYPEDDEPS_BINARY --expect vulnerable|fixed [--record OUT.json]

--expect vulnerable (the default while the extractor is unfixed): exit 0 when the defect is reproduced (all four facts hold).
--expect fixed: exit 0 when the graph is no longer blind, i.e. the key of TestB differs between a and b (fact 4 no longer holds
and fact 2 is false) while fact 3 still holds. A known-RED case kept on purpose: the decision function must answer UNVERIFIABLE
for evidence made by the graph that is blind to it, and RERUN_REQUIRED once the graph carries the implicit edge.
--record writes what was observed (source digests, keys, edges, test results, tool versions).
Fixtures: lab/testdata/iota/a (A = iota + 1; B) and b (A = iota + 2; B); TestB wants B == 2.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402

here = os.path.dirname(os.path.abspath(__file__))
fx = os.path.join(here, "..", "testdata", "iota")
TEST = "iotarepro.TestB"


def graph(binary, v):
    out = subprocess.run([binary, "."], cwd=os.path.join(fx, v), check=True, capture_output=True, text=True).stdout
    g = json.loads(out)
    return {n["id"]: n for n in g["nodes"]}, merkle.keys(g)[0]


def passes(v):
    return subprocess.run(["go", "test", "-count=1", "./..."], cwd=os.path.join(fx, v), capture_output=True).returncode == 0


def tool(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unavailable"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("--expect", choices=["vulnerable", "fixed"], default="vulnerable")
    ap.add_argument("--record")
    a = ap.parse_args()
    na, ka = graph(os.path.abspath(a.binary), "a")
    nb, kb = graph(os.path.abspath(a.binary), "b")
    pa, pb = passes("a"), passes("b")
    facts = {
        "1 B source digest identical": na["iotarepro.B"]["src_sha256"] == nb["iotarepro.B"]["src_sha256"],
        "2 TestB Merkle key identical": ka[TEST] == kb[TEST],
        "3 TestB passes on a, fails on b": pa and not pb,
        "4 B has no edge to A (graph blind to the change)": "iotarepro.A" not in nb["iotarepro.B"]["deps"],
    }
    for k, v in facts.items():
        print(("HOLDS  " if v else "no     ") + k)
    vulnerable = all(facts.values())
    fixed = (not facts["2 TestB Merkle key identical"]) and facts["3 TestB passes on a, fails on b"]
    if a.record:
        rec = {"tool": {"go": tool("go", "version"), "typeddeps_build": [l.strip() for l in tool("go", "version", "-m", os.path.abspath(a.binary)).splitlines() if "x/tools" in l or "x/mod" in l or "x/sync" in l]}, "facts": facts,
               "commits": {v: {"nodes": {i: {"kind": n["kind"], "src_sha256": n["src_sha256"], "key": k[i], "deps": n["deps"]}
                                         for i, n in ns.items()}, "test_passes": p}
                           for v, ns, k, p in (("a", na, ka, pa), ("b", nb, kb, pb))}}
        with open(a.record, "w") as f:
            json.dump(rec, f, indent=1, sort_keys=True)
            f.write("\n")
    if a.expect == "vulnerable":
        print("RED reproduced: the rule would reuse TestB on b, and TestB fails there." if vulnerable else "NOT reproduced.")
        return 0 if vulnerable else 1
    print("fixed: the key of TestB differs between a and b." if fixed else "NOT fixed: TestB would still be reused on b.")
    return 0 if fixed else 1


if __name__ == "__main__":
    sys.exit(main())
