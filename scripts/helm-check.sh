#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_command helm

printf '%s\n' 'cluster-baseline·iris-platform 은 빈 scaffold 라 형식만 검증합니다.'
for name in cluster-baseline iris-platform; do
  helm lint --strict "$REPO_ROOT/helm/charts/$name"
done

# iris-service 는 chart 의 ci/ values(Deploy Worker 출력과 같은 모양)로 schema·render 를 검증합니다.
chart="$REPO_ROOT/helm/charts/iris-service"
for values in "$chart"/ci/*.yaml; do
  helm lint --strict "$chart" -f "$values"
  helm template demo "$chart" -f "$values" >/dev/null
done
