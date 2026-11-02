#!/usr/bin/env bash
# Pull the app's secrets from AWS Secrets Manager into a root-only env file
# on tmpfs (/run is RAM-backed, cleared on reboot). Note that Docker then
# copies env_file values into each container's config under /var/lib/docker
# (root-only, on the encrypted EBS volume) - so root or a disk snapshot can
# read them. Authenticates with the EC2 instance role via IMDS -
# there are no AWS access keys on the box, in the images, or in this repo.
#
# Expected secret: a JSON object of KEY -> value, e.g.
#   {"ANTHROPIC_API_KEY": "...", "SUPABASE_KEY": "...", "GMAIL_APP_PASSWORD": "..."}
set -euo pipefail

# shellcheck disable=SC1091
source /etc/verity/config.env
: "${AWS_REGION:?set in /etc/verity/config.env}" "${SECRET_ID:?set in /etc/verity/config.env}"

umask 077
install -d -m 700 /run/verity
tmp=$(mktemp /run/verity/secrets.XXXXXX)
trap 'rm -f "$tmp"' EXIT

aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$SECRET_ID" \
    --query SecretString --output text \
  | jq -er 'to_entries[]
      | if (.key | test("^[A-Z][A-Z0-9_]*$")) and (.value | type == "string") and (.value | test("\n") | not)
        then "\(.key)=\(.value)"
        else error("secret entries must be UPPER_CASE keys with single-line string values") end' \
  > "$tmp"

mv "$tmp" /run/verity/secrets.env
trap - EXIT
echo "fetch-secrets: wrote $(wc -l < /run/verity/secrets.env) entries (values not shown)"
