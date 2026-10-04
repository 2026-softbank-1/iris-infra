"""Reconcile only reserved GCP pull Secrets using short-lived Google/AWS identity.

No ambient AWS credential chain, Secret creation, user Secret reads, or token logs.
AWS verifies the Google JWT signature. Local claim checks reject wrong identities
before asking STS. Kubernetes resourceVersion prevents overwriting concurrent edits.
"""
import base64
import http.server
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

SECRET = "iris-ecr-pull"
LABEL = "iris.dev/registry-pull"
EXPIRY = "iris.dev/expires-at"
REGION = "ap-northeast-2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_claims(token, subject, audience, now):
    raw = token.split(".")
    require(len(raw) == 3, "Invalid identity token")
    claims = json.loads(base64.urlsafe_b64decode(raw[1] + "=" * (-len(raw[1]) % 4)))
    require(claims.get("iss") in ("https://accounts.google.com", "accounts.google.com"), "Invalid issuer")
    require(claims.get("sub") == subject and claims.get("azp") == subject, "Wrong identity")
    require(claims.get("aud") == audience and claims.get("exp", 0) > now + 60, "Wrong audience or expired token")


class AWS:
    def __init__(self, account, role, subject, audience):
        require(re.fullmatch(r"[0-9]{12}", account), "Invalid AWS account")
        require(role == f"arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull", "Unexpected pull role")
        require(re.fullmatch(r"[0-9]{21}", subject), "Invalid Google service account ID")
        require(bool(audience) and len(audience) < 256, "Invalid audience")
        self.account, self.role, self.subject, self.audience = account, role, subject, audience
        self.registry = f"{account}.dkr.ecr.{REGION}.amazonaws.com"

    def fetch(self):
        # Metadata requests must never follow redirects or use an HTTP proxy.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        url = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/identity?" + urllib.parse.urlencode({"audience": self.audience})
        request = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with opener.open(request, timeout=10) as response:
            token = response.read(32768).decode().strip()
        validate_claims(token, self.subject, self.audience, time.time())
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
        config = dict(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3})
        sts = boto3.client("sts", region_name=REGION, aws_access_key_id="unused", aws_secret_access_key="unused", config=Config(signature_version=UNSIGNED, **config))
        result = sts.assume_role_with_web_identity(RoleArn=self.role, RoleSessionName="iris-gcp-ecr", WebIdentityToken=token, DurationSeconds=3600)
        credentials = result["Credentials"]
        ecr = boto3.client("ecr", region_name=REGION, aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"], aws_session_token=credentials["SessionToken"], config=Config(**config))
        auth = ecr.get_authorization_token(registryIds=[self.account])["authorizationData"]
        require(len(auth) == 1 and auth[0]["proxyEndpoint"] == "https://" + self.registry, "Wrong registry response")
        expiry = int(auth[0]["expiresAt"].timestamp())
        require(expiry > time.time() + 3600, "Insufficient credential lifetime")
        data = json.dumps({"auths": {self.registry: {"auth": auth[0]["authorizationToken"]}}}, separators=(",", ":"))
        return base64.b64encode(data.encode()).decode(), expiry


class Kubernetes:
    def __init__(self):
        self.token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        self.context = ssl.create_default_context(cafile=ca)
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        require(re.fullmatch(r"[0-9.]+", host), "Unexpected Kubernetes API address")
        self.base = f"https://{host}:{os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')}"

    def call(self, path, method="GET", body=None):
        headers = {"Authorization": "Bearer " + self.token.read_text().strip()}
        if body is not None:
            headers["Content-Type"] = "application/merge-patch+json"
        request = urllib.request.Request(self.base + path, method=method, headers=headers, data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, context=self.context, timeout=15) as response:
            return json.load(response)

    def namespaces(self):
        query = {"labelSelector": LABEL + "=gcp", "limit": "100"}
        while True:
            result = self.call("/api/v1/namespaces?" + urllib.parse.urlencode(query))
            yield from result["items"]
            cursor = result.get("metadata", {}).get("continue")
            if not cursor:
                return
            query["continue"] = cursor

    def secret(self, namespace):
        return self.call(f"/api/v1/namespaces/{namespace}/secrets/{SECRET}")

    def patch(self, namespace, secret, data, expiry):
        return self.call(f"/api/v1/namespaces/{namespace}/secrets/{SECRET}", "PATCH", {
            "metadata": {"resourceVersion": secret["metadata"]["resourceVersion"], "annotations": {EXPIRY: str(expiry)}},
            "data": {".dockerconfigjson": data},
        })


def selected(namespace, controller_namespace):
    meta = namespace["metadata"]
    return (meta.get("labels", {}).get(LABEL) == "gcp" and not meta.get("deletionTimestamp") and
            (meta["name"] == controller_namespace or
             (re.fullmatch(r"svc-[0-9]+", meta["name"]) and meta.get("labels", {}).get("iris.dev/target") == "gcp-dev-workload")))


def validate_secret(secret):
    require(secret["metadata"]["name"] == SECRET and secret["metadata"].get("labels", {}).get(LABEL) == "gcp", "Unmanaged Secret")
    require(secret.get("type") == "kubernetes.io/dockerconfigjson", "Wrong Secret type")


class Reconciler:
    def __init__(self, aws, kube, namespace="iris-system", clock=time.time):
        require(namespace == "iris-system", "Unexpected controller namespace")
        self.aws, self.kube, self.namespace, self.clock = aws, kube, namespace, clock
        self.data, self.expiry, self.refresh_at = None, 0, 0
        self.own_expiry, self.failures = 0, 0

    def tick(self):
        now = self.clock()
        if now >= self.refresh_at:
            # Never replace usable cached values if fetching fresh credentials fails.
            try:
                data, expiry = self.aws.fetch()
                require(expiry > now + 3600, "Credential expires too soon")
                self.data, self.expiry, self.refresh_at = data, expiry, now + 3600
            except Exception:
                self.failures += 1
                print("credential_refresh_failed", flush=True)
        for namespace in self.kube.namespaces():
            if not selected(namespace, self.namespace):
                continue
            name = namespace["metadata"]["name"]
            try:
                secret = self.kube.secret(name)
                validate_secret(secret)
                current_expiry = int(secret["metadata"].get("annotations", {}).get(EXPIRY, "0"))
                if self.data and self.expiry > now + 300 and (current_expiry < self.expiry or secret.get("data", {}).get(".dockerconfigjson") != self.data):
                    self.kube.patch(name, secret, self.data, self.expiry)
                    current_expiry = self.expiry
                if name == self.namespace:
                    self.own_expiry = current_expiry
            except Exception:
                self.failures += 1
                # Log only a stable event, never exception text or API responses.
                print("pull_secret_reconcile_failed", flush=True)

    def ready(self):
        return self.own_expiry > self.clock() + 300


def main():
    reconciler = Reconciler(AWS(os.environ["AWS_ACCOUNT_ID"], os.environ["AWS_ROLE_ARN"], os.environ["GOOGLE_SERVICE_ACCOUNT_ID"], os.environ["ID_TOKEN_AUDIENCE"]), Kubernetes(), os.environ.get("CONTROLLER_NAMESPACE", "iris-system"))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code = 200 if self.path == "/livez" or (self.path == "/readyz" and reconciler.ready()) else 503
            text = f"iris_ecr_reconcile_failures_total {reconciler.failures}\niris_ecr_pull_expires_at {reconciler.own_expiry}\n"
            self.send_response(200 if self.path == "/metrics" else code)
            self.end_headers()
            self.wfile.write(text.encode())
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        try:
            reconciler.tick()
        except Exception:
            reconciler.failures += 1
            print("namespace_discovery_failed", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
