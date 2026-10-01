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
