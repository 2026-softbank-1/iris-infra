#!/usr/bin/env bash
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"
require_command python3
selected="${1:-${CLUSTER:-}}"
check_cluster "$selected"
if [[ "$#" -gt 0 ]]; then shift; fi
exec python3 "$REPO_ROOT/scripts/eks-ops.py" bootstrap "$selected" "$@"
