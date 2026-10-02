# 1002 — 분석 파이프라인의 환경 바인딩 배포 계약

작성일: 2026-10-02. 작업 브랜치: `feat/analyzed-pipeline-bindings`.
전체 흐름: [WAS 1002 설계](https://github.com/2026-softbank-1/iris-was/blob/feat/ai-analysis-integration/docs/1002-integration-design.md).

## 수정 내용

WAS에서 확인한 공개 환경값과 기존 Secret 참조가 실제 Pod에 전달되도록 `iris-service` chart에 `environment`를 추가했다. chart는 0.4.0 → 0.5.0, ApplicationSet 기본 chartRevision은 `iris-service-0.5.0`이다.

- deployment template: 플랫폼 env 뒤에 사용자 environment를 추가한다.
- values schema: 최대 100개, name/value 또는 name/valueFrom.secretKeyRef 중 하나를 허용한다.
- schema/template: 플랫폼 PORT/IRIS_PUBLIC_DOMAIN/IRIS_GIT_COMMIT_SHA 덮어쓰기, 민감한 키의 공개 값, 중복 이름, 값과 참조 동시 입력을 거절한다.
- defaults: `environment: []`.
- deployment 계약/README/Helm 계약 확인 스크립트: 새 필드를 맞췄다.

```yaml
environment:
  - name: LOG_LEVEL
    value: info
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: service-db
        key: url
```

## 현재 설계와 적용 순서

GitOps 위치 `services/{serviceId}/prod/values.yaml`, Argo Application/namespace `svc-{serviceId}`는 유지한다. WAS Deploy Worker는 이 계약에 맞는 values를 만든다. Secret은 해당 namespace에 미리 존재해야 한다. 차트는 Secret을 생성하거나 비밀값을 읽지 않는다. runtime Secret 참조는 build secret 공급 경로가 아니다.

이 PR merge 후 chart `iris-service-0.5.0` tag를 발행하고 management ApplicationSet을 적용한 뒤 새 WAS Worker를 사용한다. tag/클러스터 적용은 이번 마감에서 실행하지 않았다. 이전 chart는 environment 필드를 거절하므로 변수 포함 배포는 이 변경이 먼저 적용되어야 한다.

작업 중 고정 Helm lint/render와 binding schema 검사를 개별 확인했으며 실제 클러스터 배포는 하지 않았다. 마감 요청 이후 추가 검증은 실행하지 않는다.
