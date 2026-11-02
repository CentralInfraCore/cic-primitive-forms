"""Generate .github/workflows/p1-proof.yml from .github/workflows/p1.yml.

    python3 tools/proof-plan/gen_p1_proof.py            write the file
    python3 tools/proof-plan/gen_p1_proof.py --check    exit 1 when the file in the repository is not what the generator makes

p1-proof is the pipeline that runs NEXT TO p1 and, step by step, takes its decisions from the proof-aware planner. It is generated, not copied by hand,
so that the jobs it shares with p1 cannot drift from p1. This first step changes only what has to differ for two pipelines to live side by side:

* the workflow name;
* every job gets the display name `proof / <job>`: the checks of a workflow are named by the job alone (`rust`, `quality` ...) and the ruleset
  of `main` requires those names, so a second job called `quality` would be confusable with the required one;
* `workflow_call` goes (nightly.yml calls p1, not p1-proof);
* the `inherit` job asks about p1-proof.yml, not p1.yml.

Each substitution must match exactly once, otherwise the generator stops: a change in p1.yml that the generator does not understand is an
error, not a silently wrong copy. The job graph (ids, needs, if) is untouched, which the test checks.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, ".github", "workflows", "p1.yml")
DST = os.path.join(ROOT, ".github", "workflows", "p1-proof.yml")
PREFIX = "proof / "
HEADER = ("# GENERATED from p1.yml by tools/proof-plan/gen_p1_proof.py: do not edit; change p1.yml or the generator and run it again.\n"
          "# p1-proof runs next to p1 (which stays as it is) and is built, step by step, on the proof-aware planner (tools/proof-plan/README.md).\n")


class Unsupported(Exception):
    pass


def once(text, old, new):
    if text.count(old) != 1:
        raise Unsupported("expected exactly one %r in p1.yml, found %d" % (old, text.count(old)))
    return text.replace(old, new)


def prefix_job_names(text):
    """`name:` of every job (a key at 4 spaces inside `jobs:`) gets the prefix; a job with no name gets one. The name can be anywhere in
    the block of the job, so the whole block is searched."""
    lines = text.split("\n")
    start = lines.index("jobs:")
    keys = [i for i in range(start + 1, len(lines)) if re.match(r"^  [A-Za-z0-9_-]+:\s*$", lines[i])]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\S", lines[i])), len(lines))
    out = lines[: start + 1]
    for n, k in enumerate(keys):
        stop = keys[n + 1] if n + 1 < len(keys) else end
        block = lines[k:stop]
        names = [i for i, l in enumerate(block) if re.match(r"^    name:\s*", l)]
        if len(names) > 1:
            raise Unsupported("more than one name in the job " + block[0])
        if names:
            v = re.match(r"^    name:\s*(.*)$", block[names[0]]).group(1).strip()
            if v[:1] in "'\"":
                v = v[0] + PREFIX + v[1:]
            elif '"' in v:
                raise Unsupported("job name with a quote: " + v)
            else:
                v = '"' + PREFIX + v + '"'
            block[names[0]] = "    name: " + v
        else:
            block.insert(1, '    name: "%s%s"' % (PREFIX, block[0].strip()[:-1]))
        out += block
    return "\n".join(out + lines[end:])


def generate(src_text):
    t = once(src_text, "name: p1\n", "name: p1-proof\n" + HEADER)
    t, n = re.subn(r"  workflow_call:.*\n(?:    .*\n)+(?=  push:)", "", t)
    if n != 1:
        raise Unsupported("workflow_call block not found exactly once")
    t = once(t, "      caller: p1\n", "      caller: p1-proof\n")
    t = once(t, "      workflows: p1.yml,corpus.yml\n", "      workflows: p1-proof.yml,corpus.yml\n")
    return prefix_job_names(t)


def main(argv):
    out = generate(open(SRC).read())
    if "--check" in argv:
        have = open(DST).read() if os.path.exists(DST) else None
        if have != out:
            print("p1-proof.yml is not what gen_p1_proof.py makes from p1.yml: run it and commit the result", file=sys.stderr)
            return 1
        return 0
    open(DST, "w").write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
