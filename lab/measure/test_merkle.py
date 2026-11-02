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


class Deterministic(unittest.TestCase):
    """The key is a function of the graph, not of the process: the same graph gives the same keys under any hash seed."""

    def test_keys_do_not_depend_on_the_hash_seed(self):
        import subprocess
        # a cycle with several internal edges from one member: the case in which the order of a set decided the digest
        code = (
            "import sys, json, hashlib; sys.path.insert(0, %r); import merkle;"
            "g = {'nodes': [{'id': i, 'src_sha256': 's' + i, 'deps': d} for i, d in "
            "(('a', ['b', 'c', 'd']), ('b', ['a', 'c', 'd']), ('c', ['a', 'b', 'd']), ('d', ['a', 'b', 'c', 'x']), ('t', ['a']))]};"
            "k = merkle.keys(g)[0]; print(hashlib.sha256(json.dumps(sorted(k.items())).encode()).hexdigest())"
        ) % os.path.dirname(os.path.abspath(__file__))
        outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                               env=dict(os.environ, PYTHONHASHSEED=str(seed))).stdout.strip() for seed in range(8)}
        self.assertEqual(len(outs), 1, "the keys of one graph differ between processes: %s" % sorted(outs))
