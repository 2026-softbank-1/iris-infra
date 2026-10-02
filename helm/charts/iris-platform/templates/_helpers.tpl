{{- define "iris-platform.labels" -}}
app.kubernetes.io/part-of: iris-platform
helm.sh/chart: iris-platform-{{ .Chart.Version }}
{{- end }}

{{- define "iris-platform.image" -}}
{{ .root.Values.imageRepository }}@{{ .digest }}
{{- end }}

{{- define "iris-platform.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "iris-platform.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1001
runAsGroup: 1001
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "iris-platform.env" -}}
{{- range $k, $v := merge (dict) .component.env .root.Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end }}

{{- /* One Deployment per component; args: root, name, component. */ -}}
{{- define "iris-platform.deployment" -}}
{{- $c := .component -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .name }}
  labels:
    {{- include "iris-platform.labels" .root | nindent 4 }}
    app.kubernetes.io/name: {{ .name }}
spec:
  replicas: {{ $c.replicas }}
  revisionHistoryLimit: 3
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ .name }}
  strategy:
    type: RollingUpdate
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ .name }}
        app.kubernetes.io/part-of: iris-platform
    spec:
      serviceAccountName: {{ $c.serviceAccountName }}
      securityContext:
        {{- include "iris-platform.podSecurityContext" . | nindent 8 }}
      # Workers stop gracefully on SIGTERM after finishing the current job.
      terminationGracePeriodSeconds: 60
      containers:
        - name: app
          image: {{ include "iris-platform.image" (dict "root" .root "digest" $c.digest) | quote }}
          {{- with $c.command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with $c.port }}
          ports:
            - name: http
              containerPort: {{ . }}
          readinessProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /healthz, port: http}
            periodSeconds: 20
            failureThreshold: 3
          {{- end }}
          env:
            {{- include "iris-platform.env" (dict "root" .root "component" $c) | nindent 12 }}
          envFrom:
            - secretRef:
                name: {{ $c.secretName }}
          resources:
            {{- toYaml $c.resources | nindent 12 }}
          securityContext:
            {{- include "iris-platform.securityContext" . | nindent 12 }}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: tmp
          emptyDir: {}
{{- end }}
