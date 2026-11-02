"""The mutation part of the p1-proof workflow (imported by gen_p1_proof.py): the job that predicts which verdicts may stand and the changes to
`quality-mutation-rust` that leave the carried mutants out of its run and put their earlier outcomes into its report. See gen_p1_proof.py."""

MUTPLAN_JOB = r"""
  proof-mutplan:
    name: "proof / proof-mutplan"
    needs: scope
    if: ${{ !cancelled() }}
    runs-on: ubuntu-latest
    timeout-minutes: 40
    continue-on-error: true   # a prediction: it never fails the pipeline, and without it every mutant runs
    permissions:
      contents: read
      actions: read
    env:
      GH_TOKEN: ${{ github.token }}
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
        with:
          fetch-depth: 0
      - uses: ./.github/actions/upstream-pinned
      - uses: actions/setup-go@40f1582b2485089dde7abd97c1529aa768e1baff # v5.6.0
        with:
          go-version: "1.27.2"
          cache-dependency-path: tools/proof-plan/extractor/scipdeps/go.sum
      - run: rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal --component rust-analyzer
      - name: Tool, cargo-mutants (pinned)
        uses: ./.github/actions/cached-tool
        with:
          kind: cargo
          name: cargo-mutants
          spec: cargo-mutants@27.1.0
      - name: The extractor that reads the graph (reproducible build)
        env:
          CGO_ENABLED: "0"
        run: (cd tools/proof-plan/extractor/scipdeps && go build -trimpath -buildvcs=false -o "$RUNNER_TEMP/scipdeps" .)
      - name: The last nightly FULL on main
        id: nightly
        run: |
          RUN=$(gh run list --workflow nightly.yml --branch main --status success --limit 1 --json databaseId,headSha -q '.[0] | "\(.databaseId) \(.headSha)"')
          set -- $RUN; echo "id=$1" >> "$GITHUB_OUTPUT"; echo "sha=$2" >> "$GITHUB_OUTPUT"
          if [ -z "$1" ]; then echo "No successful nightly on main: no prediction."; exit 0; fi
          mkdir -p "$RUNNER_TEMP/nightly"
          # rust-mutants-N holds outcomes.json; rust-mutants-log-N holds the same file again, which these patterns leave out
          for pat in "rust-mutants-?" "rust-mutants-??" "env-rust-mutants-0"; do gh run download "$1" --repo "$GITHUB_REPOSITORY" -p "$pat" -D "$RUNNER_TEMP/nightly" || true; done
          git worktree add --detach "$RUNNER_TEMP/prevtree" "$2"
      - name: Which mutant verdicts may stand
        if: ${{ steps.nightly.outputs.id != '' }}
        env:
          EVENT: ${{ github.event_name }}
          NIGHTLY_ID: ${{ steps.nightly.outputs.id }}
          NIGHTLY_SHA: ${{ steps.nightly.outputs.sha }}
        run: |
          (cd rust && cargo +"$RUST_TOOLCHAIN" mutants --list --json) > "$RUNNER_TEMP/all.json"
          # the verdicts are written out as carried only on main (the planner and the earlier run are then main's); anywhere else it is a report
          AUTH=""; if [ "$GITHUB_REF" = refs/heads/main ] && [ "$EVENT" != pull_request ]; then AUTH="--authorized"; fi
          { echo '### Mutation carry-over'; echo "Earlier FULL: nightly run $NIGHTLY_ID at \`${NIGHTLY_SHA:0:7}\`"; echo '```'
            python3 tools/proof-plan/mutcarry.py plan --prev-repo "$RUNNER_TEMP/prevtree" --cur-repo . --scipdeps "$RUNNER_TEMP/scipdeps" \
              --rust-toolchain "$RUST_TOOLCHAIN" --outcomes "$RUNNER_TEMP/nightly" --prev-env "$RUNNER_TEMP/nightly/env-rust-mutants-0/env.json" \
              --list "$RUNNER_TEMP/all.json" --source "$NIGHTLY_ID" $AUTH --out "$RUNNER_TEMP/carried.json"
            echo '```'; } | tee -a "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        if: ${{ steps.nightly.outputs.id != '' }}
        with:
          name: proof-carried
          path: ${{ runner.temp }}/carried.json
          if-no-files-found: warn
"""

OLD = {
    "needs": "    needs: [scope, plan]\n",
    "if": "    if: ${{ needs.scope.outputs.mode == 'full' && needs.scope.outputs.engine == 'classic' }}\n",
    "verify": '          python3 tools/shard_plan.py verify --names "$RUNNER_TEMP/q/names.json" --selected "$RUNNER_TEMP/q/selected.json"\n',
    "run": '          (ulimit -v 6291456; cd rust && CIC_FORMS_ROOT="$GITHUB_WORKSPACE" cargo +"$RUST_TOOLCHAIN" mutants -F "$(cat "$RUNNER_TEMP/q/re.txt")" --output "$RUNNER_TEMP/q/mut-rust" --jobs 1 --timeout 120 -- --tests) || true\n',
    "upload": "      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2\n        with:\n          name: rust-mutants-${{ matrix.shard }}\n",
}

NEW = {
    "needs": "    needs: [scope, plan, proof-mutplan]\n",
    # the old condition, but not skipped when the prediction job failed or was skipped (then every mutant runs); the plan is still required
    "if": "    if: ${{ !cancelled() && needs.plan.result == 'success' && needs.scope.outputs.mode == 'full' && needs.scope.outputs.engine == 'classic' }}\n",
    "verify": OLD["verify"] + r"""      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        continue-on-error: true   # no prediction: every mutant of the shard runs
        with:
          name: proof-carried
          path: ${{ runner.temp }}/carried
      - name: Mutants that carry an earlier verdict leave this shard's run
        run: |
          C="$RUNNER_TEMP/carried/carried.json"; : > "$RUNNER_TEMP/q/ex.txt"
          if [ -f "$C" ]; then
            python3 tools/proof-plan/mutcarry.py exclude --carried "$C" --names "$RUNNER_TEMP/q/names.json" --out "$RUNNER_TEMP/q/ex.txt"
            python3 tools/proof-plan/mutcarry.py rest --carried "$C" --names "$RUNNER_TEMP/q/names.json" --out "$RUNNER_TEMP/q/rest.json"
          fi
          if [ -s "$RUNNER_TEMP/q/ex.txt" ]; then
            # what runs must be exactly the planned mutants minus the carried ones, or the shard stops here
            (cd rust && cargo +"$RUST_TOOLCHAIN" mutants --list --json -F "$(cat "$RUNNER_TEMP/q/re.txt")" -E "$(cat "$RUNNER_TEMP/q/ex.txt")") > "$RUNNER_TEMP/q/selected-rest.json"
            python3 tools/shard_plan.py verify --names "$RUNNER_TEMP/q/rest.json" --selected "$RUNNER_TEMP/q/selected-rest.json"
          else
            echo "No mutant of this shard is carried: all of them run."
          fi
""",
    "run": r"""          EXARGS=(); [ -s "$RUNNER_TEMP/q/ex.txt" ] && EXARGS=(-E "$(cat "$RUNNER_TEMP/q/ex.txt")")
          (ulimit -v 6291456; cd rust && CIC_FORMS_ROOT="$GITHUB_WORKSPACE" cargo +"$RUST_TOOLCHAIN" mutants -F "$(cat "$RUNNER_TEMP/q/re.txt")" "${EXARGS[@]}" --output "$RUNNER_TEMP/q/mut-rust" --jobs 1 --timeout 120 -- --tests) || true
""",
    "upload": r"""      - name: The report of the shard holds the carried verdicts too
        run: |
          O="$RUNNER_TEMP/q/mut-rust/mutants.out/outcomes.json"; C="$RUNNER_TEMP/carried/carried.json"
          if [ -f "$C" ] && [ -s "$RUNNER_TEMP/q/ex.txt" ]; then
            python3 tools/proof-plan/mutcarry.py merge --carried "$C" --names "$RUNNER_TEMP/q/names.json" --fresh "$O" --out "$O.merged"
            mv "$O.merged" "$O"
          fi
""" + OLD["upload"],
}


def wire(text, job_block, Unsupported):
    a, b = job_block(text, "quality-mutation-rust")
    block = text[a:b]
    for k, old in OLD.items():
        if block.count(old) != 1:
            raise Unsupported("quality-mutation-rust: expected exactly one %s, found %d" % (k, block.count(old)))
        block = block.replace(old, NEW[k])
    return text[:a] + block + text[b:]
