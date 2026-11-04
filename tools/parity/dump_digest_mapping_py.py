#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Python dump of the digest mapping differential (docs/P5-DIGEST-MAPPING-UNIT2-CONTRACT.md, section 3): the reference tools/p5/digest_mapping.py on the input set.

    dump_digest_mapping_py.py IN OUT [--reference PATH]

Same line format as the Rust and Go dumps: {"id", "ok": true, "value"} or {"id", "ok": false, "reason", "field"}; one line per input line. An exception that is not a Refusal
is written as reason CRASH, never as a defined reason. `--reference` lets the selftest run a mutated copy.
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.join(HERE, "..", "p5", "digest_mapping.py")


def load(path):
    spec = importlib.util.spec_from_file_location("digest_mapping_dumped", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dump(dm, lines):
    out = []
    for line in lines:
        if not line.strip():
            continue
        c = json.loads(line)
        try:
            if c["kind"] == "convert":
                value = dm.to_receipt_digest(c["input"])
            elif c["kind"] == "map":
                value = dm.map_release(c["record"], c["pin"])
            else:
                raise ValueError("unknown kind %r" % c["kind"])
            row = {"id": c["id"], "ok": True, "value": value}
        except dm.Refusal as e:
            row = {"id": c["id"], "ok": False, "reason": e.reason, "field": e.field or None}
        except Exception as e:  # noqa: BLE001  # a crash is a wrong answer for THAT case
            row = {"id": c["id"], "ok": False, "reason": "CRASH", "detail": type(e).__name__}
        out.append(json.dumps(row, sort_keys=True, ensure_ascii=True))
    return "".join(r + "\n" for r in out)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--reference", default=REFERENCE)
    a = ap.parse_args(argv)
    with open(a.input, encoding="utf-8") as f:
        text = dump(load(a.reference), f.read().splitlines())
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
