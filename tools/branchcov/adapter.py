#!/usr/bin/env python3
"""Turn the output of a cic-branch/1 measurement (gobranch or rustbranch) into a cic-coverage-report/1 (ADR 0009).

    adapter.py {go,rust} --run HITS.json --root REPO --module-dir REL --scope SCOPE.yaml --commit SHA --out REPORT.json

The measurement tool enumerates the edges; this adapter only adds them up per file and category, takes the
digests from the files in the repository, and refuses to produce a report when the measured set is not exactly the
scope (a file the build did not include, a file nobody measured, a digest the tool saw differently).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import yaml  # noqa: E402

import coverage_gate as cg  # noqa: E402


class Refused(Exception):
    pass


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def categories(edges_hits):
    cats = {c: {"total": 0, "covered": 0} for c in cg.CATEGORIES}
    for category, hit in edges_hits:
        if category not in cats:
            raise Refused("unknown category %r" % category)
        cats[category]["total"] += 1
        cats[category]["covered"] += 1 if hit > 0 else 0
    return cats


def build_report(lang, run, root, module_dir, scope, commit):
    if run.get("ignored_files"):  # files the build did not include (go build constraints); the Rust tool refuses cfg instead
        ignored = {os.path.normpath(os.path.join(module_dir, p)).replace(os.sep, "/") for p in run["ignored_files"]}
    else:
        ignored = set()
    expected = set(cg.own_sources(root, scope[lang]))
    bad = sorted(expected & ignored)
    if bad:
        raise Refused("files of the scope are not part of the measured build (build constraints): %s" % bad)
    files = []
    for pkg in run["packages"]:
        for f in pkg["files"]:
            path = os.path.normpath(os.path.join(module_dir, f["path"])).replace(os.sep, "/")
            digest = sha256_file(os.path.join(root, path))
            if digest != f["sha256"]:
                raise Refused("%s changed between the measurement and the report" % path)
            if len(f["edges"]) != len(f["hits"]):
                raise Refused("%s: edges and hits differ in number" % path)
            ids = [e["id"] for e in f["edges"]]
            if len(set(ids)) != len(ids):
                raise Refused("%s: duplicate edge ids" % path)
            cats = categories((e["category"], h) for e, h in zip(f["edges"], f["hits"]))
            files.append({"path": path, "sha256": digest,
                          "total": sum(c["total"] for c in cats.values()),
                          "covered": sum(c["covered"] for c in cats.values()), "categories": cats})
    measured = {f["path"] for f in files}
    for p in sorted(expected - measured):
        raise Refused("%s is in the scope but was not measured" % p)
    for p in sorted(measured - expected):
        raise Refused("%s was measured but is not in the scope" % p)
    files.sort(key=lambda f: f["path"].encode("utf-8"))
    return {
        "format": cg.REPORT_FORMAT, "language": lang, "metric": "branch", "branch_definition": cg.DEFINITION,
        "tool": {"name": run["tool"]["name"], "version": run["tool"]["version"]},
        "unmeasured_categories": [],
        "source": {"commit": commit, "files": files},
        "totals": {"total": sum(f["total"] for f in files), "covered": sum(f["covered"] for f in files)},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=["go", "rust"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--module-dir", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    scope = yaml.safe_load(open(a.scope, encoding="utf-8"))["languages"]
    try:
        with open(a.run) as fh:
            report = build_report(a.language, json.load(fh), a.root, a.module_dir, scope, a.commit)
    except Refused as exc:
        print("adapter: REFUSED: %s" % exc, file=sys.stderr)
        return 1
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
