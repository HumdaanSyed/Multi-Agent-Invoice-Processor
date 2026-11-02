#!/usr/bin/env bash
# Pull-based release check, run every 2 minutes by verity-deploy.timer.
#
# Reads which commit the `production` git tag points at (moved by the
# approval-gated `release` job in .github/workflows/docker-build.yml) and,
# if it differs from the live IMAGE_TAG, runs deploy.sh for it. The repo is
# public, so this needs no credentials - and GitHub holds no access to AWS.
#
# A commit that fails its health check is remembered and not retried, so a
# bad release costs one rollback, not one every 2 minutes. Moving the tag to
# a different commit (a fix, or the previous release) clears that.
set -euo pipefail

# shellcheck disable=SC1091
source /etc/verity/config.env
repo=${DEPLOY_REPO:-https://github.com/HumdaanSyed/Multi-Agent-Invoice-Processor.git}
failed_file=/var/lib/verity/failed-release

want=$(git ls-remote --tags "$repo" refs/tags/production | cut -f1)
if [[ -z $want ]]; then
  exit 0  # no release yet
fi
if [[ ! $want =~ ^[0-9a-f]{40}$ ]]; then
  echo "deploy-poll: unexpected tag value, ignoring" >&2
  exit 1
fi
if [[ $want == "${IMAGE_TAG:-}" ]]; then
  exit 0  # already live
fi
if [[ -f $failed_file && $(<"$failed_file") == "$want" ]]; then
  exit 0  # already failed once; waiting for a new release
fi

echo "deploy-poll: production tag is $want, live is ${IMAGE_TAG:-none}"
if /opt/verity/deploy.sh "$want"; then
  rm -f "$failed_file"
else
  install -d -m 755 /var/lib/verity
  echo "$want" > "$failed_file"
  echo "deploy-poll: $want failed and was rolled back; not retrying it" >&2
  exit 1
fi
