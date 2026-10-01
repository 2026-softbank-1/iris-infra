#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
check_cluster "${TARGET:-}"
fail "${TARGET}: smoke test 구현 대기. IAM/RBAC, image pull, health와 외부 접속 검사를 추가하세요."
