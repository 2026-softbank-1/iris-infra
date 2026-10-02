{{- define "iris-platform.name" -}}{{ .Release.Name }}{{- end -}}
{{- define "iris-platform.labels" -}}
app.kubernetes.io/name: iris-platform
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- /* WAS image for one component; args: root, digest (each component is deployed separately). */ -}}
{{- define "iris-platform.image" -}}
{{ .root.Values.was.image.repository }}@{{ .digest }}
{{- end -}}
{{- define "iris-platform.database-env" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .secret | quote }}
      key: {{ .root.Values.database.urlKey | quote }}
- name: PGSSLMODE
  value: verify-full
- name: PGSSLROOTCERT
  value: /etc/iris-rds/ca-bundle.pem
{{- end -}}
{{- define "iris-platform.database-volume" -}}
- name: rds-ca
  configMap:
    name: {{ .Values.database.caConfigMap | quote }}
    items:
      - key: {{ .Values.database.caKey | quote }}
        path: ca-bundle.pem
{{- end -}}
{{- define "iris-platform.database-mount" -}}
- name: rds-ca
  mountPath: /etc/iris-rds
  readOnly: true
{{- end -}}
{{- define "iris-platform.security" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
{{- end -}}
{{- define "iris-platform.database-guard" -}}
- name: validate-database-tls
  image: {{ include "iris-platform.image" (dict "root" .root "digest" .digest) | quote }}
  imagePullPolicy: IfNotPresent
  command: [python, -c]
  args:
    - |
      import os, ssl, sys
      from sqlalchemy.engine import make_url
      try:
          url = make_url(os.environ["DATABASE_URL"])
          forbidden = {"ssl", "sslmode", "sslrootcert", "sslcert", "sslkey", "sslcrl", "sslpassword"}
          if url.drivername != "postgresql+asyncpg" or forbidden.intersection(k.lower() for k in url.query):
              raise ValueError()
          if os.environ["PGSSLMODE"] != "verify-full":
              raise ValueError()
          ssl.create_default_context(cafile=os.environ["PGSSLROOTCERT"])
      except Exception:
          sys.exit("Invalid database TLS configuration; check URL format and CA mount.")
  env:
    {{- include "iris-platform.database-env" . | nindent 4 }}
  volumeMounts:
    {{- include "iris-platform.database-mount" .root | nindent 4 }}
  securityContext:
    {{- include "iris-platform.security" .root | nindent 4 }}
  resources:
    requests: {cpu: 50m, memory: 64Mi}
    limits: {cpu: 100m, memory: 128Mi}
{{- end -}}
