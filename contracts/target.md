# target

상태: 합의 전 초안.

Git 타겟 정의는 `clusters/<id>/cluster.yaml`입니다. kube-context는 실행 환경의 로컬 별칭입니다.
필요한 데이터: target ID, provider, 용도, AWS cluster name·리전, Ingress 기본값, 인증 참조.

Terraform에서 endpoint·CA·역할 등 비밀값이 아닌 필요한 출력만 export합니다.
전체 state 읽기 권한은 인프라 담당자·CI로 한정합니다.
`scripts/export-targets.sh`는 구현 후 `.generated/targets.json`을 생성합니다.
플랫폼 ConfigMap 또는 관리자 등록 인터페이스는 백엔드와 합의합니다.

TODO: schema, endpoint/CA 전달 형식, 인증 참조, 갱신·등록 방식을 확정합니다.
