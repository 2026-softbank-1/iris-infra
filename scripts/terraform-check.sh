#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_command terraform

for stack in bootstrap/aws account/aws aws/dev/foundation aws/dev/management aws/dev/workload; do
  dir="$(stack_directory "$stack")"
  printf 'validate: %s\n' "$stack"
  terraform -chdir="$dir" init -backend=false -input=false
  terraform -chdir="$dir" validate
done
terraform -chdir="$REPO_ROOT/terraform/modules/eks" init -backend=false -input=false
terraform -chdir="$REPO_ROOT/terraform/modules/eks" validate
