{{/* Every name derives from server.key. The data file repeats it in other fields; they must agree. */}}
{{- define "iris-onprem-server.key" -}}
{{- $key := .Values.server.key -}}
{{- if ne .Values.directoryKey $key -}}
{{- fail (printf "server.key %s must equal its directory platform/onprem-servers/%s" $key .Values.directoryKey) -}}
{{- end -}}
{{- if ne .Values.server.clusterName (printf "onprem-%s" $key) -}}
{{- fail (printf "server.clusterName must be onprem-%s" $key) -}}
{{- end -}}
{{- if not (hasPrefix (printf "iris-%s." $key) .Values.server.tailnetFqdn) -}}
{{- fail (printf "server.tailnetFqdn must start with iris-%s." $key) -}}
{{- end -}}
{{- $key -}}
{{- end -}}

{{- define "iris-onprem-server.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: iris-onprem-server
iris.dev/onprem-server: {{ include "iris-onprem-server.key" . }}
{{- end -}}
