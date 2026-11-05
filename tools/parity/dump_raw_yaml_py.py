#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Python dump of the raw YAML differential (docs/P5-RAW-YAML-UNIT3-CONTRACT.md, Unit 3b): PyYAML `safe_load` and the Unit 2 mapping reference on the raw bytes.

    dump_raw_yaml_py.py IN OUT

PyYAML is the ORACLE OF DIFFERENCE, never a vote in any admission. Same line format as the Rust and Go dumps; a tree is written in the same typed form, with two extra kinds a Forms
parser never produces ("float", "other"). A text PyYAML refuses is {"ok": false, "class": "syntax"}; an error of PyYAML's own that is not a refusal of the text (a recursion
limit on deep nesting, for example) is class "python_error" and is reported, not red: this reader is not under proof.
"""
import argparse
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "p5"))
import digest_mapping as dm  # noqa: E402


def typed(v):
    if isinstance(v, dict):
        return {"k": "map", "v": [[k if isinstance(k, str) else "non-string-key:" + repr(k), typed(x)] for k, x in v.items()]}
    if isinstance(v, list):
        return {"k": "seq", "v": [typed(x) for x in v]}
    if v is None:
        return {"k": "null", "v": None}
    if isinstance(v, bool):
        return {"k": "bool", "v": v}
    if isinstance(v, int):
        return {"k": "int", "v": v}
    if isinstance(v, str):
        return {"k": "str", "v": v}
    if isinstance(v, float):
        return {"k": "float", "v": repr(v)}
    return {"k": "other", "v": type(v).__name__}


class _StrictLoader(yaml.SafeLoader):
    """TEST ONLY (selftest, Unit 3c): a PyYAML that refuses a duplicate key instead of taking the last."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, "duplicate key", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)
VARIANT = None


def load(text):
    if VARIANT == "strict-duplicates":
        return yaml.load(text, Loader=_StrictLoader)
    value = yaml.safe_load(text)
    if VARIANT == "stringify-envelope" and isinstance(value, dict):
        for who in ("release", "provenance"):
            if isinstance(value.get(who), dict) and isinstance(value[who].get("envelope"), int):
                value[who]["envelope"] = str(value[who]["envelope"])
    return value


def answer(case):
    cid = case["id"]
    data = bytes.fromhex(case["bytes_hex"])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"id": cid, "ok": False, "class": "invalid_utf8", "code": "INVALID_UTF8"}
    try:
        value = load(text)
    except yaml.YAMLError as e:
        return {"id": cid, "ok": False, "class": "syntax", "code": type(e).__name__}
    except Exception as e:  # noqa: BLE001  # the oracle failing is a fact about the oracle
        return {"id": cid, "ok": False, "class": "python_error", "code": type(e).__name__}
    mapping = None
    if isinstance(case.get("pin"), dict):
        try:
            mapping = {"ok": dm.map_release(value, case["pin"])}
        except dm.Refusal as e:
            mapping = {"refuse": e.reason, "field": e.field or None}
        except Exception as e:  # noqa: BLE001
            mapping = {"refuse": "PYTHON_ERROR:" + type(e).__name__, "field": None}
    try:
        tree = typed(value)
    except Exception as e:  # noqa: BLE001
        return {"id": cid, "ok": False, "class": "python_error", "code": type(e).__name__}
    return {"id": cid, "ok": True, "tree": tree, "mapping": mapping}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--variant", choices=["strict-duplicates", "stringify-envelope"], help="TEST ONLY: a deliberately changed oracle, for the selftest")
    a = ap.parse_args(argv)
    global VARIANT
    VARIANT = a.variant
    sys.setrecursionlimit(5000)
    with open(a.input, encoding="utf-8") as f, open(a.output, "w", encoding="utf-8") as out:
        for line in f.read().splitlines():
            if line.strip():
                out.write(json.dumps(answer(json.loads(line)), sort_keys=True, ensure_ascii=True) + "\n")
                out.flush()                       # one line per answered case, at once: the runner supervises this process like the others
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
