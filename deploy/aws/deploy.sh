#!/usr/bin/env bash
# Deploy one CI-built commit:  /opt/verity/deploy.sh <40-char commit sha>
#
# Pulls first (a failed pull changes nothing), then swaps IMAGE_TAG in
# /etc/verity/config.env, restarts verity.service and waits for both
# containers' healthchecks. If they aren't healthy in time, it puts the
# previous tag back and restarts again, so a bad image never stays live.
#
# Run as root - by hand, or by deploy-poll.sh when the `production` git tag
# moves to a new commit (deploy/ec2.md, "Automatic deploys").
set -euo pipefail

new=${1:-}
[[ $new =~ ^[0-9a-f]{40}$ ]] || { echo "usage: $0 <40-char commit sha>" >&2; exit 2; }

config=/etc/verity/config.env
compose=(docker compose -f /opt/verity/docker-compose.yml --env-file "$config")
health_timeout=${HEALTH_TIMEOUT:-180}  # seconds to wait for both healthchecks

# One deploy at a time.
exec 9>/run/verity-deploy.lock
flock -n 9 || { echo "deploy: another deploy is already running" >&2; exit 1; }

old=$(sed -n 's/^IMAGE_TAG=//p' "$config")
if [[ $new == "$old" ]]; then
  echo "deploy: $new is already live"
  exit 0
fi
echo "deploy: $old -> $new"

# A shell variable overrides --env-file, so this pulls the new images
# without touching the live config yet.
IMAGE_TAG=$new "${compose[@]}" pull --quiet

set_tag() { sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$1/" "$config"; }

wait_healthy() {
  local deadline=$((SECONDS + health_timeout)) ids status=""
  while (( SECONDS < deadline )); do
    ids=$("${compose[@]}" ps -q)
    # shellcheck disable=SC2086 # word-splitting the ids is intended
    status=$(docker inspect --format '{{.State.Health.Status}}' $ids 2>/dev/null | sort -u | tr '\n' ' ')
    if [[ $status == "healthy " && $(wc -w <<<"$ids") -eq 2 ]]; then
      return 0
    fi
    sleep 5
  done
  echo "deploy: containers not healthy after ${health_timeout}s (${status:-none})" >&2
  return 1
}

set_tag "$new"
systemctl restart verity
if wait_healthy; then
  echo "deploy: $new is live"
  exit 0
fi

echo "deploy: rolling back to $old" >&2
"${compose[@]}" logs --tail 40 >&2 || true
set_tag "$old"
systemctl restart verity
wait_healthy && echo "deploy: rolled back to $old" >&2
exit 1
