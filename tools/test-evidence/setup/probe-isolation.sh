#!/bin/bash
# Probe: a workflow on a branch other than main must NOT get the secrets of the environment `evidence`. THE OWNER RUNS THIS, after
# apply.sh --apply and before EVIDENCE_ENABLED. It pushes a throwaway branch with a one-job workflow that asks for the environment,
# waits for its run and requires that the job did not run (the environment's branch policy refuses it). The branch is deleted at
# the end. Needs gh authenticated as an administrator, and git with push access.
set -euo pipefail
REPO=${REPO:-CentralInfraCore/cic-primitive-forms}
BR=ci-evidence-iso-probe-$(date -u +%Y%m%d%H%M%S)
TMP=$(mktemp -d)
cleanup() { git -C "$TMP" push -q origin ":$BR" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
git clone -q --depth 1 "https://github.com/$REPO.git" "$TMP"
cd "$TMP"
git checkout -q -b "$BR"
mkdir -p .github/workflows
cat > .github/workflows/iso-probe.yml <<'YML'
name: iso-probe
on:
  push:
    branches: ['ci-evidence-iso-probe-**']
permissions: {}
jobs:
  take:
    runs-on: ubuntu-latest
    environment: evidence
    steps:
      - run: echo "REACHED: a branch other than main got the environment"
YML
git add .github/workflows/iso-probe.yml
git -c user.name=probe -c user.email=probe@invalid -c core.hooksPath=/dev/null -c commit.gpgsign=false commit -q -m "isolation probe"
git push -q origin "$BR"
for i in $(seq 1 40); do
  RUN=$(gh run list --repo "$REPO" --branch "$BR" --workflow iso-probe.yml --json databaseId,status,conclusion --jq '.[0]' 2>/dev/null || true)
  [ -n "$RUN" ] && [ "$(jq -r .status <<< "$RUN")" = completed ] && break
  sleep 10
done
[ -n "${RUN:-}" ] || { echo "PROBE INCONCLUSIVE: no run appeared"; exit 2; }
ID=$(jq -r .databaseId <<< "$RUN")
JOB_STEPS=$(gh run view "$ID" --repo "$REPO" --json jobs --jq '[.jobs[].steps[]?.name] | length')
CONCL=$(jq -r .conclusion <<< "$RUN")
if [ "$CONCL" = success ] || [ "$JOB_STEPS" != 0 ]; then
  echo "PROBE FAILED: the job on $BR ran (conclusion $CONCL, $JOB_STEPS steps): the environment does not stop other branches"
  exit 1
fi
echo "PROBE PASSED: the job on $BR did not run (conclusion $CONCL)"
