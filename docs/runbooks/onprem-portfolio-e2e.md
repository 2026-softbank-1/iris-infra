# 온프레미스 첫 앱 배포와 변경 기록

2026-10-03에 사용자가 운영 웹에서 요청한 `portpolio-production2` 배포를 검증했다.

- 프로젝트 12 / 서비스 28 / 배포 100 / 릴리스 44
- 소스: `monitor5/portpolio-production`, main `f54230346fbfca4e34ae2cdd1a370217611d2191`
- 앱: <https://portpolio-production2-28.internal.likelion.uk>
- 운영 화면: <https://app.likelion.uk/project/12/service/28/deployment/100>
- Argo 앱 `svc-28`, destination `iris-onprem-api.argocd.svc.cluster.local:6443`, namespace `svc-28`
- 이미지 digest: `sha256:1c2cc7d21bceaec2ff8942d5a5457a67aeb8a6d939161efc87b6e25d089c9cd8`

## 적용한 변경

| 대상 | 변경 | 관리·원복 |
| --- | --- | --- |
| iris-web | PR #39, main `cf3e59f`; ONPREM 선택·명시적 타깃 전송·미조회 차단 | PR revert. Amplify us-east-2, 앱 `d1c22f0apcqx7i`, main 배포 41 성공 |
| iris-infra | PR #69, main `ae7d333`; ONPREM ApplicationSet·svc-* 목적지·공개 게이트웨이·기존 ACM 연결 | GitOps revert와 [게이트웨이 롤백](onprem-gateway.md) 절차. Ingress는 prune=false라 잔여 규칙 확인 필요 |
| K3s RBAC | `iris-onprem-service-deployer` ClusterRole·Binding → 기존 `iris-onprem-test/iris-argocd` SA | [매니페스트](../../clusters/onprem-workload/argocd-service-deployer.yaml). 리소스 조회·Namespace 생성·앱 리소스 쓰기 허용. RBAC 변경·Pod exec·Secret 쓰기 불허 |
| Argo 클러스터 등록 | 기존 onprem 접속 인증 유지. namespaces 제한 해제, clusterResources=true | 기존 `namespaces=[iris-onprem-test]`, `clusterResources=false`로 복원. 앱 삭제·원복을 먼저 완료한 뒤 권한 축소 |
| AWS IAM | 역할 `iris-dev-onprem-ecr-svc-28`, inline policy `PullServiceImage` | 수동 생성 리소스. trust는 `arn:aws:iam::187069338876:user/iris-woo-hyun-kim` 한 명. 토큰 발급 및 `iris/services/28` 이미지 pull만 허용. 사용 종료 시 policy·role 삭제 |
| K3s 이미지 인증 | `svc-28/iris-ecr-pull` Secret과 default SA의 imagePullSecrets 연결 | AWS 영구 키를 서버에 보관하지 않음. 갱신 명령은 아래. 제거 시 Secret과 해당 SA 참조만 제거 |
| 실패 Pod | ECR 무인증으로 Pending인 Pod 1개 재생성 | 같은 배포 100에서 복구, 새 배포 요청 없음. 실행 중 앱·다른 namespace 재시작 없음 |

중복 준비한 프로젝트 13·미배포 서비스 29와 `iris-dev-onprem-ecr-svc-29` 역할·inline policy는 삭제했다. 기존 AWS 앱 서비스 8은 변경하지 않았다.

## 검증 결과

- 고정 Helm 3.19.1 렌더 검증 전체, eks-ops 단위 검사 24개, 프론트 typecheck·build 통과.
- 온프레미스 Namespace 생성·Deployment 생성 허용, RBAC 생성·Pod exec 거부 확인.
- Argo 클러스터 Successful, root·gateway·온프레미스 ApplicationSet Synced/Healthy.
- ECR 인증 누락으로 발생한 ImagePullBackOff를 해결한 뒤 Pod 1/1 Running.
- 배포 100 SUCCEEDED(2026-10-03 23:38:29 KST), 릴리스 44 SUCCEEDED.
- 공개 HTTPS의 HTML·JavaScript·CSS 200, TLS 인증서 검증 성공. 운영 웹 Active/Online과 브라우저 실제 렌더링 확인.

## 이미지 pull 인증 갱신

최초 ECR 토큰의 응답상 만료는 **2026-10-04 11:38:20 KST**다. 토큰 발급에 사용한 STS 역할 세션은 1시간이다. 실행 중인 컨테이너와 이미지 캐시를 사용하는 재시작은 별도로 계속 동작하지만, 이후 새 이미지 다운로드에는 유효한 인증이 필요하다. 다음 명령은 기존 역할을 사용해 Secret을 갱신하며 IAM 권한·영구 access key·Deployment를 변경하지 않는다.

```bash
# 먼저 서버 dry-run으로 검증한다. AWS profile은 실행할 운영자의 실제 profile을 사용한다.
python3 scripts/onprem-ecr-auth.py \
  --service-id 28 --profile iris-evaluation --account-id 187069338876 \
  --ssh-host remotedaus.iptime.org --ssh-port 1199 --ssh-user tempsoftbank \
  --ssh-key "$HOME/Downloads/tempsoftbank_ed25519" \
  --vm-host 192.168.122.131 --vm-user iris --vm-key /home/tempsoftbank/.ssh/iris_onprem_vm

# 검증한 동일 명령에 --apply를 추가하면 Secret과 default SA 참조를 갱신한다.
```

현재 trust에 다른 운영자는 포함되지 않는다. 종욱님 등 다른 운영자가 갱신하려면 그 사람의 실제 IAM ARN을 확인하고 역할 trust에 추가해야 한다. 다른 서비스는 각 서비스 저장소에 한정한 역할·정책과 해당 namespace 인증을 따로 준비한다.

이 첫 배포는 변수 없는 정적 앱이다. 사용자 환경변수를 사용하는 앱의 Sealed Secrets controller·공유 복호화 키와 온프레미스 로그/메트릭 수집은 이번 검증에 포함되지 않았다.
