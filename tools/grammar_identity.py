#!/usr/bin/env python3
"""Grammar identity of the cic-primitives grammar (ADR 0002).

The identity is the immutable commit plus a manifest of exactly the files the library
reads: for each file the SHA-256 of its raw Git blob bytes at that commit, in the
`sha256sum` format, sorted by the UTF-8 bytes of the PATH (not of the whole line), LF
line ends. The manifest digest is the SHA-256 of that text. There is no YAML
normalization and no whitespace processing. Nothing from the upstream repository is
ever executed: only the bytes of the pinned files are read.

    grammar_identity.py compute  --repo DIR --commit SHA     print a pin block
    grammar_identity.py commit   --pins corpus/pins.yaml     print the pinned commit
    grammar_identity.py verify   --pins corpus/pins.yaml --repo DIR   fail unless the
                                 bytes of DIR at the pinned commit match the pin
    grammar_identity.py selftest                              seeded-fault proof
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

# The accepted file set (ADR 0002, owner decision, amended for D-018 and again for the resolution reference).
# Changing it changes the identity and needs an ADR change; the README is part of it deliberately.
# The three files after the seven are the first amendment: the normative text of the access-policy/1
# grammar and its migration rules (cic-primitives D-018, P0.4), which the schemas refer to, and the
# upstream licence text (CC BY-NC-SA 4.0), so that the licence of the pinned bytes is part of the
# verified identity. The two files `access_policy.py` and `access-vectors/resolution.yaml` are the second
# amendment (ADR 0002, P3 fourth unit): the reference model of the resolution and its golden vectors, which
# PolicyResolution is cross-checked against. They are test data read at the pinned bytes; the Python is
# never executed by the library and is not a runtime dependency. The other P0.4 vectors (reading,
# expansion, migration) stay contract evidence outside the set.
GRAMMAR_FILES = (
    "LICENSE.md",
    "proposals/atom-grammar/P0-4-ACCESS-GRAMMAR.md",
    "proposals/atom-grammar/P0-4-MIGRATION.md",
    "proposals/atom-grammar/README.md",
    "proposals/atom-grammar/access-vectors/resolution.yaml",
    "proposals/atom-grammar/access_policy.py",
    "proposals/atom-grammar/check_grammar.py",
    "proposals/atom-grammar/instance-grammar.schema.yaml",
    "schemas/aggregate/policy-surface.yaml",
    "schemas/atomic/access.yaml",
    "schemas/atomic/role.yaml",
    "schemas/atomic/shape.yaml",
)


def sorted_by_path(entries):
    """Entries (path, sha256) in the order of the manifest: UTF-8 bytes of the path."""
    return sorted(entries, key=lambda e: e[0].encode("utf-8"))


def manifest_text(entries):
    return "".join("%s  %s\n" % (sha, path) for path, sha in sorted_by_path(entries))


def manifest_digest(entries):
    return hashlib.sha256(manifest_text(entries).encode("utf-8")).hexdigest()


def pin_name(repo, commit, digest):
    """The pin carries its own identity: re-pinning changes the name, never silently."""
    return "%s-%s-%s" % (repo.split("/")[-1], commit[:7], digest[:8])


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=False)


def blob(repo, commit, path):
    r = git(repo, "cat-file", "blob", "%s:%s" % (commit, path))
    return r.stdout if r.returncode == 0 else None


def compute(repo, commit):
    entries = []
    for p in GRAMMAR_FILES:
        data = blob(repo, commit, p)
        if data is None:
            raise SystemExit("compute: %s is not in %s" % (p, commit))
        entries.append((p, hashlib.sha256(data).hexdigest()))
    return entries


def load_pins(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["grammar"]


def verify_pin(name, pin, repo):
    """Return a list of (reason, detail). Empty means the pin reproduces from the repo bytes."""
    out = []
    if not os.path.isdir(repo) or git(repo, "rev-parse", "--git-dir").returncode != 0:
        return [("GRAMMAR_REPO_UNREADABLE", "%s is not a Git repository" % repo)]
    head = git(repo, "rev-parse", "HEAD").stdout.decode().strip()
    if head != pin["commit"]:
        out.append(("COMMIT_MISMATCH", "checked out %s, pinned %s" % (head or "nothing", pin["commit"])))
    if git(repo, "cat-file", "-e", "%s^{commit}" % pin["commit"]).returncode != 0:
        return out + [("COMMIT_MISMATCH", "commit %s is not in the repository" % pin["commit"])]
    listed = [f["path"] for f in pin["files"]]
    if sorted(listed) != sorted(GRAMMAR_FILES):
        out.append(("FILESET_MISMATCH", "pinned %s, accepted %s" % (sorted(listed), list(GRAMMAR_FILES))))
    actual = []
    for f in pin["files"]:
        data = blob(repo, pin["commit"], f["path"])
        if data is None:
            out.append(("FILE_MISSING", f["path"]))
            continue
        sha = hashlib.sha256(data).hexdigest()
        actual.append((f["path"], sha))
        if sha != f["sha256"]:
            out.append(("FILE_HASH_MISMATCH", "%s: pinned %s, upstream %s" % (f["path"], f["sha256"], sha)))
    if len(actual) == len(pin["files"]) and manifest_digest(actual) != pin["manifest_sha256"]:
        out.append(("MANIFEST_DIGEST_MISMATCH", "pinned %s, upstream %s" % (pin["manifest_sha256"], manifest_digest(actual))))
    expected = pin_name(pin["repo"], pin["commit"], pin["manifest_sha256"])
    if name != expected:
        out.append(("PIN_NAME_MISMATCH", "%s, expected %s" % (name, expected)))
    return out


REASONS = ("GRAMMAR_REPO_UNREADABLE", "COMMIT_MISMATCH", "FILESET_MISMATCH", "FILE_MISSING",
           "FILE_HASH_MISMATCH", "MANIFEST_DIGEST_MISMATCH", "PIN_NAME_MISMATCH")


def run_verify(pins_path, repo):
    pins = load_pins(pins_path)
    if not pins:
        print("GRAMMAR IDENTITY: FAIL (no grammar pin)")
        return 1
    bad = 0
    for name, pin in pins.items():
        fails = verify_pin(name, pin, repo)
        for r, d in fails:
            print("FAIL %s %s: %s" % (name, r, d))
        if not fails:
            for f in sorted_by_path([(f["path"], f["sha256"]) for f in pin["files"]]):
                print("ok   %s  %s" % (f[1], f[0]))
            print("ok   manifest %s" % pin["manifest_sha256"])
        bad += len(fails)
    print("GRAMMAR IDENTITY: %s" % ("FAIL (%d)" % bad if bad else "PASS"))
    return 1 if bad else 0


# --- self-test: a throwaway Git repository with seeded faults ----------------------

def make_repo(root, tweak=None):
    os.makedirs(root)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def run(*a):
        subprocess.run(["git", "-C", root, *a], check=True, capture_output=True, env=env)
    run("init", "-q")
    for i, p in enumerate(GRAMMAR_FILES):
        fp = os.path.join(root, p)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as fh:
            fh.write(("synthetic file %d\r\nwith CRLF and no normalization\n" % i).encode())
    run("add", "-A")
    run("-c", "commit.gpgsign=false", "commit", "-q", "-m", "x")
    commit = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, check=True).stdout.decode().strip()
    return commit, run, env


def selftest():
    work = tempfile.mkdtemp(prefix="grammar-id-")
    failed = 0
    try:
        commit, run, env = make_repo(os.path.join(work, "up"))
        up = os.path.join(work, "up")
        entries = compute(up, commit)
        digest = manifest_digest(entries)
        good = {"repo": "X/up", "ref": "devel", "commit": commit,
                "files": [{"path": p, "sha256": s} for p, s in sorted_by_path(entries)], "manifest_sha256": digest}
        name = pin_name("X/up", commit, digest)
        if verify_pin(name, good, up):
            print("self-test: the unmodified pin does not reproduce: %s" % verify_pin(name, good, up))
            return 1
        # independent reproduction with the shell tools: byte-identical manifest
        sh = subprocess.run("cd %s && for f in %s; do printf '%%s  %%s\\n' \"$(git show %s:$f | sha256sum | cut -d' ' -f1)\" \"$f\"; done | LC_ALL=C sort -k2 | sha256sum | cut -d' ' -f1"
                            % (up, " ".join(GRAMMAR_FILES), commit), shell=True, capture_output=True, text=True).stdout.strip()
        if sh != digest:
            print("self-test: the manifest digest differs from the sha256sum reproduction (%s vs %s)" % (sh, digest))
            return 1
        order_ok = [e[0] for e in sorted_by_path(entries)] == list(GRAMMAR_FILES)
        by_line = [l.split("  ", 1)[1] for l in sorted(manifest_text(entries).splitlines())]
        if not order_ok or by_line == list(GRAMMAR_FILES):
            print("self-test: the manifest must be ordered by path bytes, which differs from ordering the lines")
            return 1
        if sorted_by_path([("é.yaml", "1"), ("z.yaml", "2")])[0][0] != "z.yaml":
            print("self-test: path order is not by UTF-8 bytes")
            return 1
        covered = set()
        def expect(label, reason, pin, repo=up, nm=None):
            nonlocal failed
            got = {r for r, _ in verify_pin(nm or pin_name("X/up", pin["commit"], pin["manifest_sha256"]), pin, repo)}
            if reason in got:
                covered.add(reason)
                print("  red   %-44s -> %s" % (label, reason))
            else:
                failed += 1
                print("  GREEN %-44s expected %s, got %s" % (label, reason, sorted(got) or "pass"))
        import copy
        def mod(f):
            p = copy.deepcopy(good); f(p); return p
        expect("pinned hash of one file is wrong", "FILE_HASH_MISMATCH", mod(lambda p: p["files"][2].__setitem__("sha256", "0" * 64)))
        expect("manifest digest is wrong", "MANIFEST_DIGEST_MISMATCH", mod(lambda p: p.__setitem__("manifest_sha256", "f" * 64)))
        expect("one file removed from the pin", "FILESET_MISMATCH", mod(lambda p: p["files"].pop()))
        expect("an extra file in the pin", "FILESET_MISMATCH", mod(lambda p: p["files"].append({"path": "schemas/atomic/extra.yaml", "sha256": "1" * 64})))
        expect("a pinned file is not in the commit", "FILE_MISSING", mod(lambda p: p["files"].__setitem__(0, {"path": "nope/missing.yaml", "sha256": "1" * 64})))
        expect("pin name does not carry the identity", "PIN_NAME_MISMATCH", good, nm="X-up-old")
        expect("pinned commit is not the checkout", "COMMIT_MISMATCH", mod(lambda p: p.__setitem__("commit", "a" * 40)))
        expect("pinned commit is absent from the repository", "COMMIT_MISMATCH", mod(lambda p: p.__setitem__("commit", "b" * 40)))
        expect("not a Git repository", "GRAMMAR_REPO_UNREADABLE", good, repo=os.path.join(work, "nowhere"))
        # upstream bytes change after the pin: a new commit with one edited file
        with open(os.path.join(up, GRAMMAR_FILES[4]), "ab") as fh:
            fh.write(b"# edited upstream\n")
        run("add", "-A"); run("-c", "commit.gpgsign=false", "commit", "-q", "-m", "y")
        expect("upstream moved: HEAD is another commit", "COMMIT_MISMATCH", good)
        run("checkout", "-q", commit)
        # the same commit, but the working tree is dirty: blobs are read from Git, not from the tree
        with open(os.path.join(up, GRAMMAR_FILES[0]), "ab") as fh:
            fh.write(b"dirty")
        if verify_pin(name, good, up):
            failed += 1
            print("  GREEN a dirty working tree must not matter, the blob decides")
        else:
            print("  ok    the blob of the commit is read, not the working tree")
        for r in REASONS:
            if r not in covered:
                failed += 1
                print("  reason %s has no fault that turns it red" % r)
    finally:
        shutil.rmtree(work)
    print("self-test: %d of %d reasons covered, %s" % (len(covered), len(REASONS), "FAILED" if failed else "the verifier can fail for every reason"))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute"); c.add_argument("--repo", required=True); c.add_argument("--commit", required=True)
    k = sub.add_parser("commit"); k.add_argument("--pins", required=True)
    v = sub.add_parser("verify"); v.add_argument("--pins", required=True); v.add_argument("--repo", required=True)
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "compute":
        e = compute(a.repo, a.commit)
        d = manifest_digest(e)
        print("commit: %s\nmanifest_sha256: %s\nname: %s" % (a.commit, d, pin_name("CentralInfraCore/cic-primitives", a.commit, d)))
        print("files:")
        for p, s in sorted_by_path(e):
            print("  - {path: %s, sha256: %s}" % (p, s))
        return 0
    if a.cmd == "commit":
        pins = load_pins(a.pins)
        if len(pins) != 1:
            print("exactly one grammar pin is expected, found %d" % len(pins), file=sys.stderr)
            return 1
        print(next(iter(pins.values()))["commit"])
        return 0
    if a.cmd == "verify":
        return run_verify(a.pins, a.repo)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
