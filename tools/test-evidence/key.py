"""The validity key (docs/TEST-EVIDENCE.md, "The validity key").

The digest of everything that, if it changed, would make an older measurement say nothing about a target: the
lockfiles, the pinned tools and thresholds, the whole harness scope (key-inputs.json), the resolved execution
environment, the version of the selector and the schema. Product code is not in it: a change of the product is
what a DELTA looks at, not a reason to distrust the measurement. The list of patterns is itself an input, so
adding a file to it invalidates every older record. It fails closed: a pattern that matches nothing, a symlink, a
file that cannot be read, is an error, not a smaller key.
"""
import hashlib
import json
import os
import re

import canon
import record

SELECTOR_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))


class KeyError_(Exception):
    def __init__(self, kind, detail=""):
        super().__init__("%s %s" % (kind, detail))
        self.kind = kind


def glob_regex(pattern):
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z")


def load_patterns(path=None):
    path = path or os.path.join(HERE, "key-inputs.json")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        doc = json.loads(raw)
        include, exclude = doc["include"], doc["exclude"]
        if set(doc) != {"include", "exclude"} or not include or not all(isinstance(x, str) for x in include + exclude):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise KeyError_("PATTERNS", path)
    return {"include": include, "exclude": exclude}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def expand(root, patterns):
    """The files the patterns select under root, sorted by path. Every include pattern has to select something."""
    inc = [(p, glob_regex(p)) for p in patterns["include"]]
    exc = [glob_regex(p) for p in patterns["exclude"]]
    hit = {p: 0 for p, _ in inc}
    files = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if any(rx.match(rel) for rx in exc):
                continue
            matched = [p for p, rx in inc if rx.match(rel)]
            if not matched:
                continue
            if os.path.islink(full) or not os.path.isfile(full):
                raise KeyError_("NOT_A_REGULAR_FILE", rel)
            for p in matched:
                hit[p] += 1
            files.append(rel)
    for p, n in hit.items():
        if n == 0:
            raise KeyError_("NO_MATCH", p)
    return sorted(files, key=lambda r: r.encode("utf-8"))


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inputs(root, environment, patterns=None):
    """The named digests the key is made of; the record carries them as validity_inputs."""
    patterns = patterns or load_patterns()
    try:
        record._environment(environment, "environment")
    except record.RecordError as e:
        raise KeyError_("ENVIRONMENT", e.detail)
    out = {}
    for rel in expand(root, patterns):
        try:
            out["file:" + rel] = sha256_file(os.path.join(root, rel))
        except OSError as e:
            raise KeyError_("UNREADABLE", "%s: %s" % (rel, e))
    out["environment"] = canon.content_hash(environment)
    out["patterns"] = canon.content_hash(patterns)
    out["selector"] = _sha("selector-version:%d" % SELECTOR_VERSION)
    out["schema"] = _sha("schema:" + record.SCHEMA)
    return out


def key_of(named_inputs):
    return canon.content_hash({"inputs": named_inputs})


def validity_key(root, environment, patterns=None):
    named = inputs(root, environment, patterns)
    return key_of(named), named
