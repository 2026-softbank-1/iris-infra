# local-workload

용도: workload. provider: local.
`cluster.yaml`은 타겟 정의 초안이며 실제 kubeconfig·자격 증명을 포함하지 않습니다.
`kubeContext`는 로컬 별칭입니다. bootstrap 구현 시 이 context와 AWS 계정을 함께 확인합니다.
values는 기본 자리표시자이며 addon 이름·버전과 세부 리소스를 구현 시 확정합니다.
