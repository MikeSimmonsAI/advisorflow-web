#!/usr/bin/env bash
# Relay wake-up signal: update .relay/chatgpt-wakeup.json on branch
# "relay-signal" and keep ONE draft PR (relay-signal -> main) open, so every
# trusted terminal Claude status produces exactly one new PR activity event
# (a "synchronize") for ChatGPT Work. The PR is an event channel only - it is
# never merged and carries no code, secrets or customer data.
#
# Inputs (env): GH_TOKEN, GITHUB_REPOSITORY, RUNNER_TEMP, RELAY_DIR (this folder).
set -euo pipefail
PAYLOAD="$RUNNER_TEMP/relay-signal.json"
[ -f "$PAYLOAD" ] || { echo "relay signal: nothing to signal"; exit 0; }
GUARD="$RELAY_DIR/relay_guard.py"
AUTH="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)"
URL="https://github.com/${GITHUB_REPOSITORY}.git"
D="$RUNNER_TEMP/relay-signal-branch"

for attempt in 1 2 3; do
  rm -rf "$D"
  if git -c "http.https://github.com/.extraheader=$AUTH" clone -q --depth 1 --branch relay-signal "$URL" "$D" 2>/dev/null; then
    :
  else
    git -c "http.https://github.com/.extraheader=$AUTH" clone -q --depth 1 "$URL" "$D"
    git -C "$D" checkout -q -b relay-signal
  fi
  decision=$(python3 "$GUARD" should-signal "$PAYLOAD" "$D/.relay/chatgpt-wakeup.json" | sed -n 's/^signal=//p')
  if [ "$decision" != "true" ]; then
    echo "relay signal: already signalled for this run/status - no new event (dedupe)"
    exit 0
  fi
  mkdir -p "$D/.relay"
  cp "$PAYLOAD" "$D/.relay/chatgpt-wakeup.json"
  run_id=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['relay_run_id'], d['status'])" "$PAYLOAD")
  git -C "$D" add .relay/chatgpt-wakeup.json
  git -C "$D" -c user.name="relay-signal" -c user.email="relay-signal@users.noreply.github.com" \
      commit -q -m "relay signal: $run_id"
  if git -C "$D" -c "http.https://github.com/.extraheader=$AUTH" push -q origin HEAD:refs/heads/relay-signal; then
    break
  fi
  echo "relay signal: push raced (attempt $attempt), retrying"
  sleep $((attempt * 3))
done

pr=$(gh pr list --repo "$GITHUB_REPOSITORY" --head relay-signal --state open --json number --jq '.[0].number' || true)
if [ -z "$pr" ]; then
  gh pr create --repo "$GITHUB_REPOSITORY" --draft --base main --head relay-signal \
    --title "RELAY SIGNAL - ChatGPT wake-up channel (DO NOT MERGE)" \
    --body "Event channel for the automatic relay (issue #1). Each push here means a new terminal [RELAY:CLAUDE_STATUS] is waiting in issue #1. The only file is .relay/chatgpt-wakeup.json (run id, status, branch, time - no secrets, no customer data). Never merge this PR."
  echo "relay signal: opened the relay PR"
else
  echo "relay signal: updated relay PR #$pr"
fi
