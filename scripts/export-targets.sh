#!/usr/bin/env bash
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"
require_command python3
exec python3 "$REPO_ROOT/scripts/eks-ops.py" export "$@"
