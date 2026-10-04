#!/usr/bin/env bash
# Source this file from team command scripts. Bash 3.2 compatible.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "필수 도구가 없습니다: $1"
}

stack_directory() {
  case "$1" in
    bootstrap/aws|account/aws|bootstrap/gcp|account/gcp) printf '%s/terraform/%s\n' "$REPO_ROOT" "$1" ;;
    aws/dev/foundation|aws/dev/management|aws/dev/workload|aws/dev/gcp-access|gcp/dev/workload)
      printf '%s/terraform/environments/%s\n' "$REPO_ROOT" "$1" ;;
    *) fail "허용되지 않은 STACK: $1" ;;
  esac
}

check_cluster() {
  case "$1" in
    aws-dev-management|aws-dev-workload|local-workload) ;;
    *) fail "허용되지 않은 CLUSTER/TARGET: $1" ;;
  esac
}
