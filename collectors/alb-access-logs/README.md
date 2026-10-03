# ALB access-log collector

S3/SQS → management Loki의 정규화 로그를 수집하는 단일 replica collector입니다. 집계는 [Loki recording rules](../../helm/charts/iris-alb-log-collector/files/traffic.yaml)가 수행합니다. 서비스 라벨·단위·시간 의미는 [연동 계약](../../contracts/service-traffic.md), 배포와 장애 대응은 [runbook](../../docs/runbooks/observability.md#alb-외부-트래픽-v2)에 있습니다.

- 관리 클러스터 Pod Identity로 AWS에 접근합니다. S3 read, SQS consume/DLQ send, ELB Describe만 허용합니다.
- SQLite outbox/checkpoint는 1Gi gp3 PVC에 저장합니다. `Recreate`, 1 replica를 유지합니다.
- 시작·재시작 시 첫 정상 AWS 조회 후 소비합니다. ALB 식별자·TargetGroup 태그·조회 시각을 모두 확인한 뒤 하나의 SQLite transaction으로 저장하며, 조회/저장 실패 시 마지막 정상 snapshot을 유지합니다. 매핑이 5분 이상 오래되면 readiness가 내려갑니다.
- 확인한 ALB 식별자와 삭제·교체된 TargetGroup의 마지막 매핑을 7일 보관하여 교체 전 ALB의 지연 로그도 처리합니다. AWS account·region·cluster·Ingress group이 다른 PVC는 재사용하지 않습니다. 아직 확인하지 못한 같은 group의 ALB/TG는 최대 5분 재시도 후 `mapping`으로 격리합니다.
- S3 알림 중복은 완료 객체로 건너뜁니다. 전송 ack 유실은 원래 timestamp·record_id·JSON body로 replay합니다.
- 압축 전후 객체 64MiB, 한 줄 64KiB, push 100건으로 제한합니다. URL·query·IP·User-Agent는 보내지 않습니다.
- 정상 처리·진단 격리·보존 기간 만료로 종료한 객체는 SQS ack합니다. 일시적 장애는 큐의 maxReceiveCount 10과 DLQ로 제한합니다. 부분 수락은 개별 동일 레코드를 재전송합니다.
- outbox 본문·객체별 진단은 최초 수신 후 7일이 지나면 전송 완료 여부와 무관하게 정리합니다. 미완료 객체는 `expired`로 종료하며, 완료/만료 시각부터 14일간 작은 체크포인트를 남겨 중복 알림을 건너뜁니다. 정리는 60초마다 최대 100객체씩 AWS 조회/Loki/DLQ 성공 여부와 독립적으로 수행하며 재수신 때도 만료를 확인합니다. 만료 때문에 별도 DLQ 메시지를 보내지 않습니다.
- `records_total{reason="expired"}`는 정리 당시 로컬 outbox의 pending 건수입니다. ack 유실로 Loki에 이미 수락된 항목을 포함할 수 있어 확정 유실 건수로 해석하지 않습니다. SQLite는 빈 페이지를 재사용하고 WAL을 checkpoint하며 full VACUUM은 수행하지 않습니다. 1Gi 용량은 트래픽에 맞춰 측정해야 합니다.

## 로컬 검증

Python 3.14, Helm 3.19.1, PyYAML 6.0.3을 사용합니다. 단위 테스트와 실제 프로세스 통합 테스트는 AWS credentials·dev endpoint를 사용하지 않습니다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s collectors/alb-access-logs -p test_collector.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test-alb-traffic.py
python3 scripts/check-alb-traffic.py --loki-binary /path/to/loki-3.6.12
python3 collectors/alb-access-logs/integration.py \
  --loki-binary /path/to/loki-3.6.12 \
  --prometheus-binary /path/to/prometheus-3.15.0
```

통합 테스트는 임시 디렉터리와 localhost의 실제 Loki/ruler/Prometheus를 사용합니다. 평가 주기만 1초로 줄이고 production의 1분/5분 window와 15분 offset은 유지합니다. 서비스 분리, 오류 0/결측, 바이트·분위수, 인접 분 경계, 중복 알림·부분 수락·ack 유실·flush·재시작·rule reload와 ALB 교체/조회 실패 후 실제 요청·바이트 합계를 확인합니다. CI는 공식 바이너리를 SHA256으로 확인합니다.

단위 테스트는 7일/14일 경계, 만료 후 중복, 장애 중 정리, transaction rollback, ALB 이력·범위 검증과 기존 SQLite schema의 추가 컬럼 migration을 확인합니다. digest gate 테스트는 임시 저장소의 빈/실제 형식 digest·lock 일치/불일치를 검사하며 실제 deployment 입력을 바꾸지 않습니다. `IRIS_ALB_LOKI_CHART=/path/to/loki`를 지정하면 캐시한 7.3.0 chart를 사용하고, 생략하면 해당 버전을 임시 디렉터리에 내려받습니다.

## 기존 state 업그레이드·코드 롤백

기존 객체/record ID·timestamp·body·전송 상태는 보존하고, 완료 시각·정리 표시 컬럼과 ALB 이력/snapshot 테이블만 추가합니다. 기존 완료 객체는 과거 완료 시각을 알 수 없어 최초 수신 시각을 보존 기간의 기준으로 사용합니다. 기존 state에는 ALB 이력이 없으므로 첫 정상 조회 전에 삭제된 ALB를 소급해서 확인할 수는 없습니다.

코드 롤백 시 collector를 중지하고 PVC를 보존한 상태에서 이전 이미지를 사용합니다. 추가 컬럼/테이블은 이전 v2의 SQL과 호환되지만, 이전 코드는 새 만료 처리와 ALB 이력을 사용하지 않습니다. state를 초기화하거나 DLQ를 일괄 redrive하지 않습니다. 체크포인트 보존 기간 이후 수동 재처리는 S3 재조회/재전송을 유발할 수 있으며, 원본 존재 여부와 Loki 입력 허용 시간을 먼저 확인해야 합니다. 실제 중지·이미지 변경·배포는 별도 승인 범위입니다.

## 이미지

Dockerfile base digest와 SDK 전이 의존성의 wheel SHA256을 고정했습니다.

```bash
docker build --platform linux/amd64 --tag iris-alb-log-collector:local collectors/alb-access-logs
```

현재 deployment values는 `enabled: false`, `image.digest: ""`입니다. chart fixture의 가상 digest는 렌더링 테스트에만 사용하며 배포 입력으로 쓰지 않습니다. 실제 ECR `iris/alb-log-collector`에 빌드 이미지를 게시한 뒤 digest를 조회하여 cluster values와 `helm/images.lock.json`에 실제 이미지 참조를 추가해야 합니다. 기존 was/build/deploy worker 게시 역할에 collector 게시 권한은 추가하지 않았습니다. 이미지 게시와 인프라·GitOps 배포는 별도 승인 범위입니다.

## 이미지 CI

[ALB collector image workflow](../../.github/workflows/alb-log-collector.yml)는 Terraform CI와 별도로 이미지를 빌드합니다. 코드·Dockerfile·의존성·빌드 helper/workflow 변경은 빌드 대상이며, 테스트·recording rules 변경은 검증만 수행하고 문서·배포 digest 변경은 재빌드하지 않습니다. 일반 PR은 인증 없이 테스트·로컬 빌드만 수행합니다.

main의 빌드 대상 변경은 설정 확인과 단위/실제 Loki·Prometheus 테스트 후 collector 전용 OIDC 역할로 ECR에 게시합니다. 태그는 `sha-<source SHA>-<run ID>-<attempt>`, 배포 참조는 Buildx가 반환한 실제 digest입니다. PAT는 PR 생성에 사용하고 AWS 인증에는 사용하지 않습니다.

게시 성공 후 `automation/alb-log-collector-image` 브랜치의 PR을 생성·갱신합니다. 변경 파일은 cluster collector values의 `image.digest`와 image lock의 태그 없는 repository URL entry 두 개입니다. source SHA/run/attempt는 digest 옆 YAML 주석으로 기록합니다. 최신 main과 빌드 입력이 다르거나 동일 입력의 더 최신 실행이 이미 갱신했다면 이전 결과를 건너뜁니다. 자동화 브랜치의 다른 설정·파일 변경은 덮어쓰지 않고 실패합니다.

digest PR에서는 `collector-ci`가 merge tree와 빌드 source 입력을 대조합니다. 이 검사가 이후 main 변경에도 효력을 가지려면 **필수 검사 + 최신 base 반영 후 merge(strict)** 규칙이 필요합니다. 일반 코드 PR은 이전 배포 이미지 provenance 때문에 막지 않습니다. 자동 merge/collector 활성화/Argo revision 변경은 수행하지 않습니다.

최초 게시·설정·재시도는 [운영 절차](../../docs/runbooks/observability.md#collector-이미지-ci-설정)를 따릅니다. 로컬 검증은 아래처럼 실행하며, 실제 Actions/ECR/PR 실행과는 구분합니다.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test-collector-ci.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test-build-push-ecr.py
```

현재 작업 머신은 Docker daemon에 연결할 수 없어 컨테이너 build/run은 로컬에서 검증하지 못했습니다. 실제 네이티브 Loki/Prometheus 통합 검증은 별도로 수행합니다.
