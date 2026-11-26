{{- define "agent-tessera.fullname" -}}
{{- .Release.Name }}-agent-tessera
{{- end }}

{{- define "agent-tessera.labels" -}}
app.kubernetes.io/name: agent-tessera
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{- define "agent-tessera.secretName" -}}
{{- if .Values.kek.existingSecret }}
{{- .Values.kek.existingSecret }}
{{- else }}
{{- include "agent-tessera.fullname" . }}
{{- end }}
{{- end }}
