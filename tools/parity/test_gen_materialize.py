"""The generator of the flat-materialization differential is reproducible: the same seed gives the same file whatever the hash seed of the Python process is
(the field names are drawn from a list and written in a shuffled order: the shuffle must see the same sequence)."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gen_materialize.py")


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


if __name__ == "__main__":
    unittest.main()
