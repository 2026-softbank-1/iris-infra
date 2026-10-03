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
