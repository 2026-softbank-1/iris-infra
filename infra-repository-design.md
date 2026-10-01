# 인프라 저장소 설계안

작성일: 2026-10-01
상태: 기존 인프라 저장소에 적용할 구조 제안
초기 대상: AWS 관리 EKS, AWS 사용자 앱 EKS, 로컬 k3d
배포 방식: BuildKit Job + Deployer Worker + Helm 직접 배포

## 1. 저장소의 역할

이 저장소에서 플랫폼을 실행하는 AWS 자원, Kubernetes 공통 설정, Helm 차트, 운영 절차를 함께 관리합니다. 서비스 소스와 배포 오케스트레이션 코드는 각각 서비스·백엔드 저장소에서 관리합니다.

| 관리 항목 | 저장 위치 / 담당 |
|---|---|
| AWS 자원의 선언, 공통 차트, 클러스터별 설정 | 인프라 Git 저장소 |
| Terraform이 관리하는 실제 자원 상태 | S3 Terraform backend |
| 사용자 앱 이미지와 배포 가능한 차트 패키지 | ECR |
| 소스 URL·commit SHA, 배포 상태·이력·현재 성공 버전 | 플랫폼 PostgreSQL |
| 실제 환경변수·비밀값 | SSM Parameter Store, 배포 시 Kubernetes Secret |
| 빌드 Job 생성, Helm 호출, 상태 수집, 실패 처리 | 백엔드 저장소의 API / Worker |
| 사용자 앱 소스 | 사용자 원본 저장소, 빌드 작업의 고정 commit 작업본 |

사용자 앱을 배포할 때마다 인프라 저장소에 프로젝트 폴더나 Git commit을 만들 필요는 없습니다. 현재 Helm 직접 배포 구조에서 인프라 저장소는 공통 기반과 템플릿의 원본입니다.

## 2. 디렉터리 구성

별도 인프라 저장소이므로 최상위부터 도구와 책임에 따라 구분합니다.

| 경로 | 관리 내용 | 초기 구성 |
|---|---|---|
| README.md | 프로젝트 범위, 필수 도구, 시작 순서, 문서 링크 | 필수 |
| terraform/bootstrap/aws/ | state용 S3 버킷, 버전 관리, 암호화, 퍼블릭 접근 차단 | 필수 |
| terraform/account/aws/ | 팀 IAM 그룹·정책, GitHub OIDC와 CI 역할, 계정 공통 설정 | 필수 |
| terraform/environments/aws/dev/foundation/ | 공통 VPC·서브넷·네트워크, ECR, 공통 workload IAM 역할 | 필수 |
| terraform/environments/aws/dev/management/ | 관리 EKS·노드 그룹, Pod Identity Agent 등의 AWS 애드온·IAM 연결 | 필수 |
| terraform/environments/aws/dev/workload/ | 사용자 앱 EKS·노드 그룹, Worker의 대상 EKS 인증 연결 | 필수 |
| terraform/modules/eks/ | 두 EKS 구성이 공유하는 모듈 | 두 클러스터 구현 시 |
| helm/charts/cluster-baseline/ | Namespace, 공통 RBAC, 리소스 상한 등 기본 Kubernetes 정책 | 클러스터 생성 후 |
| helm/charts/iris-platform/ | API·Worker·Agent, 초기 플랫폼 PostgreSQL·PVC | 플랫폼 배포 시 |
| helm/charts/iris-service/ | 사용자 앱 Deployment·Service·Ingress 등의 공통 차트 | 샘플 배포 시 |
| clusters/aws-dev-management/ | 관리 클러스터 정보, 플랫폼·공통 애드온 values | 클러스터 생성 후 |
| clusters/aws-dev-workload/ | 대상 클러스터 정보, 공통 애드온·앱 타겟 기본 values | 클러스터 생성 후 |
| clusters/local-workload/ | 로컬 클러스터 정보, Traefik 등의 앱 타겟 기본 values | 로컬 연결 시 |
| local/ | k3d 설정, 이미지 import, 로컬 공개 URL 실행 안내 | 로컬 연결 시 |
| contracts/ | Worker·CLI와 합의할 타겟·빌드·배포 설정 규격 | 초기 합의부터 |
| examples/ | AWS·로컬 샘플 앱 values, 샘플 BuildKit Job | 검증 시 |
| scripts/ | 클러스터 초기화, 타겟 정보 추출, 접속 검증, 정리 명령 | 필요 시 |
| docs/architecture.md | 구성도, 네트워크·접근 경로, 구성 요소 책임 | 초기 |
| docs/decisions/ | 주요 설계 선택과 이유 | 선택이 발생할 때 |
| docs/runbooks/ | 계정 준비, bootstrap, 배포, 장애 확인, 철거 절차 | 구현과 함께 |
| .github/workflows/ | Terraform·Helm 검증, 차트 패키징·배포 | 로컬 검증 이후 |
| Makefile | 팀에서 사용하는 공통 실행 명령 | 초기 |

위 표는 전체 목적지입니다. 처음에는 README, Terraform bootstrap/account, docs, 기본 검증 workflow부터 추가하고 구현 순서에 맞춰 나머지를 채웁니다. Azure/GCP 등의 빈 디렉터리를 미리 만들 필요는 없습니다.

## 3. Terraform 실행 단위와 state

### 3.1 실행 단위

terraform/bootstrap/aws, terraform/account/aws, foundation, management, workload 각각을 독립적인 root module로 사용합니다. 각 디렉터리에서 별도로 init, plan, apply를 실행합니다.

각 root module의 기본 파일은 다음과 같습니다.

| 파일 | 역할 |
|---|---|
| versions.tf | Terraform·provider 버전 조건 |
| providers.tf | AWS provider, 리전, 허용 AWS 계정 ID |
| backend.tf | S3 backend 선언 |
| backend.hcl.example | 버킷·state key·리전 예시 |
| main.tf | 리소스와 재사용 모듈 호출 |
| variables.tf | 입력값과 설명 |
| outputs.tf | 후속 구성에 전달할 비밀값이 아닌 출력 |
| terraform.tfvars.example | 계정 ID·리전·이름·노드 설정 등의 예시 |
| README.md | 준비 사항, 실행 방법, 생성 자원 |

파일 이름은 책임을 알아보기 위한 규칙입니다. Terraform은 같은 root 디렉터리의 .tf 파일을 함께 읽으므로, 파일별로 별도 실행하지 않습니다.

재사용 모듈은 terraform/modules/eks 아래 main.tf, variables.tf, outputs.tf, README.md로 시작합니다. 실제로 중복되는 클러스터 생성 구성을 모듈에 두고, 환경별 이름·노드 설정은 각 root에서 전달합니다.

### 3.2 state 경로

S3 버킷 하나에서 다음처럼 key를 분리합니다.

| 실행 단위 | S3 key 예시 |
|---|---|
| bootstrap/aws | bootstrap/aws/terraform.tfstate |
| account/aws | account/aws/terraform.tfstate |
| AWS dev foundation | aws/dev/foundation/terraform.tfstate |
| AWS dev management | aws/dev/management/terraform.tfstate |
| AWS dev workload | aws/dev/workload/terraform.tfstate |

bootstrap은 최초 로컬 state로 버킷을 생성한 뒤 S3로 이전합니다. S3 backend에는 use_lockfile = true를 적용합니다. 버킷은 버전 관리·암호화·퍼블릭 접근 차단·삭제 보호를 설정합니다.

두 사람은 같은 root의 S3 key를 사용합니다. 서로 다른 root의 잠금은 독립적이므로, 의존 관계에 있는 stack의 apply는 순서에 맞춰 실행합니다.

### 3.3 의존 방향

기본 의존 방향은 bootstrap → account / foundation → management / workload → Kubernetes bootstrap → 플랫폼·샘플 앱 배포입니다.

foundation의 VPC·서브넷·ECR·공통 역할 출력값을 각 클러스터 stack이 사용합니다. 관리/대상 클러스터가 상대방의 state를 서로 읽는 순환 의존은 피합니다. Worker의 공통 IAM 역할은 foundation에 두고, 관리 클러스터의 Pod Identity 연결과 대상 EKS의 Access Entry가 각각 해당 역할을 참조하도록 구성합니다.

관리→대상 API의 네트워크 경로는 foundation에서 정의한 공통 접근용 보안 그룹과 VPC 경로를 활용합니다. Pod Identity를 선택한 관리 EKS에는 Agent를 설치하고, 빌드·Worker ServiceAccount에 각각 역할을 연결합니다. IAM 연결과 Kubernetes RBAC는 함께 검증합니다.

terraform_remote_state를 사용하는 초기 구현도 가능합니다. 다만 출력값을 읽을 권한은 해당 state 전체를 읽는 권한과 연결되므로, 인프라 담당자와 인프라 CI에 한정합니다. 애플리케이션에는 필요한 출력만 별도 타겟 설정으로 전달합니다.

이미 콘솔에서 만든 IAM 그룹·사용자를 Terraform으로 관리하려면 기존 자원을 import합니다. 초기 관리자 로그인 수단과 비밀번호는 별도 관리하고, 클러스터 철거에 포함하지 않습니다.

## 4. Terraform과 Helm의 책임

같은 자원을 두 관리 도구가 함께 소유하지 않도록 구분합니다.

| 자원 | 관리 주체 |
|---|---|
| VPC·서브넷·라우팅·보안 그룹 | Terraform |
| EKS·노드 그룹·IAM 역할·EKS Access Entry | Terraform |
| AWS 관리형 EKS 애드온으로 선택한 VPC CNI·EBS CSI 등 | Terraform의 EKS 애드온 설정 |
| ECR 저장소, GitHub OIDC, AWS 공통 자원 | Terraform |
| AWS Load Balancer Controller 등 Helm으로 설치하는 외부 애드온 | 클러스터 bootstrap의 Helm release |
| Namespace·공통 RBAC·리소스 상한·기본 정책 | cluster-baseline chart |
| 플랫폼 API·Worker·Agent·초기 PostgreSQL | iris-platform chart |
| 사용자 앱 리소스 | Worker가 iris-service chart로 관리하는 Helm release |
| ALB 등 컨트롤러가 생성하는 AWS 자원 | 해당 Kubernetes 컨트롤러 |
| 프로젝트별 실제 Namespace·Secret·배포 values·상태 | 플랫폼 프로젝트 등록·배포 흐름 |

ALB Controller를 Helm으로 설치하면 해당 release의 설정과 버전은 인프라 저장소에서 관리합니다. 컨트롤러가 만든 ALB를 Terraform에서도 별도로 선언해 관리하지 않습니다.

프로젝트별 Namespace 생성에는 클러스터 범위 권한이 필요합니다. 이를 프로젝트 등록 단계의 권한으로 별도 정의하고, 일반 앱 배포 권한은 대상 Namespace 범위에 맞춥니다. 초기 샘플 Namespace는 bootstrap으로 사전 생성할 수 있습니다.

cluster-baseline의 NetworkPolicy는 사용 중인 CNI에서 실제 적용되는지 검증합니다.

## 5. Helm 차트 구성

### 5.1 세 가지 차트

| 차트 | 포함할 내용 | 배포 담당 |
|---|---|---|
| cluster-baseline | 고정 Namespace, ServiceAccount, 공통 RBAC, Quota·LimitRange, 검증된 기본 정책 | 인프라 담당자 |
| iris-platform | Control API, Deployer Worker, Agent, 플랫폼 DB·PVC, 설정·Secret 참조 | 인프라 담당자 / 플랫폼 배포 CI |
| iris-service | Deployment, Service, Ingress, 선택적인 migration Job, health·resource 설정 | Deployer Worker / 로컬 CLI |

플랫폼 PostgreSQL은 배포 메타데이터용 DB입니다. 사용자 앱마다 DB를 자동 제공하는 기능과 구분합니다. 이후 RDS로 이전할 때 플랫폼 chart는 외부 DB 연결 설정을 사용하고, AWS DB 자원은 Terraform에서 관리합니다.

각 custom chart의 기본 구조는 Chart.yaml, values.yaml, values.schema.json, templates/, README.md입니다. 사용자 앱 values의 검증 원본은 iris-service/values.schema.json에 둡니다.

외부 애드온은 원본 chart를 복사하기보다 공식 chart의 이름·버전을 고정하고 팀 설정 values를 관리합니다. 초기 설치 명령과 chart 버전은 scripts/bootstrap-cluster.sh에서 관리하고, 클러스터별 values는 clusters에 둡니다.

### 5.2 클러스터별 설정

| 경로 예시 | 내용 |
|---|---|
| clusters/aws-dev-management/cluster.yaml | target ID, 용도, provider, 리전, Terraform 경로, 로컬 kube-context 별칭 |
| clusters/aws-dev-management/values/baseline.yaml | 관리·빌드 Namespace와 정책 |
| clusters/aws-dev-management/values/platform.yaml | API·Worker 이미지, 리소스, 플랫폼 DB, Secret 이름 |
| clusters/aws-dev-management/values/load-balancer-controller.yaml | 관리 클러스터의 외부 애드온 설정 |
| clusters/aws-dev-workload/cluster.yaml | 사용자 앱 배포 대상의 식별 정보 |
| clusters/aws-dev-workload/values/baseline.yaml | 대상 클러스터의 기본 정책 |
| clusters/aws-dev-workload/values/service-defaults.yaml | AWS 앱 Ingress 등 타겟 기본 설정 |
| clusters/local-workload/values/service-defaults.yaml | 로컬 앱 Ingress class 등 기본 설정 |

cluster.yaml은 이 프로젝트가 정의하는 설정 파일입니다. Kubernetes가 직접 읽는 manifest가 아닙니다. kube-context는 로컬 별칭이며, 실제 kubeconfig와 인증 정보는 각 실행 환경에서 생성합니다.

values의 합성 순서는 chart 기본값 → 타겟 기본값 → 배포별 생성값입니다. schema 검증과 서버 측 필드 제한을 통해 사용자 입력이 배포 권한이나 타겟 공통 정책을 임의로 바꾸지 못하도록 구현합니다.

## 6. 백엔드·CLI에 전달할 규격

### 6.1 배포 values 예시

아래는 최종 앱 배포 values의 제안 형태입니다. 필드 이름은 백엔드 팀과 합의한 뒤 chart schema로 고정합니다.

~~~yaml
image:
  repository: ACCOUNT_ID.dkr.ecr.ap-northeast-2.amazonaws.com/apps/demo
  digest: sha256:IMAGE_DIGEST

containerPort: 3000
service:
  port: 80

envSecretName: app-demo-env

health:
  path: /health

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

ingress:
  enabled: true
  className: alb
  host: demo.apps.example.com
~~~

AWS 배포의 환경변수 실제 값은 SSM에서 조회해 Secret으로 전달합니다. 로컬 배포는 CLI의 로컬 환경 설정에서 Secret을 생성할 수 있도록 같은 chart 인터페이스를 사용합니다. 인프라 Git에는 Secret 참조와 설정 예시를 보관합니다.

image 입력은 repository와 함께 digest 또는 tag를 지원합니다. AWS 배포는 빌드 결과 digest로 고정하고, 로컬 k3d image import는 commit 기반 tag로 참조할 수 있도록 구성합니다. chart와 schema에서 두 입력의 처리 규칙을 명확하게 정의합니다.

### 6.2 contracts에 기록할 내용

| 문서 | 합의 내용 |
|---|---|
| contracts/deployment.md | chart values의 원본 schema 위치, release 이름·Namespace 규칙, image digest 사용 |
| contracts/target.md | target ID, cluster name·리전, Ingress 기본값, 인증 참조, Terraform 출력 전달 방식 |
| contracts/build.md | 고정 commit SHA, 빌드 플랫폼, 이미지 주소·digest, 로그 위치, 종료 코드 |
| contracts/namespace.md | 프로젝트 Namespace 생성 주체, Quota·정책 적용, 초기화 권한과 앱 배포 권한 |
| contracts/release.md | chart 버전·source SHA·image digest·target ID·values snapshot과 성공 버전 기록 |

Git의 타겟 정의와 Terraform 출력은 scripts/export-targets.sh가 필요한 항목만 추출해 .generated/targets.json으로 합성합니다. 플랫폼에 전달할 ConfigMap 또는 관리자용 등록 인터페이스는 백엔드와 합의합니다. 실제 사용자 배포 상태는 DB에서 갱신합니다.

배포별 generated values는 Worker 작업 디렉터리에서 생성하고, 재현에 필요한 비밀값이 아닌 snapshot과 참조를 배포 이력에 보관합니다.

BuildKit Job의 실제 생성 로직은 백엔드에 둡니다. examples/build-job.yaml은 실행 검증용 샘플이며, SA·리소스·timeout 등의 인프라 제약을 contracts/build.md에 기록합니다.

## 7. Worker와 CLI의 차트 사용

### 7.1 공통 원본과 버전

iris-service의 원본은 이 저장소 하나에서 관리합니다. 차트 변경 시 Chart.yaml의 version을 올리고 검증된 패키지를 배포합니다.

| 단계 | 흐름 |
|---|---|
| 개발 | 인프라 저장소의 iris-service chart 수정 |
| 검증 | lint, schema 검증, AWS·로컬 values로 template 렌더링 |
| 배포 | CI가 chart를 패키징해 ECR의 OCI chart 저장소에 push |
| AWS Worker | 배포에 지정된 chart 버전을 받아 Helm 실행 |
| 로컬 CLI | 같은 버전의 chart 패키지를 CLI 릴리스에 포함해 실행 |

ECR에 helm/iris-service 저장소를 만들면 OCI 참조는 oci://REGISTRY/helm/iris-service 형태로 사용합니다. helm push에서는 chart 이름을 제외한 oci://REGISTRY/helm 경로를 사용하고, install/upgrade에서는 chart 이름과 버전을 지정합니다.

로컬 CLI에 차트 패키지를 포함하면 로컬 배포용 차트를 얻기 위해 AWS 로그인이 필요하지 않습니다. CLI 구현과 릴리스 파이프라인은 이 버전 전달 규칙을 따릅니다.

Worker가 ECR에서 Helm chart를 직접 읽을 경우 Worker 역할에도 해당 chart 저장소 읽기 권한이 필요합니다. 대상 노드의 컨테이너 이미지 pull 권한과 별도입니다.

### 7.2 버전 기록

배포마다 source commit SHA, image reference, chart version, target ID, values snapshot/ref를 기록합니다. AWS의 image reference는 digest를 사용하고, 로컬 tag 참조 시에는 빌드 이미지 ID도 함께 기록합니다. 현재 성공 버전과 시도 중인 버전도 구분합니다.

차트의 최신 버전을 자동 선택하지 않고 배포 기록에 지정된 버전을 사용합니다. 빌드 실패 시 이전 배포를 유지하고, 배포·외부 접속 검사 실패 시의 복구는 Worker의 상태 처리와 연동합니다.

## 8. CI와 팀 명령

### 8.1 workflow

| workflow | 실행 내용 | 적용 순서 |
|---|---|---|
| terraform-check.yml | fmt, init -backend=false, validate | 초기 |
| helm-check.yml | lint, AWS·로컬 values/schema/template 검증 | 차트 추가 시 |
| terraform-plan.yml | GitHub OIDC 인증, 지정 stack init·plan | 로컬 plan 성공 후 |
| charts-release.yml | 버전 고정, chart package, OCI push | Worker 연동 시 |
| terraform-apply.yml | 선택 stack의 적용, 실행 대상·버전 확인 | 이후 수동 실행부터 |

초기 실제 apply는 두 인프라 담당자의 로컬 환경에서 진행합니다. Actions 연동 후에는 repo·브랜치의 OIDC claim에 맞춰 전용 역할의 신뢰 조건을 제한합니다.

PR의 기본 fmt/validate는 AWS 자격 증명이 없는 작업으로 구성합니다. AWS 인증이 필요한 plan은 초기에는 신뢰된 기본 브랜치에서 수동 실행합니다.

plan 역할에도 S3 state 조회와 .tflock 생성·삭제 권한이 필요합니다. AWS 리소스 조회 권한과 backend 잠금 권한을 함께 설정합니다.

### 8.2 Makefile 인터페이스 제안

아래는 구현할 팀 공통 명령의 인터페이스입니다.

~~~bash
make tf-plan STACK=aws/dev/foundation
make tf-apply STACK=aws/dev/foundation

make bootstrap CLUSTER=aws-dev-management
make bootstrap CLUSTER=aws-dev-workload

make helm-check
make smoke-test TARGET=aws-dev-workload
make smoke-test TARGET=local-workload
~~~

Makefile과 scripts에서 입력 STACK·CLUSTER·TARGET을 허용된 디렉터리에 매핑하고, 실행 대상 AWS 계정과 kube-context를 확인합니다. 파괴적인 destroy는 일반 apply 명령과 구분해 runbook에 정리합니다.

## 9. 구축 및 확장 순서

| 순서 | 생성·구현 항목 | 완료 기준 |
|---|---|---|
| 1 | README, 문서 구조, 도구 버전, ignore 규칙 | 두 사람이 같은 준비 절차 사용 |
| 2 | Terraform bootstrap | S3 state 저장·잠금 확인 |
| 3 | account | 기존 IAM import 범위 정리, 두 관리자 접근, 초기 CI 역할 설정 |
| 4 | foundation | VPC·외부 통신·ECR·공통 역할 생성 |
| 5 | management와 workload | EKS 두 개 및 필요한 인증 연결 완료 |
| 6 | baseline과 애드온 | Namespace·RBAC·LBC·스토리지 기본 동작 |
| 7 | iris-service와 샘플 | ECR 앱 이미지로 AWS 외부 접속 성공 |
| 8 | iris-platform와 타겟 설정 전달 | API·Worker·플랫폼 DB 실행, target 정보 공유 |
| 9 | BuildKit·Worker 연결 | Git SHA 고정 빌드→push→Helm 배포→상태·접속 검증 |
| 10 | 로컬 k3d와 CLI | 같은 앱 chart로 로컬 배포 검증 |
| 11 | OCI chart 릴리스 | Worker·CLI의 chart 버전 일치 및 이력 기록 |
| 12 | 추가 클라우드·관측 시스템 | 공급자별 Terraform과 타겟 values 추가 |

management와 workload Terraform은 foundation을 기반으로 독립 실행할 수 있도록 설계합니다. Helm bootstrap은 해당 클러스터 생성 이후에 실행합니다.

AWS 클러스터 검증이 끝나면 terraform/environments/azure/dev 또는 gcp/dev와 clusters의 타겟 디렉터리를 추가합니다. EC2 기반 자체 Kubernetes도 별도 Terraform 실행 단위와 타겟 설정으로 추가할 수 있습니다.

관측 도구는 공통 애드온과 클러스터 values로 확장하고, 저장소·수집·조회 책임을 docs에서 정리합니다. 사용자 앱 공통 차트는 타겟별 기본값으로 재사용합니다.

## 10. 필수 운영 문서와 완료 기준

| 문서 | 내용 |
|---|---|
| docs/runbooks/bootstrap.md | AWS 로그인, state 생성, stack 순서, kube-context 준비 |
| docs/runbooks/deploy-platform.md | 플랫폼 chart 버전, 이미지, DB 연결, 타겟 설정 적용 |
| docs/runbooks/troubleshooting.md | 빌드 로그, Pod 이벤트, image pull, Ingress·RBAC 확인 |
| docs/runbooks/teardown.md | 앱·Ingress 정리, ALB 삭제 확인, 클러스터·VPC 철거, 잔여 볼륨 확인 |
| docs/decisions/ | 두 클러스터 분리, shared VPC, Helm 직접 배포, chart 버전 배포 등의 이유 |

애플리케이션과 Ingress를 먼저 정리하고 ALB Controller가 생성한 자원의 삭제를 확인한 뒤 클러스터를 철거합니다. account와 bootstrap은 일반 개발 클러스터 철거와 별도 수명 주기로 관리합니다.

첫 완료 기준은 인프라 저장소만으로 두 담당자가 준비 절차를 재현하고, 관리 클러스터에서 샘플 앱을 빌드해 대상 클러스터에 배포한 뒤 외부 접속과 로그 조회까지 확인하는 것입니다.

실제 저장소 적용 시 AWS 계정 ID·리전·저장소 이름·브랜치·도구 버전·노드 자원은 프로젝트 값으로 확정해야 합니다. 이 문서는 설계안이며 실제 cloud 자원을 생성한 결과는 아닙니다.

## 공식 참고 자료

- [Terraform module 구조](https://developer.hashicorp.com/terraform/language/modules/develop/structure)
- [Terraform S3 backend와 잠금](https://developer.hashicorp.com/terraform/language/backend/s3)
- [Helm chart 구조](https://helm.sh/docs/topics/charts/)
- [Helm OCI registry 사용](https://helm.sh/docs/topics/registries/)
- [ECR에 Helm chart 저장](https://docs.aws.amazon.com/AmazonECR/latest/userguide/push-oci-artifact.html)
- [GitHub Actions의 AWS OIDC 인증](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
