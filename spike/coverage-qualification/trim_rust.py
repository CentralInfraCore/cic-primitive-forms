#!/usr/bin/env python3
"""Reduce a raw `cargo llvm-cov --json` export to what the spike needs, without absolute paths."""
import json
import sys

d = json.load(open(sys.argv[1]))["data"][0]
keep = {
    "summary_branches": d["files"][0]["summary"]["branches"],
    "summary_regions": d["files"][0]["summary"]["regions"],
    # The mangled names carry a hash that depends on the build path, so functions are identified by the position of
    # their first region and kept in that order.
    "functions": sorted(({"first_region": f["regions"][0][:2] if f["regions"] else None, "branches": f["branches"],
                          "regions": [r[:5] + [r[7]] for r in f["regions"]]} for f in d["functions"] if f["regions"]),
                        key=lambda f: (f["first_region"], str(f["regions"]))),
}
json.dump(keep, open(sys.argv[2], "w"), indent=1, sort_keys=True)
