{{/*
Generic service deployment template for request-manager and agent-service.
Usage: {{ include "partner-agent.serviceDeployment" (dict "serviceName" "request-manager" "serviceConfig" .Values.requestManager "imageKey" "requestManager" "context" .) }}
*/}}
{{- define "partner-agent.serviceDeployment" -}}
{{- $serviceName := .serviceName -}}
{{- $serviceConfig := .serviceConfig -}}
{{- $imageKey := .imageKey -}}
{{- $context := .context -}}
{{- $fullName := include "partner-agent.fullname" $context -}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
    component: {{ $serviceName }}
spec:
  replicas: {{ $serviceConfig.replicas | default 1 }}
  selector:
    matchLabels:
      {{- include "partner-agent.selectorLabels" $context | nindent 6 }}
      app: {{ $fullName }}-{{ $serviceName }}
  template:
    metadata:
      labels:
        {{- include "partner-agent.labels" $context | nindent 8 }}
        app: {{ $fullName }}-{{ $serviceName }}
        app.kubernetes.io/component: {{ $serviceName }}
        component: {{ $serviceName }}
    spec:
      serviceAccountName: {{ include "partner-agent.serviceAccountName" $context }}
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      initContainers:
      - name: wait-for-keycloak
        image: "{{ $context.Values.image.registry }}/{{ index $context.Values.image $imageKey }}:{{ $context.Values.image.tag | default $context.Chart.AppVersion }}"
        imagePullPolicy: {{ $context.Values.image.pullPolicy }}
        command: ["python3", "-c"]
        args:
          - |
            import json, os, sys, time, urllib.request, urllib.error
            KC = os.environ.get("KEYCLOAK_URL", "")
            if not KC:
                print("No KEYCLOAK_URL — skipping init")
                sys.exit(0)
            ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USERNAME", "admin")
            ADMIN_PASS = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin123")
            print(f"Waiting for Keycloak at {KC}...")
            for i in range(120):
                try:
                    urllib.request.urlopen(f"{KC}/realms/partner-agent", timeout=5)
                    break
                except Exception:
                    if i == 119:
                        print("Keycloak not ready", file=sys.stderr)
                        sys.exit(1)
                    time.sleep(2)
            print("Keycloak ready, fetching client secret...")
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    f"{KC}/realms/master/protocol/openid-connect/token",
                    data=f"client_id=admin-cli&grant_type=password&username={ADMIN_USER}&password={ADMIN_PASS}".encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30)
                token = json.loads(r.read())["access_token"]
                r2 = urllib.request.urlopen(urllib.request.Request(
                    f"{KC}/admin/realms/partner-agent/clients?clientId=partner-agent-ui",
                    headers={"Authorization": f"Bearer {token}"}), timeout=30)
                clients = json.loads(r2.read())
                if not clients:
                    print("Client not found, skipping"); sys.exit(0)
                cid = clients[0]["id"]
                r3 = urllib.request.urlopen(urllib.request.Request(
                    f"{KC}/admin/realms/partner-agent/clients/{cid}/client-secret",
                    headers={"Authorization": f"Bearer {token}"}), timeout=30)
                secret = json.loads(r3.read()).get("value", "")
                with open("/tmp/kc-init/client-secret", "w") as f:
                    f.write(secret)
                print(f"Client secret written ({secret[:10]}...)")
            except Exception as e:
                print(f"Warning: {e}")
                with open("/tmp/kc-init/client-secret", "w") as f:
                    f.write("")
        env:
        - name: KEYCLOAK_URL
          value: "http://{{ $fullName }}-keycloak:8080"
        {{- include "partner-agent.keycloakAdminEnvVars" $context | nindent 8 }}
        volumeMounts:
        - name: kc-init
          mountPath: /tmp/kc-init
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop:
            - ALL
          runAsNonRoot: true
          seccompProfile:
            type: RuntimeDefault
      containers:
      - name: {{ $serviceName }}
        image: "{{ $context.Values.image.registry }}/{{ index $context.Values.image $imageKey }}:{{ $context.Values.image.tag | default $context.Chart.AppVersion }}"
        imagePullPolicy: {{ $context.Values.image.pullPolicy }}
        command: ["/bin/sh", "-c"]
        args:
          - |
            if [ -f /tmp/kc-init/client-secret ]; then
              export KEYCLOAK_CLIENT_SECRET=$(cat /tmp/kc-init/client-secret)
            fi
            exec python3 -m uvicorn {{ if eq $serviceName "request-manager" }}request_manager.main:app{{ else }}agent_service.main:app{{ end }} --host 0.0.0.0 --port 8080 --workers ${UVICORN_WORKERS:-4}
        ports:
        - containerPort: 8080
          protocol: TCP
          name: http
        env:
        {{- if eq $serviceName "request-manager" }}
        {{- include "partner-agent.requestManagerEnvVars" $context | nindent 8 }}
        {{- else if eq $serviceName "agent-service" }}
        {{- include "partner-agent.agentServiceEnvVars" $context | nindent 8 }}
        {{- end }}
        {{- if $serviceConfig.uvicornWorkers }}
        - name: UVICORN_WORKERS
          value: {{ $serviceConfig.uvicornWorkers | quote }}
        {{- end }}
        volumeMounts:
        - name: kc-init
          mountPath: /tmp/kc-init
          readOnly: true
        - name: agent-capabilities
          mountPath: /etc/praxis/agent_capabilities.yaml
          subPath: agent_capabilities.yaml
          readOnly: true
        {{- if eq $serviceName "agent-service" }}
        - name: agent-config
          mountPath: /app/config/agents/kubernetes-support-agent.yaml
          subPath: kubernetes-support-agent.yaml
          readOnly: true
        {{- end }}
        {{- if $context.Values.spire.enabled }}
        - name: spire-agent-socket
          mountPath: /run/spire/sockets
          readOnly: true
        {{- end }}
        {{- include "partner-agent.spireVolumeMount" $context | nindent 8 }}
        {{- if $serviceConfig.resources }}
        resources:
          {{- toYaml $serviceConfig.resources | nindent 10 }}
        {{- end }}
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop:
            - ALL
          runAsNonRoot: true
          seccompProfile:
            type: RuntimeDefault
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 3
          failureThreshold: 3
        startupProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
          timeoutSeconds: 5
          failureThreshold: 30
      volumes:
      - name: kc-init
        emptyDir: {}
      - name: agent-capabilities
        configMap:
          name: {{ $fullName }}-agent-capabilities
      {{- if eq $serviceName "agent-service" }}
      - name: agent-config
        configMap:
          name: {{ $fullName }}-agent-config
      {{- end }}
      {{- if $context.Values.spire.enabled }}
      - name: spire-agent-socket
        {{- if $context.Values.spire.csiDriver }}
        csi:
          driver: "csi.spiffe.io"
          readOnly: true
        {{- else }}
        hostPath:
          path: /run/spire/sockets
          type: DirectoryOrCreate
        {{- end }}
      {{- end }}
      {{- include "partner-agent.spireVolume" $context | nindent 6 }}
      restartPolicy: Always
      terminationGracePeriodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
spec:
  type: ClusterIP
  selector:
    {{- include "partner-agent.selectorLabels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
  ports:
  - name: http
    port: 80
    targetPort: 8080
    protocol: TCP
{{- if $serviceConfig.autoscaling.enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $fullName }}-{{ $serviceName }}
  namespace: {{ $context.Release.Namespace }}
  labels:
    {{- include "partner-agent.labels" $context | nindent 4 }}
    app: {{ $fullName }}-{{ $serviceName }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ $fullName }}-{{ $serviceName }}
  minReplicas: {{ $serviceConfig.autoscaling.minReplicas | default 1 }}
  maxReplicas: {{ $serviceConfig.autoscaling.maxReplicas | default 10 }}
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: {{ $serviceConfig.autoscaling.targetCPUUtilization | default 70 }}
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: {{ $serviceConfig.autoscaling.targetMemoryUtilization | default 80 }}
{{- end }}
{{- end }}
