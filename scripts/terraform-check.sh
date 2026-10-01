#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_command terraform

validate_directory() {
  local dir="$1"
  if [[ -f "$dir/.terraform.lock.hcl" ]]; then
    terraform -chdir="$dir" init -backend=false -input=false -lockfile=readonly
  else
    terraform -chdir="$dir" init -backend=false -input=false
  fi
  terraform -chdir="$dir" validate
}

for stack in bootstrap/aws account/aws aws/dev/foundation aws/dev/management aws/dev/workload; do
  dir="$(stack_directory "$stack")"
  printf 'validate: %s\n' "$stack"
  validate_directory "$dir"
done
validate_directory "$REPO_ROOT/terraform/modules/eks"
