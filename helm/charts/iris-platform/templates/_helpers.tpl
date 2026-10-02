{{- define "iris-platform.labels" -}}
app.kubernetes.io/part-of: iris-platform
helm.sh/chart: iris-platform-{{ .Chart.Version }}
{{- end }}

{{- define "iris-platform.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "iris-platform.podSecurityContext" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- /* One container spec shared by the Deployment and the migration Job; args: root, name, c. */ -}}
{{- define "iris-platform.container" -}}
image: {{ printf "%s@%s" .c.repository .c.digest | quote }}
envFrom:
  - secretRef:
      name: {{ .c.secretName | default (printf "iris-%s-env" .name) }}
{{- with merge (dict) (.c.env | default dict) .root.Values.env }}
env:
  {{- range $k, $v := . }}
  - name: {{ $k }}
    value: {{ $v | quote }}
  {{- end }}
{{- end }}
resources:
  {{- toYaml (.c.resources | default .root.Values.resources) | nindent 2 }}
securityContext:
  {{- include "iris-platform.securityContext" . | nindent 2 }}
volumeMounts:
  - {name: tmp, mountPath: /tmp}
{{- end }}
