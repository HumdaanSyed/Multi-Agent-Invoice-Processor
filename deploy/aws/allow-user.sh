#!/usr/bin/env bash
# Manage who besides the owner may sign in: ALLOWED_USER_IDS in
# /etc/verity/config.env (app/auth.py and web/lib/supabase/env.ts read it).
#
#   allow-user.sh list
#   allow-user.sh add <supabase-user-uid>
#   allow-user.sh remove <supabase-user-uid>
#
# The owner (OWNER_USER_ID) is always allowed and isn't managed here. add and
# remove restart verity.service so both containers load the new list - a few
# seconds of downtime. A removed user is locked out on their next request.
set -euo pipefail

config=${VERITY_CONFIG:-/etc/verity/config.env}
uid_re='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

current() { sed -n 's/^ALLOWED_USER_IDS=//p' "$config" | tr ',' '\n' | sed 's/[[:space:]]//g; /^$/d'; }

# Replace the ALLOWED_USER_IDS line with the ids on stdin (one per line).
write() {
  local ids
  ids=$(paste -sd, -)
  sed -i '/^ALLOWED_USER_IDS=/d' "$config"
  echo "ALLOWED_USER_IDS=$ids" >> "$config"
}

apply() {
  [[ -n ${NO_RESTART:-} ]] || systemctl restart verity
  echo "allowed users (besides the owner):"
  current | sed 's/^/  /'
}

cmd=${1:-}
uid=${2:-}
uid=${uid,,}

case $cmd in
  list)
    current
    ;;
  add|remove)
    [[ $uid =~ $uid_re ]] || { echo "not a Supabase user UID: '${2:-}'" >&2; exit 2; }
    ids=$(current)
    if [[ $cmd == add ]]; then
      if grep -qx "$uid" <<<"$ids"; then echo "already allowed"; exit 0; fi
      { [[ -z $ids ]] || echo "$ids"; echo "$uid"; } | write
    else
      if ! grep -qx "$uid" <<<"$ids"; then echo "not in the allowlist"; exit 0; fi
      { grep -vx "$uid" <<<"$ids" || true; } | write
    fi
    apply
    ;;
  *)
    echo "usage: $0 list | add <uid> | remove <uid>" >&2
    exit 2
    ;;
esac
