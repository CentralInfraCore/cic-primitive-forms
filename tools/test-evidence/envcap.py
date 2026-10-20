"""The resolved execution environment of a measurement (docs/EVIDENCE-PRODUCTION.md, "The environment").

Each measuring job writes the part of the environment it is responsible for, as the tools report it, never as the label the
workflow asked for (`stable`, `ubuntu-latest`). The parts are merged for the record: a field that two jobs report differently
is a conflict and the record is not made; a field nobody reports is missing and the record is not made. The action revisions
come from the pinned `uses:` of the workflows (pins.py).
"""
import json
import os
import re
import subprocess
import sys

import pins
import record

FIELDS = ("rustc", "cargo", "go", "python", "runner", "tool:cargo-llvm-cov", "tool:cargo-mutants", "tool:cargo-fuzz", "tool:go-mutesting")


class EnvError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind


def _run(argv, run):
    try:
        done = run(argv, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise EnvError("UNRESOLVED", "%s: %s" % (" ".join(argv), e))
    if done.returncode != 0:
        raise EnvError("UNRESOLVED", "%s exited %d" % (" ".join(argv), done.returncode))
    return done.stdout.decode("utf-8", "replace")


def _match(rx, text, what):
    m = re.search(rx, text, re.M)
    if not m:
        raise EnvError("UNRESOLVED", "no %s in %r" % (what, text[:80]))
    return m.group(1)


def _version(value, what):
    """A version, not a label: `stable`, `latest`, `nightly` say nothing about what ran."""
    if not record.VERSION.match(value):
        raise EnvError("UNRESOLVED", "%s %r is not a version" % (what, value))
    return value


def _tool(argv, name, run):
    return _version(_match(r"^%s\s+(\S+)" % re.escape(name), _run(argv, run), name + " version"), name)


def capture(fields, run=subprocess.run, environ=None, py=None, toolchain=None):
    """The part of the environment named by `fields`, resolved now. With `toolchain` the Rust commands are asked as
    `cargo +TOOLCHAIN ...`, the way the measuring jobs run them: the default toolchain of a runner is not the one that ran."""
    environ = os.environ if environ is None else environ
    plus = ["+" + toolchain] if toolchain else []
    py = py or "%d.%d.%d" % sys.version_info[:3]
    out = {}
    for f in fields:
        if f not in FIELDS:
            raise EnvError("UNKNOWN_FIELD", f)
        if f == "rustc":
            out["rustc"] = _version(_match(r"^release:\s*(\S+)", _run(["rustc"] + plus + ["-vV"], run), "rustc release"), "rustc")
        elif f == "cargo":
            out["cargo"] = _version(_match(r"^cargo\s+(\S+)", _run(["cargo"] + plus + ["-V"], run), "cargo version"), "cargo")
        elif f == "go":
            out["go"] = _match(r"\bgo version (go\d+\.\d+(?:\.\d+)?)", _run(["go", "version"], run), "go version")
        elif f == "python":
            out["python"] = py
        elif f == "runner":
            try:
                out["runner"] = {"image_os": environ["ImageOS"], "image_version": environ["ImageVersion"],
                                 "os": environ["RUNNER_OS"], "arch": environ["RUNNER_ARCH"]}
            except KeyError as e:
                raise EnvError("UNRESOLVED", "the runner does not say %s" % e)
        elif f == "tool:cargo-llvm-cov":
            out.setdefault("tools", {})["cargo-llvm-cov"] = _tool(["cargo"] + plus + ["llvm-cov", "--version"], "cargo-llvm-cov", run)
        elif f == "tool:cargo-mutants":
            out.setdefault("tools", {})["cargo-mutants"] = _tool(["cargo"] + plus + ["mutants", "--version"], "cargo-mutants", run)
        elif f == "tool:cargo-fuzz":
            out.setdefault("tools", {})["cargo-fuzz"] = _tool(["cargo"] + plus + ["fuzz", "--version"], "cargo-fuzz", run)
        elif f == "tool:go-mutesting":
            exe = _run(["which", "go-mutesting"], run).strip()
            ver = _version(_match(r"^\s*mod\s+github\.com/avito-tech/go-mutesting\s+v(\S+)", _run(["go", "version", "-m", exe], run), "go-mutesting module"), "go-mutesting")
            out.setdefault("tools", {})["go-mutesting"] = ver
    return out


def merge(parts, root):
    """The whole environment of a record from the parts the jobs wrote, with the action revisions of the workflows under root."""
    env = {}

    def put(path, value):
        node = env
        for p in path[:-1]:
            node = node.setdefault(p, {})
        if path[-1] in node and node[path[-1]] != value:
            raise EnvError("CONFLICT", "%s: %r and %r" % (".".join(path), node[path[-1]], value))
        node[path[-1]] = value

    for part in parts:
        for k, v in part.items():
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    put((k, k2), v2)
            else:
                put((k,), v)
    actions, problems = pins.scan(root)
    if problems or not actions:
        raise EnvError("UNPINNED", "; ".join(problems) or "no action found")
    env["actions"] = actions
    try:
        record._environment(env, "environment")
    except record.RecordError as e:
        raise EnvError("INCOMPLETE", e.detail)
    return env


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--toolchain")
    a = ap.parse_args(argv)
    part = capture([f for f in a.fields.split(",") if f], toolchain=a.toolchain)
    with open(a.out, "w") as fh:
        json.dump(part, fh, sort_keys=True)
    return 0
