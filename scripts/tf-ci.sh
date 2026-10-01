#!/usr/bin/env bash
# CI deploys root stacks in dependency order. Account IAM is managed by an administrator.
source "$(dirname "$0")/common.sh"

action="${1:-}"
case "$action" in
  plan|apply) ;;
  *) fail '지원하는 CI 동작: plan, apply' ;;
esac
[[ "$#" == 1 ]] || fail '사용법: tf-ci.sh plan|apply'

require_command terraform
require_command aws
[[ "${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ && "$AWS_ACCOUNT_ID" != 000000000000 ]] \
  || fail 'AWS_ACCOUNT_ID에 실제 12자리 계정 ID가 필요합니다.'
[[ -n "${AWS_REGION:-}" && -n "${TF_STATE_BUCKET:-}" ]] \
  || fail 'AWS_REGION과 TF_STATE_BUCKET이 필요합니다.'

stacks=(bootstrap/aws aws/dev/foundation aws/dev/management aws/dev/workload)
enabled_stacks=()

# Check every enabled root before applying any resources.
for stack in "${stacks[@]}"; do
  dir="$(stack_directory "$stack")"
  [[ -d "$dir" ]] || fail "$stack: 디렉터리가 없습니다."
  if [[ -f "$dir/.scaffold" ]]; then
    [[ "$stack" != bootstrap/aws ]] || fail 'bootstrap 리소스를 먼저 구현하세요.'
    printf 'skip: %s (scaffold)\n' "$stack"
    continue
  fi
  [[ -f "$dir/.terraform.lock.hcl" ]] || fail "$stack: .terraform.lock.hcl을 Git에 포함하세요."
  grep -Eq '^[[:space:]]*backend[[:space:]]+"s3"' "$dir/backend.tf" \
    || fail "$stack: S3 backend 선언이 필요합니다."
  enabled_stacks+=("$stack")
done

actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$AWS_ACCOUNT_ID" ]] \
  || fail "AWS 계정 불일치: expected=$AWS_ACCOUNT_ID actual=$actual_account"

export TF_IN_AUTOMATION=true TF_INPUT=false
export TF_VAR_aws_account_id="$AWS_ACCOUNT_ID"
export TF_VAR_aws_region="$AWS_REGION"

plan_file=''
trap 'if [[ -n "$plan_file" ]]; then rm -f "$plan_file"; fi' EXIT

for stack in "${enabled_stacks[@]}"; do
  dir="$(stack_directory "$stack")"
  printf '%s: %s\n' "$action" "$stack"
  terraform -chdir="$dir" init -input=false -lockfile=readonly \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="key=$stack/terraform.tfstate" \
    -backend-config="region=$AWS_REGION" \
    -backend-config='encrypt=true' \
    -backend-config='use_lockfile=true' \
    -backend-config="allowed_account_ids=[\"$AWS_ACCOUNT_ID\"]"

  plan_file="$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/iris-tfplan.XXXXXX")"
  terraform -chdir="$dir" plan -input=false -lock-timeout=5m -out="$plan_file"
  if [[ "$action" == apply ]]; then
    terraform -chdir="$dir" apply -input=false -lock-timeout=5m "$plan_file"
  fi
  rm -f "$plan_file"
  plan_file=''
done
