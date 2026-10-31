"""The Merkle key changes for exactly the nodes that can reach a changed declaration, and for no other.

    python3 -m unittest lab/measure/test_merkle.py            (a synthetic graph)
    MERKLE_GRAPH=typed.json python3 -m unittest lab/measure/test_merkle.py   (also every node of a real graph, one at a time)
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import merkle  # noqa: E402


def graph(edges, srcs=None):
    ids = sorted(edges)  # a dep that is not a key is outside the graph (an external id)
    return {"nodes": [{"id": i, "src_sha256": (srcs or {}).get(i, "s-" + i), "deps": sorted(edges.get(i, ()))} for i in ids]}


def reaching(g, target):
    deps = {n["id"]: set(n["deps"]) for n in g["nodes"]}
    out = set()
    for n in deps:
        seen, stack = {n}, [n]
        while stack:
            for d in deps.get(stack.pop(), ()):
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        if target in seen:
            out.add(n)
    return out


def check_every_node(test, g):
    base, _ = merkle.keys(g)
    for i, n in enumerate(g["nodes"]):
        changed = copy.deepcopy(g)
        changed["nodes"][i]["src_sha256"] = "changed"
        after, _ = merkle.keys(changed)
        moved = {k for k in base if base[k] != after[k]}
        test.assertEqual(moved, reaching(g, n["id"]), n["id"])


class MerkleTest(unittest.TestCase):
    def test_chain_cycle_and_diamond(self):
        g = graph({"t1": {"a"}, "t2": {"b"}, "a": {"c", "x"}, "b": {"c"}, "c": {"d"}, "d": {"c", "e"}, "e": set(), "lonely": set()})
        check_every_node(self, g)

    def test_a_cycle_shares_one_key(self):
        k, size = merkle.keys(graph({"a": {"b"}, "b": {"a"}, "c": {"a"}}))
        self.assertEqual(k["a"], k["b"])
        self.assertNotEqual(k["a"], k["c"])
        self.assertEqual(size["a"], 2)

    def test_an_edge_change_moves_the_key(self):
        before, _ = merkle.keys(graph({"a": {"b"}, "b": set(), "c": set()}))
        after, _ = merkle.keys(graph({"a": {"c"}, "b": set(), "c": set()}))
        self.assertNotEqual(before["a"], after["a"])
        self.assertEqual(before["b"], after["b"])

    def test_external_ids_count(self):
        before, _ = merkle.keys(graph({"a": {"fmt.Sprintf"}}))
        after, _ = merkle.keys(graph({"a": {"fmt.Errorf"}}))
        self.assertNotEqual(before["a"], after["a"])

    @unittest.skipUnless(os.environ.get("MERKLE_GRAPH"), "MERKLE_GRAPH not set")
    def test_every_node_of_a_real_graph(self):
        import json
        check_every_node(self, json.load(open(os.environ["MERKLE_GRAPH"])))


if __name__ == "__main__":
    unittest.main()
