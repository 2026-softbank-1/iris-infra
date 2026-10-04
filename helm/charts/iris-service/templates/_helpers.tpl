{{- define "iris-service.selectorLabels" -}}
app.kubernetes.io/name: iris-service
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "iris-service.labels" -}}
{{ include "iris-service.selectorLabels" . }}
helm.sh/chart: iris-service-{{ .Chart.Version }}
{{- end }}

{{- /* AWS 는 digest, 로컬 import 는 commit tag 를 씁니다. schema 가 둘 중 하나만 허용합니다. */ -}}
{{- define "iris-service.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end }}

{{- /* 카나리·블루그린은 Pod 가 2개 이상일 때만 뜻이 있어, 그보다 적으면 롤링으로 렌더링합니다. */ -}}
{{- define "iris-service.deploymentStrategy" -}}
{{- $strategy := .Values.deploymentStrategy | default "ROLLING" -}}
{{- if lt (int .Values.replicas) 2 -}}ROLLING{{- else -}}{{ $strategy }}{{- end -}}
{{- end }}

{{- /* 0.8.0: app(Rollout·Ingress) 또는 database(StatefulSet·PVC). 키가 없으면 app 입니다. */ -}}
{{- define "iris-service.isDatabase" -}}
{{- if eq (dig "kind" "app" (.Values.workload | default dict)) "database" -}}true{{- end -}}
{{- end }}

{{- /* 프로젝트 id 라벨 값. JSON 숫자는 float64 라 1e+06 꼴로 찍히지 않게 정수로 바꿉니다. */ -}}
{{- define "iris-service.projectId" -}}
{{- if kindIs "string" .Values.projectId -}}{{ .Values.projectId }}{{- else -}}{{ .Values.projectId | int64 }}{{- end -}}
{{- end }}

{{- /* 엔진별 고정 설정: 컨테이너가 듣는 포트, 데이터 디렉터리(PVC mount), 실행 uid. */ -}}
{{- define "iris-service.databaseNativePort" -}}
{{- get (dict "postgres" 5432 "mysql" 3306 "mongodb" 27017 "redis" 6379) .Values.database.engine -}}
{{- end }}

{{- define "iris-service.databasePort" -}}
{{- .Values.database.port | default (include "iris-service.databaseNativePort" .) | int -}}
{{- end }}

{{- define "iris-service.databaseMountPath" -}}
{{- get (dict "postgres" "/var/lib/postgresql/data" "mysql" "/var/lib/mysql" "mongodb" "/data/db" "redis" "/data") .Values.database.engine -}}
{{- end }}

{{- /* 공식 이미지의 엔진 사용자. postgres alpine 만 uid 70 이고 나머지(postgres debian·mysql·mongo·redis)는 999 입니다. */ -}}
{{- define "iris-service.databaseUid" -}}
{{- if and (eq .Values.database.engine "postgres") (contains "alpine" .Values.database.image) -}}70{{- else -}}999{{- end -}}
{{- end }}
