# iris-onprem-probe

사용자가 등록한 온프레미스 서버에 Argo CD 가 실제로 붙어 쓰기까지 되는지 보는 chart 입니다. `iris-onprem-server` chart 의 probe Application(`iris-onprem-probe-{serverKey}`)이 서버의 `iris-system` namespace 에 ConfigMap `iris-onprem-probe` 하나를 만듭니다. AppProject `iris-onprem-probe` 는 `onprem-*` 클러스터의 `iris-system` 과 ConfigMap 만 허용합니다.

| 값 | 의미 |
|---|---|
| `serverKey` | `[a-z][a-z0-9]{7}`. ConfigMap data 에 그대로 넣습니다 |
