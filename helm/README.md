# Helm

세 custom chart의 원본을 `charts/`에서 관리합니다. 현재는 빈 scaffold입니다.
외부 애드온의 공식 차트는 복사하지 않고 설치할 이름·버전을 bootstrap script에 고정합니다.
AWS 관리형 EKS addon은 Terraform, Helm으로 설치하는 addon은 bootstrap이 소유합니다.
`make helm-check`는 현재 기본 형식을 확인하며 실제 manifest 검증은 구현 후 확장합니다.
