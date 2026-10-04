#!/usr/bin/env bash
# Run real Terraform test commands against a clean copy, never local state/tfvars.
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"
require_command terraform
require_command python3
work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/iris-tf-tests.XXXXXX")"
trap 'rm -rf "$work"' EXIT
python3 - "$REPO_ROOT/terraform" "$work/terraform" <<'PY'
from pathlib import Path
import shutil
import sys
src,dest=map(Path,sys.argv[1:])
for p in src.rglob('*'):
    rel=p.relative_to(src)
    if '.terraform' in rel.parts or not p.is_file() or p.name.endswith(('.tfvars.json','.tfvars')) or p.name in ('override.tf','override.tf.json') or p.name.endswith(('_override.tf','_override.tf.json')):continue
    if p.name == '.terraform.lock.hcl' or p.suffix == '.tf' or p.name.endswith('.tftest.hcl') or p.name.endswith('.example') or (rel.parts[0] == 'config' and p.suffix == '.json') or p.suffix in ('.yaml', '.yml'):
        target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
PY
export TF_PLUGIN_CACHE_DIR="$work/provider-cache"
mkdir -p "$TF_PLUGIN_CACHE_DIR"
# Reuse installed signed providers if available, without changing the original cache.
if [[ -d "$REPO_ROOT/terraform/modules/eks/.terraform/providers/registry.terraform.io" ]]; then
  cp -R "$REPO_ROOT/terraform/modules/eks/.terraform/providers/registry.terraform.io" "$TF_PLUGIN_CACHE_DIR/"
fi
for path in account/aws environments/aws/dev/foundation modules/eks environments/aws/dev/management environments/aws/dev/workload bootstrap/gcp account/gcp environments/gcp/dev/workload environments/aws/dev/gcp-access; do
  terraform -chdir="$work/terraform/$path" init -backend=false -input=false -lockfile=readonly
  terraform -chdir="$work/terraform/$path" test
 done
