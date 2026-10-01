# 로컬 workload

상태: 설정 예시만 준비했습니다. Docker·k3d·Helm·kubectl 버전을 검증하고 k3s 이미지를 고정합니다.

```bash
cp local/k3d.yaml.example local/k3d.yaml
# k3d.yaml에 검증한 k3s image 버전 지정
k3d cluster create --config local/k3d.yaml
kubectl --context k3d-iris-workload get nodes
k3d image import demo:COMMIT_SHA --cluster iris-workload
```

로컬 CLI는 로컬 설정에서 Kubernetes Secret을 생성하고 AWS와 같은 iris-service 차트를 사용합니다.
image tag는 commit 기반으로 고정하고 이미지 ID도 배포 기록에 보관합니다.
Traefik과 호스트·8080 포트 접근을 검증한 뒤 필요하면 공개 URL 도구의 설정을 추가합니다.
CLI 구현과 번들 차트 패키징은 백엔드·CLI 저장소에서 관리합니다.
