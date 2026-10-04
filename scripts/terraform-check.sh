#!/usr/bin/env bash
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"
require_command terraform

# Never reuse a local directory initialized against real S3 state.
check_data="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/iris-tf-check.XXXXXX")"
trap 'rm -rf "$check_data"' EXIT

validate_directory() {
  local dir="$1"
  TF_DATA_DIR="$check_data/$(basename "$dir")-$(basename "$(dirname "$dir")")"
  export TF_DATA_DIR
  if [[ -f "$dir/.terraform.lock.hcl" ]]; then
    terraform -chdir="$dir" init -backend=false -input=false -lockfile=readonly
  else
    terraform -chdir="$dir" init -backend=false -input=false
  fi
  terraform -chdir="$dir" validate
}

for stack in bootstrap/aws account/aws aws/dev/foundation aws/dev/management aws/dev/workload bootstrap/gcp account/gcp gcp/dev/workload aws/dev/gcp-access; do
  dir="$(stack_directory "$stack")"
  printf 'validate: %s\n' "$stack"
  validate_directory "$dir"
done
validate_directory "$REPO_ROOT/terraform/modules/eks"
