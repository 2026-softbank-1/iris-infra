SHELL := /bin/bash
.DEFAULT_GOAL := help

export STACK CLUSTER TARGET AWS_ACCOUNT_ID

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
