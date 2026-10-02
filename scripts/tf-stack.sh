#!/usr/bin/env bash
# shellcheck source=scripts/common.sh
source "$(dirname "$0")/common.sh"

action="${1:-}"
case "$action" in
  init|plan|apply) ;;
  *) fail '지원하는 동작: init, plan, apply' ;;
esac

stack="${STACK:-}"
dir="$(stack_directory "$stack")"

if [[ "$action" != init && -f "$dir/.scaffold" ]]; then
  fail "$stack: 리소스가 없는 scaffold입니다. 구현·검증 후 .scaffold를 제거하세요."
fi
require_command terraform

if [[ "$action" == init ]]; then
  if grep -Eq '^[[:space:]]*backend[[:space:]]+"s3"' "$dir/backend.tf"; then
    [[ -f "$dir/backend.hcl" ]] || fail 'backend.hcl.example을 복사하고 실제 값을 입력하세요.'
    exec terraform -chdir="$dir" init -input=false -backend-config=backend.hcl
  fi
  exec terraform -chdir="$dir" init -input=false
fi

# The operator supplies the expected account explicitly. Pass the same ID to the provider.
[[ "${AWS_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ && "$AWS_ACCOUNT_ID" != 000000000000 ]] \
  || fail 'AWS_ACCOUNT_ID에 실행 대상의 실제 12자리 계정 ID를 지정하세요.'
require_command aws
actual_account="$(aws sts get-caller-identity --query Account --output text)"
[[ "$actual_account" == "$AWS_ACCOUNT_ID" ]] || fail "AWS 계정 불일치: expected=$AWS_ACCOUNT_ID actual=$actual_account"
[[ -f "$dir/terraform.tfvars" ]] || fail 'terraform.tfvars.example을 복사하고 실제 값을 입력하세요.'
printf 'STACK=%s AWS_ACCOUNT_ID=%s\n' "$stack" "$actual_account"

# apply retains Terraform's interactive approval. Never use -auto-approve here.
exec terraform -chdir="$dir" "$action" -var="aws_account_id=$AWS_ACCOUNT_ID"
