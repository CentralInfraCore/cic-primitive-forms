#!/bin/bash
# The repository settings the evidence workflow needs (docs/EVIDENCE-SETUP.md). THE OWNER RUNS THIS; nothing else does.
#
#   apply.sh                       print what would be done (nothing is changed)
#   apply.sh --apply               do it
#
# Needs: gh, authenticated as an administrator of the repository; jq; and the GitHub App already created and installed on this
# repository (APP_SLUG, CLIENT_ID and KEY_FILE below). It never sets EVIDENCE_ENABLED: that is the last step, after the probes.
set -euo pipefail
REPO=${REPO:-CentralInfraCore/cic-primitive-forms}
APP_SLUG=${APP_SLUG:-cic-evidence-writer}
: "${CLIENT_ID:?the Client ID of the App (Settings > Developer settings > GitHub Apps > the App > General)}"
: "${KEY_FILE:?the private key (.pem) generated for the App}"
HERE=$(cd "$(dirname "$0")" && pwd)
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
PIN=0; [ "${2:-}" = "--pin-actions" ] && PIN=1

run() { echo "+ $*"; if [ "$APPLY" = 1 ]; then "$@"; fi; }

# 0. the precondition that makes the environment worth anything: any workflow on main can use the App's credentials, so a change to
# main (and to .github/workflows) has to be reviewed. This refuses to go on while main takes pushes without a pull request.
RULES=$(gh api "repos/$REPO/rules/branches/main" --jq '[.[].type] | join(",")' 2>/dev/null || true)
case ",$RULES," in
  *,pull_request,*) echo "main requires a pull request: ok" ;;
  *) echo "main does NOT require a pull request (rules: '${RULES:-none}')."
     echo "Any workflow pushed to main could then use the App's credentials. Protect main first (and review .github/workflows)."
     [ "${ALLOW_UNPROTECTED_MAIN:-0}" = 1 ] || exit 1
     echo "ALLOW_UNPROTECTED_MAIN=1: going on with an unprotected main, on your decision." ;;
esac

APP_ID=$(gh api "/apps/$APP_SLUG" --jq .id)
echo "the App $APP_SLUG has the id $APP_ID"
INSTALLED=$(gh api "repos/$REPO/installation" --jq '.app_slug' 2>/dev/null || true)
[ "$INSTALLED" = "$APP_SLUG" ] || { echo "the App $APP_SLUG is not installed on $REPO (found: '${INSTALLED:-none}')"; exit 1; }

# 1. the environment: only refs/heads/main may use the App's credentials
run gh api -X PUT "repos/$REPO/environments/evidence" --input - <<< '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
run gh api -X POST "repos/$REPO/environments/evidence/deployment-branch-policies" -f name=main -f type=branch || true

# 2. its credentials
run gh variable set EVIDENCE_APP_CLIENT_ID --env evidence --repo "$REPO" --body "$CLIENT_ID"
if [ "$APPLY" = 1 ]; then gh secret set EVIDENCE_APP_PRIVATE_KEY --env evidence --repo "$REPO" < "$KEY_FILE"; else echo "+ gh secret set EVIDENCE_APP_PRIVATE_KEY --env evidence --repo $REPO < \$KEY_FILE"; fi

# 3. the ruleset: nobody but the App creates, updates, deletes or force-pushes ci-evidence (and the probe branch)
BODY=$(jq --argjson id "$APP_ID" '.bypass_actors[0].actor_id = $id' "$HERE/ruleset-ci-evidence.json")
run gh api -X POST "repos/$REPO/rulesets" --input - <<< "$BODY"

# 4. optional: have GitHub itself refuse an action that is not pinned to a full commit
if [ "$PIN" = 1 ]; then
  CUR=$(gh api "repos/$REPO/actions/permissions")
  run gh api -X PUT "repos/$REPO/actions/permissions" --input - <<< "$(jq '{enabled: .enabled, allowed_actions: .allowed_actions, sha_pinning_required: true}' <<< "$CUR")"
fi

echo
echo "Next: run the workflow evidence-probes by hand on main. Only when it is green:  gh variable set EVIDENCE_ENABLED --repo $REPO --body true"
