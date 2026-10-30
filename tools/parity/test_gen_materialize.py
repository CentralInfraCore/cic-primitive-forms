"""The generator of the flat-materialization differential is reproducible: the same seed gives the same file whatever the hash seed of the Python process is
(the field names are drawn from a list and written in a shuffled order: the shuffle must see the same sequence)."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gen_materialize.py")
sys.path.insert(0, os.path.dirname(GEN))


def digest(hash_seed, seed, count=3000):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "in.jsonl")
        env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
        subprocess.run([sys.executable, GEN, out, "--count", str(count), "--seed", str(seed)], check=True, env=env)
        with open(out, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()


class Reproducible(unittest.TestCase):
    def test_the_same_seed_gives_the_same_file_under_different_hash_seeds(self):
        self.assertEqual(len({digest(h, 7) for h in (0, 1, 2, 12345)}), 1)

    def test_another_seed_gives_another_file(self):
        self.assertNotEqual(digest(0, 7), digest(0, 8))


class Coverage(unittest.TestCase):
    def test_the_generated_names_include_a_pair_whose_utf16_order_differs_from_the_byte_order(self):
        """The byte order of valid UTF-8 is the code point order; the UTF-16 order differs for a character from U+E000 to U+FFFF against one beyond U+FFFF. A case that holds
        such a pair is what makes a writer that sorts by UTF-16 code unit visible."""
        import gen_materialize as gm

        def utf16(k):
            return k.encode("utf-16-be")

        pairs = [(a, b) for a in gm.NAMES for b in gm.NAMES if a.encode("utf-8") < b.encode("utf-8") and utf16(a) > utf16(b)]
        self.assertTrue(pairs, "no two names sort differently by UTF-16 and by byte")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "in.jsonl")
            subprocess.run([sys.executable, GEN, out, "--count", "3000", "--seed", "7"], check=True)
            import json
            hits = 0
            for line in open(out, encoding="utf-8"):
                c = json.loads(line)
                if c["want"][0] != "ok":
                    continue
                keys = c["keys"]
                if keys != sorted(keys, key=utf16):
                    hits += 1
        self.assertGreater(hits, 20, "too few accepted cases whose order of the names depends on the choice between byte and UTF-16 order")


class Comparator(unittest.TestCase):
    def test_a_repeated_id_is_refused_by_the_loader(self):
        import compare_materialize as cm
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "x.jsonl")
            with open(f, "w", encoding="utf-8") as h:
                h.write('{"id": 1, "r": ["err", "set", "a"]}\n{"id": 2, "r": ["err", "set", "a"]}\n{"id": 1, "r": ["err", "set", "b"]}\n')
            with self.assertRaises(cm.DuplicateId):
                cm.load(f)


if __name__ == "__main__":
    unittest.main()
