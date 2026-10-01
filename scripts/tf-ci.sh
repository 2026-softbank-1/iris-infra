#!/usr/bin/env bash
# CI deploys the implemented bootstrap stack. Account IAM is set up by an administrator.
source "$(dirname "$0")/common.sh"

action="${1:-}"
case "$action" in
  plan|apply) ;;
  *) fail '지원하는 CI 동작: plan, apply' ;;
esac

require_command terraform
require_command aws
[[ "${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ && "$AWS_ACCOUNT_ID" != 000000000000 ]] \
  || fail 'AWS_ACCOUNT_ID에 실제 12자리 계정 ID가 필요합니다.'
[[ -n "${AWS_REGION:-}" && -n "${TF_STATE_BUCKET:-}" ]] \
  || fail 'AWS_REGION과 TF_STATE_BUCKET이 필요합니다.'

dir="$(stack_directory bootstrap/aws)"
[[ ! -f "$dir/.scaffold" ]] || fail 'bootstrap 리소스를 먼저 구현하세요.'
[[ -f "$dir/.terraform.lock.hcl" ]] || fail 'bootstrap의 .terraform.lock.hcl을 Git에 포함하세요.'
grep -Eq '^[[:space:]]*backend[[:space:]]+"s3"' "$dir/backend.tf" \
  || fail 'bootstrap state를 S3로 이전하고 backend 선언을 활성화하세요.'

actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$AWS_ACCOUNT_ID" ]] \
  || fail "AWS 계정 불일치: expected=$AWS_ACCOUNT_ID actual=$actual_account"

export TF_IN_AUTOMATION=true TF_INPUT=false
export TF_VAR_aws_account_id="$AWS_ACCOUNT_ID"
export TF_VAR_aws_region="$AWS_REGION"

terraform -chdir="$dir" init -input=false -lockfile=readonly \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config='key=bootstrap/aws/terraform.tfstate' \
  -backend-config="region=$AWS_REGION" \
  -backend-config='encrypt=true' \
  -backend-config='use_lockfile=true' \
  -backend-config="allowed_account_ids=[\"$AWS_ACCOUNT_ID\"]"

plan_file="$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/iris-tfplan.XXXXXX")"
trap 'rm -f "$plan_file"' EXIT
terraform -chdir="$dir" plan -input=false -lock-timeout=5m -out="$plan_file"

if [[ "$action" == apply ]]; then
  # Main has authorized deployment: apply the exact saved plan without a second prompt.
  terraform -chdir="$dir" apply -input=false -lock-timeout=5m "$plan_file"
fi
