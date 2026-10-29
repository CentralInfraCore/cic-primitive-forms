# SPDX-License-Identifier: Apache-2.0
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inherited_tree as it  # noqa: E402

SHA, HEAD = "a" * 40, "b" * 40


def pr(**kw):
    p = {"number": 7, "merged_at": "2026-10-29T21:47:00Z", "merge_commit_sha": SHA, "base": {"ref": "main"}, "head": {"sha": HEAD}}
    p.update(kw)
    return p


def run(n=1, status="completed", conclusion="success", head=HEAD, event="pull_request", attempt=1):
    return {"run_number": n, "run_attempt": attempt, "status": status, "conclusion": conclusion, "head_sha": head, "event": event}


def runs(**by_workflow):
    return lambda w, head: by_workflow.get(w, [])


def trees(**kw):
    t = {SHA: "t1", HEAD: "t1"}
    t.update(kw)
    return lambda rev: t.get(rev)


GOOD = runs(**{"p1.yml": [run()], "corpus.yml": [run()]})


def decide(pulls=None, tree=None, rs=None, workflows=("p1.yml", "corpus.yml")):
    return it.decide(SHA, [pr()] if pulls is None else pulls, tree or trees(), rs or GOOD, list(workflows))[0]


class Inherited(unittest.TestCase):
    def test_the_same_tree_after_a_green_pull_request_is_inherited(self):
        self.assertTrue(decide())

    def test_a_tree_that_differs_is_not(self):
        self.assertFalse(decide(tree=trees(**{SHA: "t2"})))

    def test_a_tree_that_cannot_be_read_is_not(self):
        self.assertFalse(decide(tree=trees(**{HEAD: None})))
        self.assertFalse(decide(tree=trees(**{SHA: None})))

    def test_a_push_that_is_no_pull_request_is_not(self):
        self.assertFalse(decide(pulls=[]))

    def test_a_pull_request_that_was_not_merged_into_this_commit_is_not(self):
        self.assertFalse(decide(pulls=[pr(merged_at=None)]))
        self.assertFalse(decide(pulls=[pr(merge_commit_sha="c" * 40)]))
        self.assertFalse(decide(pulls=[pr(base={"ref": "release"})]))

    def test_two_pull_requests_for_one_commit_are_not_decided(self):
        self.assertFalse(decide(pulls=[pr(), pr(number=8, head={"sha": "d" * 40})]))

    def test_a_workflow_that_failed_or_was_cancelled_is_not(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out", None):
            self.assertFalse(decide(rs=runs(**{"p1.yml": [run(conclusion=conclusion)], "corpus.yml": [run()]})), conclusion)

    def test_a_workflow_that_is_still_running_is_not(self):
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run(status="in_progress", conclusion=None)], "corpus.yml": [run()]})))

    def test_a_workflow_with_no_run_is_not(self):
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run()]})))

    def test_the_newest_run_decides_not_the_best(self):
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run(1, conclusion="success"), run(2, conclusion="failure")], "corpus.yml": [run()]})))
        self.assertTrue(decide(rs=runs(**{"p1.yml": [run(1, conclusion="failure"), run(2, conclusion="success")], "corpus.yml": [run()]})))
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run(2, conclusion="success"), run(2, conclusion="failure", attempt=2)], "corpus.yml": [run()]})))

    def test_a_run_of_another_head_or_event_does_not_count(self):
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run(head="e" * 40)], "corpus.yml": [run()]})))
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run(event="push")], "corpus.yml": [run()]})))

    def test_every_workflow_named_must_have_succeeded(self):
        self.assertFalse(decide(rs=runs(**{"p1.yml": [run()], "corpus.yml": [run(conclusion="failure")]})))
        self.assertTrue(decide(rs=runs(**{"p1.yml": [run()]}), workflows=("p1.yml",)))


if __name__ == "__main__":
    unittest.main()
