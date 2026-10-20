"""Probes of the publisher against real git repositories: no force, no token in an argument, no silent overwrite."""
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import canon  # noqa: E402
import publish  # noqa: E402

DATE = "2026-10-20"


def rec(n, outcome="pass"):
    raw = canon.canonicalize({"outcome": outcome, "n": n})
    return raw, hashlib.sha256(raw).hexdigest() + ".json"


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    done = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull] + list(args), cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class Recorder:
    """A `run` that records every git command and can act before one of them."""

    def __init__(self):
        self.calls, self.before = [], None

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw.get("env", {})))
        if self.before:
            self.before(argv)
        return subprocess.run(argv, **kw)


class PublishTests(unittest.TestCase):
    def setUp(self):
        try:
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no git")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin = os.path.join(self.tmp.name, "origin.git")
        git(self.tmp.name, "init", "-q", "--bare", self.origin)

    def read(self, rel):
        out = os.path.join(self.tmp.name, "read")
        if not os.path.exists(out):
            git(self.tmp.name, "clone", "-q", "--branch", "ci-evidence", self.origin, out)
        else:
            git(out, "pull", "-q")
        with open(os.path.join(out, rel), "rb") as fh:
            return fh.read()

    def test_the_branch_is_created_and_the_record_is_in_it(self):
        raw, name = rec(1)
        sha = publish.publish(self.origin, name, raw, "full", DATE)
        self.assertEqual(sha, git(self.origin, "rev-parse", "ci-evidence"))
        self.assertEqual(self.read("evidence/full/" + name), raw)
        self.assertEqual(publish.read_index(self.read("index/%s.json" % DATE)), (DATE, [(name[:-5], "full")]))

    def test_a_second_record_joins_the_index_and_the_history_only_grows(self):
        a, an = rec(1)
        b, bn = rec(2, "fail")
        first = publish.publish(self.origin, an, a, "full", DATE)
        second = publish.publish(self.origin, bn, b, "audit", DATE)
        self.assertEqual(git(self.origin, "rev-parse", second + "^"), first)
        date, entries = publish.read_index(self.read("index/%s.json" % DATE))
        self.assertEqual(sorted(entries), sorted([(an[:-5], "full"), (bn[:-5], "audit")]))
        self.assertEqual(self.read("evidence/audit/" + bn), b)

    def test_publishing_the_same_record_again_changes_nothing(self):
        raw, name = rec(1)
        first = publish.publish(self.origin, name, raw, "full", DATE)
        again = publish.publish(self.origin, name, raw, "full", DATE)
        self.assertEqual(first, again)   # no new commit
        self.assertEqual(publish.read_index(self.read("index/%s.json" % DATE))[1], [(name[:-5], "full")])

    def test_a_different_day_is_a_different_index(self):
        a, an = rec(1)
        b, bn = rec(2)
        publish.publish(self.origin, an, a, "full", "2026-10-19")
        publish.publish(self.origin, bn, b, "full", "2026-10-20")
        self.assertEqual(publish.entries_for(self.checkout(), ["2026-10-19", "2026-10-20", "2026-10-21"]),
                         [(an[:-5], "full"), (bn[:-5], "full")])

    def checkout(self):
        out = os.path.join(self.tmp.name, "co")
        if os.path.exists(out):
            subprocess.run(["rm", "-rf", out], check=True)
        git(self.tmp.name, "clone", "-q", "--branch", "ci-evidence", self.origin, out)
        return out

    def test_it_never_forces_and_the_token_stays_out_of_the_arguments(self):
        raw, name = rec(1)
        r = Recorder()
        publish.publish(self.origin, name, raw, "full", DATE, token="s3cr3t-token", run=r)
        pushes = [a for a, _ in r.calls if "push" in a]
        self.assertTrue(pushes)
        for argv, _ in r.calls:
            for bad in ("--force", "-f", "--force-with-lease", "--mirror", "--delete", "-d", "--prune", "--no-verify"):
                self.assertNotIn(bad, argv)
            self.assertFalse(any(x.startswith("+") for x in argv))
            self.assertFalse(any("s3cr3t-token" in x or "x-access-token" in x for x in argv))
        for argv, env in r.calls:
            self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
            self.assertIn("AUTHORIZATION: basic", env["GIT_CONFIG_VALUE_0"])
            self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(publish.auth_env(None), {})

    def test_someone_else_published_first_so_it_is_retried_on_the_new_tip(self):
        a, an = rec(1)
        b, bn = rec(2)
        publish.publish(self.origin, an, a, "full", DATE)
        r = Recorder()
        done = {"n": 0}

        def competitor(argv):
            if "push" in argv and not done["n"]:
                done["n"] = 1
                c, cn = rec(3)
                publish.publish(self.origin, cn, c, "full", DATE)   # lands between the clone and the push
        r.before = competitor
        publish.publish(self.origin, bn, b, "full", DATE, run=r)
        _, entries = publish.read_index(self.read("index/%s.json" % DATE))
        self.assertEqual(len(entries), 3)  # all three, none lost
        self.assertEqual(len([1 for a_, _ in r.calls if "push" in a_]), 2)

    def test_a_branch_that_always_moves_is_a_race_not_a_force(self):
        a, an = rec(1)
        publish.publish(self.origin, an, a, "full", DATE)
        r = Recorder()
        counter = {"n": 10}

        def always(argv):
            if "push" in argv:
                counter["n"] += 1
                c, cn = rec(counter["n"])
                publish.publish(self.origin, cn, c, "full", DATE)
        r.before = always
        b, bn = rec(2)
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.origin, bn, b, "full", DATE, retries=2, run=r)
        self.assertEqual(cm.exception.kind, "RACE")

    def test_a_refusal_by_the_repository_is_an_error_not_a_retry(self):
        raw, name = rec(1)
        publish.publish(self.origin, name, raw, "full", DATE)
        hook = os.path.join(self.origin, "hooks", "pre-receive")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho 'ruleset: only the evidence App may push' >&2\nexit 1\n")
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IXUSR)
        r = Recorder()
        b, bn = rec(2)
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.origin, bn, b, "full", DATE, run=r)
        self.assertEqual(cm.exception.kind, "PUSH")
        self.assertEqual(len([1 for a_, _ in r.calls if "push" in a_]), 1)  # no second try

    def test_what_is_not_a_record_of_its_name_and_kind_is_refused(self):
        raw, name = rec(1)
        for label, args, kind in (("a name that is not the hash", (self.origin, "0" * 64 + ".json", raw, "full", DATE), "NAME"),
                                  ("a name without .json", (self.origin, name[:-5], raw, "full", DATE), "NAME"),
                                  ("another kind", (self.origin, name, raw, "other", DATE), "ARGUMENT"),
                                  ("a bad date", (self.origin, name, raw, "full", "2026-10-2"), "ARGUMENT"),
                                  ("a pass as an audit record", (self.origin, name, raw, "audit", DATE), "KIND"),
                                  ("a fail as an anchor", (self.origin, rec(2, "fail")[1], rec(2, "fail")[0], "full", DATE), "KIND")):
            with self.subTest(label), self.assertRaises(publish.PublishError) as cm:
                publish.publish(*args)
            self.assertEqual(cm.exception.kind, kind)
        spaced = raw.replace(b'"outcome"', b' "outcome"')
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.origin, hashlib.sha256(spaced).hexdigest() + ".json", spaced, "full", DATE)
        self.assertEqual(cm.exception.kind, "NOT_CANONICAL")
        self.assertFalse(os.path.exists(os.path.join(self.origin, "refs", "heads", "ci-evidence")))  # nothing was written

    def test_a_file_that_exists_with_other_content_is_corruption(self):
        raw, name = rec(1)
        publish.publish(self.origin, name, raw, "full", DATE)
        work = os.path.join(self.tmp.name, "tamper")
        git(self.tmp.name, "clone", "-q", "--branch", "ci-evidence", self.origin, work)
        with open(os.path.join(work, "evidence", "full", name), "wb") as fh:
            fh.write(b"{}")
        git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "tamper")
        git(work, "push", "-q", "origin", "HEAD:ci-evidence")
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.origin, name, raw, "full", DATE)
        self.assertEqual(cm.exception.kind, "CONFLICT")

    def test_a_clone_that_fails_for_another_reason_is_not_a_new_branch(self):
        raw, name = rec(1)
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(os.path.join(self.tmp.name, "does-not-exist.git"), name, raw, "full", DATE)
        self.assertEqual(cm.exception.kind, "CLONE")


class IndexTests(unittest.TestCase):
    H = "a" * 64
    G = "b" * 64

    def test_round_trip_and_order(self):
        data = publish.index_bytes(DATE, [(self.G, "full"), (self.H, "audit"), (self.H, "full"), (self.H, "full")])
        date, entries = publish.read_index(data)
        self.assertEqual((date, entries), (DATE, [(self.H, "audit"), (self.H, "full"), (self.G, "full")][:0] or entries))
        self.assertEqual(entries, sorted(set(entries), key=lambda e: (e[1], e[0])))
        self.assertEqual(len(entries), 3)

    def test_anything_else_is_refused(self):
        good = {"schema": publish.INDEX_SCHEMA, "date": DATE, "records": [{"hash": self.H, "kind": "full"}]}
        publish.read_index(canon.canonicalize(good))
        bad = {
            "another schema": dict(good, schema="x"), "an extra member": dict(good, extra=1), "a bad date": dict(good, date="20261020"),
            "a short hash": dict(good, records=[{"hash": "a" * 63, "kind": "full"}]),
            "a bad kind": dict(good, records=[{"hash": self.H, "kind": "delta"}]),
            "an extra entry member": dict(good, records=[{"hash": self.H, "kind": "full", "x": 1}]),
            "out of order": dict(good, records=[{"hash": self.G, "kind": "full"}, {"hash": self.H, "kind": "audit"}]),
            "repeated": dict(good, records=[{"hash": self.H, "kind": "full"}] * 2),
            "not an object": [1],
        }
        for label, doc in bad.items():
            with self.subTest(label), self.assertRaises(publish.PublishError):
                publish.read_index(canon.canonicalize(doc))
        with self.assertRaises(publish.PublishError):
            publish.read_index(b'{ "schema": "x" }')   # valid JSON, not canonical
        with self.assertRaises(publish.PublishError):
            publish.read_index(b"not json")


if __name__ == "__main__":
    unittest.main()
