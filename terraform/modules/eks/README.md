# eks 모듈

상태: scaffold. 두 클러스터에서 실제로 공유하는 구성을 구현할 위치입니다.
환경별 이름·버전·노드 설정은 management/workload root에서 전달합니다.
Pod Identity 연결은 management, Worker Access Entry는 workload root에서 관리합니다.
모듈 내부에서 provider·backend를 선언하지 않습니다.
