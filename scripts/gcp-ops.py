#!/usr/bin/env python3
"""GCP metadata, explicit access/bootstrap, and read-only operational checks.

No infrastructure apply, DNS edits, image/tag publication, or key generation.
Credentials and Kubernetes Secrets travel only through subprocess stdin/memory.
"""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = ROOT / ".generated/gcp-target.json"


def need(condition, message):
    if not condition:
        raise ValueError(message)


def run(args, *, input=None, env=None):
    result = subprocess.run(args, input=input, env=env, text=True, capture_output=True, timeout=120)
    need(result.returncode == 0, f"{Path(args[0]).name} operation failed; response suppressed")
    return result.stdout


def private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.replace(path)


def project():
    value = os.environ.get("GCP_PROJECT_ID", "")
    need(re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", value), "Set GCP_PROJECT_ID explicitly")
    return value


def validate(t, expected):
    need(t.get("schema_version") == 1 and t.get("id") == "gcp-dev-workload", "Wrong GCP target")
    need(t.get("project_id") == expected, "GCP project mismatch")
    need(t.get("location") == "asia-northeast3-a" and t.get("region") == "asia-northeast3", "Unexpected location")
    need(t.get("name") == "gcp-dev-workload" and t.get("kube_context") == "iris-gcp-dev-workload", "Unexpected cluster/context")
    need(re.fullmatch(r"https://[a-z0-9.-]+\.gke\.goog", t.get("endpoint", "")), "Expected GKE DNS endpoint")
    need(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", t.get("base_domain", "")), "Invalid base domain")
    ipaddress.ip_address(t["ip_address"])
    need(t["network"] == t["subnet"] == "gcp-dev-workload", "Unexpected network")
    need(t["argocd_service_account"] == f"iris-gcp-argocd@{expected}.iam.gserviceaccount.com", "Wrong Argo identity")
    need(t["ecr_service_account"] == f"iris-gcp-ecr@{expected}.iam.gserviceaccount.com" and re.fullmatch(r"[0-9]{21}", t["ecr_service_account_id"]), "Wrong pull identity")
    need(re.fullmatch(r"projects/[0-9]+/locations/global/workloadIdentityPools/iris-argocd/providers/management-eks", t["wif_provider"]), "Unexpected federation provider")
    for name in ("address_name", "certificate_map", "certificate_name"):
        need(t[name] == "iris-gcp-apps", "Unexpected application infrastructure name")
    need(t["sealed_key_secret"] == "iris-gcp-sealed-secrets-key", "Unexpected key backup name")
    need(len(t["denied_cidrs"]) == 4 and "169.254.0.0/16" in t["denied_cidrs"], "Missing metadata isolation")
    for cidr in t["denied_cidrs"]:
        need(ipaddress.ip_network(cidr).is_private or cidr == "169.254.0.0/16", "Unexpected denied network")
    # Export only the infrastructure contract; reject credentials or unknown fields.
    allowed = {"schema_version", "id", "project_id", "location", "region", "name", "endpoint", "kube_context", "network", "subnet", "denied_cidrs", "base_domain", "ip_address", "address_name", "certificate_map", "certificate_name", "dns_authorization", "argocd_service_account", "wif_provider", "ecr_service_account", "ecr_service_account_id", "ecr_audience", "sealed_key_secret"}
    need(set(t) == allowed, "Unexpected or missing GCP target field")
    return t


def load():
    return validate(json.loads(TARGET_FILE.read_text()), project())


def cloud(t, *args):
    return json.loads(run(["gcloud", *args, "--project", t["project_id"], "--format=json"]))


def preflight(t):
    c = cloud(t, "container", "clusters", "describe", t["name"], "--location", t["location"])
    need(c["status"] == "RUNNING" and c["name"] == t["name"], "Cluster not running")
    need(c["network"].rsplit("/", 1)[-1] == t["network"] and c["subnetwork"].rsplit("/", 1)[-1] == t["subnet"], "Cluster network mismatch")
    need(c.get("privateClusterConfig", {}).get("enablePrivateNodes"), "Public nodes are not allowed")
    endpoints = c["controlPlaneEndpointsConfig"]
    need("https://" + endpoints["dnsEndpointConfig"]["endpoint"] == t["endpoint"] and endpoints["dnsEndpointConfig"].get("allowExternalTraffic"), "DNS endpoint changed")
    need(not endpoints.get("ipEndpointsConfig", {}).get("enabled", True), "IP API access must remain disabled")
    need(c.get("datapathProvider") == "ADVANCED_DATAPATH" and c.get("workloadIdentityConfig", {}).get("workloadPool") == t["project_id"] + ".svc.id.goog", "Dataplane/identity mismatch")
    need(c.get("gatewayApiConfig", {}).get("channel") == "CHANNEL_STANDARD", "Gateway API not enabled")
    account = cloud(t, "iam", "service-accounts", "describe", t["ecr_service_account"])
    need(account["uniqueId"] == t["ecr_service_account_id"], "Pull service account changed")
    return c


def kube_path(t):
    return ROOT / ".generated/kubeconfig-gcp-dev-workload.json"


def kubectl(t, *args, input=None):
    path = kube_path(t)
    need(path.is_file(), "Run make gcp-access before Kubernetes operations")
    config = json.loads(run(["kubectl", "--kubeconfig", str(path), "config", "view", "--minify", "-o", "json"]))
    need(config["current-context"] == t["kube_context"] and config["clusters"][0]["cluster"]["server"] == t["endpoint"], "Wrong kubeconfig/context")
    tls = config["clusters"][0]["cluster"]
    need(not tls.get("insecure-skip-tls-verify") and not tls.get("certificate-authority-data") and not tls.get("certificate-authority"), "DNS endpoint must use system CA")
    return run(["kubectl", "--kubeconfig", str(path), "--context", t["kube_context"], *args], input=input)


def apply(t, objects):
    kubectl(t, "apply", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))


def credential_config(t):
    audience = "//iam.googleapis.com/" + t["wif_provider"]
    return {"type": "external_account", "audience": audience, "subject_token_type": "urn:ietf:params:oauth:token-type:jwt", "token_url": "https://sts.googleapis.com/v1/token", "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/" + t["argocd_service_account"] + ":generateAccessToken", "credential_source": {"file": "/var/run/gcp-identity/token", "format": {"type": "text"}}}


def argo_overlay(t):
    config = credential_config(t)
    volumes = [{"name": "gcp-identity", "projected": {"sources": [{"serviceAccountToken": {"audience": config["audience"], "expirationSeconds": 3600, "path": "token"}}]}}, {"name": "gcp-config", "configMap": {"name": "iris-gcp-identity"}}]
    mounts = [{"name": "gcp-identity", "mountPath": "/var/run/gcp-identity", "readOnly": True}, {"name": "gcp-config", "mountPath": "/etc/gcp-identity", "readOnly": True}]
    component = {"env": [{"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/etc/gcp-identity/credentials.json"}], "volumes": volumes, "volumeMounts": mounts}
    return {"argo-cd": {"controller": component, "server": component}}


def cluster_secret(t):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "iris-gcp-workload-cluster", "namespace": "argocd", "labels": {"argocd.argoproj.io/secret-type": "cluster"}}, "type": "Opaque", "stringData": {"name": t["id"], "server": t["endpoint"], "config": json.dumps({"execProviderConfig": {"command": "argocd-k8s-auth", "args": ["gcp"], "apiVersion": "client.authentication.k8s.io/v1beta1"}, "tlsClientConfig": {"insecure": False}})}}


def identity_configmap(t):
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "iris-gcp-identity", "namespace": "argocd"}, "data": {"credentials.json": json.dumps(credential_config(t))}}


def bootstrap_gate(t, g, secret, now=time.time):
    need(g["enabled"] and g["credentials"]["enabled"], "Enable the credential reconciler explicitly")
    need(g["endpoint"] == t["endpoint"] and g["baseDomain"] == t["base_domain"] and g["deniedCidrs"] == t["denied_cidrs"], "GCP GitOps metadata mismatch")
    for field, key in (("addressName", "address_name"), ("certificateMap", "certificate_map")):
        need(g[field] == t[key], "GCP Gateway metadata mismatch")
    c = g["credentials"]
    account = c["awsAccountId"]
    need(re.fullmatch(r"[0-9]{12}", account) and c["roleArn"] == f"arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull", "Wrong AWS pull identity")
    need(c["serviceAccountEmail"] == t["ecr_service_account"] and c["serviceAccountId"] == t["ecr_service_account_id"] and c["audience"] == t["ecr_audience"], "Wrong Google pull identity")
    need(re.fullmatch(rf"{account}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/iris/gcp-ecr-credentials@sha256:[a-f0-9]{{64}}", c["image"]), "Publish and pin the reconciler image first")
    need(ipaddress.ip_network(c["kubeApiCidr"]).prefixlen == 32, "Pin the API endpoint address")
    need(g["chartRevision"] == "iris-service-0.10.0", "Unexpected GCP service chart revision")
    meta = secret["metadata"]
    need(meta["name"] == "iris-ecr-pull" and meta.get("labels", {}).get("iris.dev/registry-pull") == "gcp" and secret["type"] == "kubernetes.io/dockerconfigjson", "Invalid bootstrap pull Secret")
    need(int(meta.get("annotations", {}).get("iris.dev/expires-at", "0")) > now() + 3600, "Bootstrap credential lifetime too short")
    data = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    registry = f"{account}.dkr.ecr.ap-northeast-2.amazonaws.com"
    need(set(data["auths"]) == {registry} and bool(data["auths"][registry].get("auth")), "Invalid bootstrap registry credential")


def prepare(t, args):
    preflight(t)
    account = args.aws_account_id
    need(account and re.fullmatch(r"[0-9]{12}", account), "Specify --aws-account-id")
    identity = json.loads(run(["aws", "sts", "get-caller-identity", "--output", "json"]))
    need(identity["Account"] == account, "AWS operator account mismatch")
    image = args.image
    need(image and re.fullmatch(rf"{account}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/iris/gcp-ecr-credentials@sha256:[a-f0-9]{{64}}", image), "Specify the published reconciler image digest")
    image_info = json.loads(run(["aws", "ecr", "describe-images", "--region", "ap-northeast-2", "--registry-id", account, "--repository-name", "iris/gcp-ecr-credentials", "--image-ids", "imageDigest=" + image.rsplit("@", 1)[1], "--output", "json"]))
    need(len(image_info.get("imageDetails", [])) == 1, "Reconciler image unavailable")
    argo_objects = [{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "iris-argocd-management"}, "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "cluster-admin"}, "subjects": [{"kind": "User", "name": t["argocd_service_account"], "apiGroup": "rbac.authorization.k8s.io"}]}, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "iris-system", "labels": {"iris.dev/registry-pull": "gcp", "pod-security.kubernetes.io/enforce": "baseline"}}}]
    # Dataplane V2 ipBlock rules use the API endpoint, never a Service clusterIP.
    slices = json.loads(kubectl(t, "get", "endpointslices", "-n", "default", "-l", "kubernetes.io/service-name=kubernetes", "-o", "json"))["items"]
    addresses = {a for s in slices for e in s["endpoints"] for a in e["addresses"]}
    need(len(addresses) == 1, "Expected one control-plane API address")
    api = addresses.pop()
    need(ipaddress.ip_address(api).version == 4, "Expected IPv4 API address")
    g = {"enabled": True, "endpoint": t["endpoint"], "baseDomain": t["base_domain"], "addressName": t["address_name"], "certificateMap": t["certificate_map"], "deniedCidrs": t["denied_cidrs"], "chartRevision": "iris-service-0.10.0", "credentials": {"enabled": True, "image": image, "serviceAccountEmail": t["ecr_service_account"], "serviceAccountId": t["ecr_service_account_id"], "awsAccountId": account, "roleArn": f"arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull", "audience": t["ecr_audience"], "kubeApiCidr": api + "/32"}}
    auth = json.loads(run(["aws", "ecr", "get-authorization-token", "--region", "ap-northeast-2", "--registry-ids", account, "--output", "json"]))["authorizationData"][0]
    need(auth["proxyEndpoint"] == f"https://{account}.dkr.ecr.ap-northeast-2.amazonaws.com", "Wrong registry response")
    from datetime import datetime
    expiry = int(datetime.fromisoformat(auth["expiresAt"].replace("Z", "+00:00")).timestamp())
    data = base64.b64encode(json.dumps({"auths": {auth["proxyEndpoint"][8:]: {"auth": auth["authorizationToken"]}}}).encode()).decode()
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "iris-ecr-pull", "namespace": "iris-system", "labels": {"iris.dev/registry-pull": "gcp"}, "annotations": {"iris.dev/expires-at": str(expiry)}}, "type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": data}}
    bootstrap_gate(t, g, secret)
    # Validate inputs before mutation. The namespace must exist for Secret server
    # dry-run; a later API failure can leave a partial, repeatable bootstrap.
    kubectl(t, "apply", "--dry-run=server", "-f", "-", input=json.dumps({"apiVersion": "v1", "kind": "List", "items": argo_objects}))
    apply(t, argo_objects)
    kubectl(t, "apply", "--dry-run=server", "-f", "-", input=json.dumps(secret))
    apply(t, [secret])
    private(ROOT / ".generated/gcp-gitops.json", {"target": t, "gcp": g})
    print("GCP bootstrap identity and pull Secret prepared. Argo installation remains a separate operation.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("export", "preflight", "access", "bootstrap", "smoke", "backup-keys"))
    parser.add_argument("--aws-account-id")
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.action == "export":
        t = validate(json.loads(run(["terraform", "-chdir=" + str(ROOT / "terraform/environments/gcp/dev/workload"), "output", "-json", "target"])), project())
        private(TARGET_FILE, t)
        print("Exported GCP metadata; no credentials.")
        return
    t = load()
    if args.action == "preflight":
        preflight(t)
        print("GCP identity/network/API configuration verified; deployment not verified.")
    elif args.action == "access":
        preflight(t)
        path = kube_path(t)
        path.parent.mkdir(parents=True, exist_ok=True)
        # gcloud writes YAML initially, then canonicalize into private JSON.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        run(["gcloud", "container", "clusters", "get-credentials", t["name"], "--location", t["location"], "--project", t["project_id"], "--dns-endpoint"], env={**os.environ, "KUBECONFIG": str(path)})
        config = json.loads(run(["kubectl", "--kubeconfig", str(path), "config", "view", "--raw", "-o", "json"]))
        need(len(config["contexts"]) == 1, "Dedicated kubeconfig must have one context")
        config["contexts"][0]["name"] = t["kube_context"]
        config["current-context"] = t["kube_context"]
        private(path, config)
        kubectl(t, "get", "--raw", "/version")
        print("Created private GCP kubeconfig using public CA verification.")
    elif args.action == "bootstrap":
        prepare(t, args)
    elif args.action == "backup-keys":
        preflight(t)
        keys = json.loads(kubectl(t, "get", "secrets", "-n", "kube-system", "-l", "sealedsecrets.bitnami.com/sealed-secrets-key", "-o", "json"))
        need(keys.get("items") and all(k.get("data", {}).get("tls.key") and k.get("data", {}).get("tls.crt") for k in keys["items"]), "No sealing key pair found")
        run(["gcloud", "secrets", "versions", "add", t["sealed_key_secret"], "--project", t["project_id"], "--data-file=-"], input=json.dumps(keys))
        print("Sealing keys backed up; verify restoration separately before user-variable deployments.")
    else:
        preflight(t)
        nodes = json.loads(kubectl(t, "get", "nodes", "-o", "json"))["items"]
        need(1 <= len(nodes) <= 2 and all(any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"]) for n in nodes), "Nodes not Ready")
        gateway = json.loads(kubectl(t, "get", "gateway", "iris-gcp-apps", "-n", "iris-system", "-o", "json"))
        need(any(c["type"] == "Programmed" and c["status"] == "True" and c.get("observedGeneration") == gateway["metadata"]["generation"] for c in gateway.get("status", {}).get("conditions", [])), "Gateway not programmed")
        status = json.loads(kubectl(t, "get", "deployment", "ecr-credentials", "-n", "iris-system", "-o", "json"))
        need(status.get("status", {}).get("availableReplicas", 0) == 1, "Credential reconciler not available")
        print("Nodes, Gateway and credential reconciler are ready. HTTPS, rotation, rollback and isolation require the runbook exercises.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        print("GCP operation stopped: input or operation failed; sensitive responses suppressed.", file=sys.stderr)
        sys.exit(1)
