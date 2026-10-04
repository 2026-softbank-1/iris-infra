SHELL := /bin/bash
.DEFAULT_GOAL := help

export STACK CLUSTER TARGET AWS_ACCOUNT_ID GCP_PROJECT_ID

.PHONY: help scaffold-check tf-fmt tf-fmt-check tf-check tf-init tf-plan tf-apply helm-check bootstrap smoke-test export-targets

help:
	@echo 'make scaffold-check                  기본 파일·schema·shell 문법 검사'
	@echo 'make tf-fmt / tf-fmt-check            Terraform 형식 정리 / 검사'
	@echo 'make tf-check                        backend 없는 init + validate'
	@echo 'make tf-test                         격리 mock tests'
	@echo 'make eks-preflight TARGET=aws-dev-workload  AWS API 경로 검증'
	@echo 'make eks-api-tunnel TARGET=aws-dev-workload SSM 터널'
	@echo 'make tf-init STACK=aws/dev/foundation'
	@echo 'make tf-plan STACK=aws/dev/foundation AWS_ACCOUNT_ID=실제계정'
	@echo 'make tf-apply STACK=aws/dev/foundation AWS_ACCOUNT_ID=실제계정'
	@echo 'make helm-check                      chart lint + AWS/로컬 render'
	@echo 'make bootstrap CLUSTER=aws-dev-management  Argo CD + 두 클러스터 addon'
	@echo 'make smoke-test TARGET=aws-dev-workload     읽기 전용 운영 검증'
	@echo 'make export-targets                       검증된 Terraform target 내보내기'
	@echo 'make gcp-export gcp-preflight gcp-access    GCP metadata 검증 / DNS API 접근'
	@echo 'make gcp-bootstrap gcp-smoke               GCP 명시적 초기화 / 읽기 전용 검증'
	@echo 'GCP state bootstrap과 적용은 docs/runbooks/gcp-workload.md 참조'
	@echo 'make gcp-readiness gcp-ci-test             계정 없는 metadata / CI gate 검증'
	@echo 'GCP 자동화 활성화는 docs/runbooks/gcp-pipeline.md 참조'

scaffold-check:
	@python3 scripts/check-scaffold.py

tf-fmt:
	terraform fmt -recursive terraform

tf-fmt-check:
	terraform fmt -check -recursive terraform

tf-check: tf-fmt-check
	@bash scripts/terraform-check.sh

tf-init:
	@bash scripts/tf-stack.sh init

tf-plan:
	@bash scripts/tf-stack.sh plan

tf-apply:
	@bash scripts/tf-stack.sh apply

helm-check:
	@bash scripts/helm-check.sh

bootstrap:
	@bash scripts/bootstrap-cluster.sh

smoke-test:
	@bash scripts/smoke-test.sh

export-targets:
	@bash scripts/export-targets.sh

.PHONY: tf-test eks-preflight eks-api-tunnel
tf-test:
	@bash scripts/terraform-test.sh

eks-preflight:
	@bash scripts/eks-preflight.sh

eks-api-tunnel:
	@bash scripts/eks-api-tunnel.sh

.PHONY: gcp-export gcp-preflight gcp-access gcp-bootstrap gcp-smoke gcp-backup-keys gcp-state-local-init gcp-state-local-plan gcp-state-local-apply gcp-state-migrate
gcp-export:
	@python3 scripts/gcp-ops.py export
gcp-preflight:
	@python3 scripts/gcp-ops.py preflight
gcp-access:
	@python3 scripts/gcp-ops.py access
gcp-bootstrap:
	@python3 scripts/gcp-ops.py bootstrap --aws-account-id "$(AWS_ACCOUNT_ID)" --image "$(GCP_ECR_IMAGE)"
gcp-smoke:
	@python3 scripts/gcp-ops.py smoke
gcp-backup-keys:
	@python3 scripts/gcp-ops.py backup-keys
gcp-state-local-init:
	@python3 scripts/gcp-terraform.py local-init
gcp-state-local-plan:
	@python3 scripts/gcp-terraform.py local-plan
gcp-state-local-apply:
	@python3 scripts/gcp-terraform.py local-apply
gcp-state-migrate:
	@python3 scripts/gcp-terraform.py migrate

.PHONY: gcp-readiness gcp-online-readiness gcp-ci-config gcp-github-discover gcp-ci-test
GCP_CI_CONFIG ?= terraform/config/gcp/ci.example.json
gcp-readiness:
	@python3 scripts/gcp-setup.py readiness --config "$(GCP_CI_CONFIG)"
gcp-online-readiness:
	@python3 scripts/gcp-setup.py online --config "$(GCP_CI_CONFIG)"
gcp-ci-config:
	@python3 scripts/gcp-setup.py account-export --config "$(GCP_CI_CONFIG)"
gcp-github-discover:
	@python3 scripts/gcp-setup.py github-discover
gcp-ci-test:
	@python3 scripts/tests/test-gcp-ci.py
