#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
require_command helm

printf '%s\n' '현재 charts는 빈 scaffold입니다. 형식 검증만 수행하며 배포 동작은 검증하지 않습니다.'
for name in cluster-baseline iris-platform iris-service; do
  helm lint --strict "$REPO_ROOT/helm/charts/$name"
done
for target in aws local; do
  if [[ "$target" == aws ]]; then
    defaults="$REPO_ROOT/clusters/aws-dev-workload/values/service-defaults.yaml"
  else
    defaults="$REPO_ROOT/clusters/local-workload/values/service-defaults.yaml"
  fi
  chart="$REPO_ROOT/helm/charts/iris-service"
  sample="$REPO_ROOT/examples/$target-service-values.yaml"
  helm lint --strict "$chart" -f "$defaults" -f "$sample"
  helm template demo "$chart" -f "$defaults" -f "$sample" >/dev/null
done
