#!/usr/bin/env bash
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"
require_command helm
require_command python3
python3 "$REPO_ROOT/scripts/check-helm.py"
exec python3 "$REPO_ROOT/scripts/check-gcp.py"
