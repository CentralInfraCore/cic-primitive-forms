"""Generate .github/workflows/p1-proof.yml from .github/workflows/p1.yml.

    python3 tools/proof-plan/gen_p1_proof.py            write the file
    python3 tools/proof-plan/gen_p1_proof.py --check    exit 1 when the file in the repository is not what the generator makes

p1-proof is the pipeline that runs NEXT TO p1 and, step by step, takes its decisions from the proof-aware planner. It is generated, not copied by hand,
so that the jobs it shares with p1 cannot drift from p1. This first step changes only what has to differ for two pipelines to live side by side:

* the workflow name;
* every job gets the display name `proof / <job>`: the checks of a workflow are named by the job alone (`rust`, `quality` ...) and the ruleset
  of `main` requires those names, so a second job called `quality` would be confusable with the required one;
* `workflow_call` goes (nightly.yml calls p1, not p1-proof);
* the `inherit` job asks about p1-proof.yml, not p1.yml;
* the jobs `rust` and `go` wait for `proof-plan`, fetch its plan and run their test step through `select_tests.py run` (step 4): the command of p1, minus
  the tests whose earlier result may stand. The plan skips nothing unless it is authorized (evidence and planner from main), so on a pull
  request they run exactly what p1 runs; without the plan (the planner failed, no artifact) too;
* the job `proof-compare` is added (step 5): after every other job of p1-proof it compares their conclusions with those of the p1 run of the same commit;
* the job `proof-plan` is added (step 2): the planner of proof-plan.yml, called as a reusable workflow. Nothing waits for it and it is not in
  the `needs` of `quality`: it reports, it decides nothing yet.

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


PLANNER_JOB = """
  proof-plan:
    name: "proof / proof-plan"
    uses: ./.github/workflows/proof-plan.yml
    permissions:
      contents: read
      actions: read   # the planner finds and downloads the evidence artifact of an earlier run
"""


DOWNLOAD = """      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        continue-on-error: true   # no plan: the test step runs the command of p1
        with:
          name: proof-evidence
          path: ${{ runner.temp }}/proof
"""
CHECKOUT = "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0\n"
TEST_STEPS = {
    "rust": ('      - run: cargo +"$RUST_TOOLCHAIN" test --locked\n        working-directory: rust\n',
             '      - run: python3 tools/proof-plan/select_tests.py run rust --plan "$RUNNER_TEMP/proof/plan.json" --repo . --rust-toolchain "$RUST_TOOLCHAIN"\n'),
    "go": ("      - run: go test -race -count=1 -rapid.checks=1000 ./...\n        working-directory: go\n",
           '      - run: python3 tools/proof-plan/select_tests.py run go --plan "$RUNNER_TEMP/proof/plan.json" --repo .\n'),
}


def job_block(text, job):
    """(start, end) of the text of a job: from its key to the next job key."""
    m = re.search(r"^  %s:\n" % re.escape(job), text, re.M)
    if not m:
        raise Unsupported("job %s not found" % job)
    n = re.search(r"^  [A-Za-z0-9_-]+:\s*$", text[m.end():], re.M)
    return m.start(), (m.end() + n.start() if n else len(text))


def wire_tests(text):
    for job, (old, new) in TEST_STEPS.items():
        a, b = job_block(text, job)
        block = text[a:b]
        for what, was, now in (("needs", "    needs: inherit\n", "    needs: [inherit, proof-plan]\n"), ("checkout", CHECKOUT, CHECKOUT + DOWNLOAD), ("test step", old, new)):
            # the first checkout of the job is the first step; a later one (the engine's) is another step
            if block.count(was) != (1 if what != "checkout" else block.count(was)) or block.count(was) < 1:
                raise Unsupported("job %s: expected exactly one %s, found %d" % (job, what, block.count(was)))
            if what == "checkout" and not block.split("    steps:\n", 1)[1].startswith(CHECKOUT):
                raise Unsupported("job %s: the first step is not the checkout" % job)
            block = block.replace(was, now, 1)
        text = text[:a] + block + text[b:]
    return text


COMPARE_JOB = """
  proof-compare:
    name: "proof / proof-compare"
    needs: [%s]
    if: ${{ always() && !cancelled() }}   # also when a job before it failed: that is what is compared
    runs-on: ubuntu-latest
    timeout-minutes: 40   # it waits for the p1 run of the same commit
    permissions:
      contents: read
      actions: read
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        continue-on-error: true
        with:
          name: proof-evidence
          path: ${{ runner.temp }}/proof
      - run: |
          python3 tools/proof-plan/compare_runs.py --repo "$GITHUB_REPOSITORY" --sha "$HEAD_SHA" --event "$EVENT" --own-run "$GITHUB_RUN_ID" \\
            --comparison "$RUNNER_TEMP/proof/comparison.json" | tee -a "$GITHUB_STEP_SUMMARY"
          exit "${PIPESTATUS[0]}"
        env:
          GH_TOKEN: ${{ github.token }}
          EVENT: ${{ github.event_name }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}   # the pull request run is named by the head, not by the merge commit
"""


def generate(src_text):
    t = once(src_text, "name: p1\n", "name: p1-proof\n" + HEADER)
    t, n = re.subn(r"  workflow_call:.*\n(?:    .*\n)+(?=  push:)", "", t)
    if n != 1:
        raise Unsupported("workflow_call block not found exactly once")
    t = once(t, "      caller: p1\n", "      caller: p1-proof\n")
    t = once(t, "      workflows: p1.yml,corpus.yml\n", "      workflows: p1-proof.yml,corpus.yml\n")
    t = prefix_job_names(wire_tests(t))
    ids = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", t[t.index("\njobs:"):], re.M)
    return t.rstrip("\n") + "\n" + PLANNER_JOB + COMPARE_JOB % ", ".join(ids + ["proof-plan"])


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
