#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
check_cluster "${CLUSTER:-}"
fail "${CLUSTER}: bootstrap 구현 대기. baseline·애드온 버전과 계정/context 확인 로직을 먼저 구현하세요."
