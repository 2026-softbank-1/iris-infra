#!/usr/bin/env python3
"""Validate staged and fixture-enabled traffic charts without an image publication.

Fixture digests are NEVER deployment digests. The published collector image/lock
must be checked separately before enabling the production GitOps Application.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get("HELM", "helm")


def command(*args):
    if args[0] in {"template", "lint"}:
        args = (*args, "--kube-version", "1.35.0")
    if args[0] == "template":
        args = (*args, "--api-versions", "monitoring.coreos.com/v1/ServiceMonitor",
                "--api-versions", "monitoring.grafana.com/v1alpha1/MetricsInstance")
    return subprocess.check_output([HELM, *map(str, args)], text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loki-chart")
    parser.add_argument("--loki-binary", help="Verify the fully rendered config with Loki 3.6.12")
    parser.add_argument("--deployment", action="store_true", help="Require the real collector digest in values and image lock")
    args = parser.parse_args()
    version = json.loads((ROOT / "helm/versions.json").read_text())["helm"]
    assert command("version", "--short").startswith("v"+version+"+")
    with tempfile.TemporaryDirectory(prefix="iris-alb-render-") as temporary:
        directory = Path(temporary)
        chart = ROOT / "helm/charts/iris-alb-log-collector"
        cluster = ROOT / "clusters/aws-dev-management/values/alb-log-collector.yaml"
        actual = yaml.safe_load(cluster.read_text())
        if args.deployment:
            digest = actual["image"]["digest"]
            lock = json.loads((ROOT / "helm/images.lock.json").read_text())["images"]
            assert digest and lock.get(actual["image"]["repository"]) == digest, "Publish and lock the actual image first"
            actual_docs = list(yaml.safe_load_all(command("template", "alb-log-collector", chart, "-f", cluster, "--set", "enabled=true", "--namespace", "observability")))
            image = next(d for d in actual_docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"]
            assert image == actual["image"]["repository"]+"@"+digest
        else:
            assert not actual["enabled"], "Use --deployment when enabling the actual collector"
            assert not command("template", "alb-log-collector", chart, "-f", cluster).strip(), "Staged collector must not deploy"
        fixture = directory / "fixture.yaml"
        fixture.write_text(json.dumps({"enabled": True, "image": {"digest": "sha256:"+"a"*64}}))
        command("lint", "--strict", chart, "-f", cluster, "-f", fixture)
        docs = list(yaml.safe_load_all(command("template", "alb-log-collector", chart, "-f", cluster, "-f", fixture, "--namespace", "observability")))
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        assert deployment["spec"]["replicas"] == 1 and deployment["spec"]["strategy"] == {"type": "Recreate"}
        pod = deployment["spec"]["template"]["spec"]
        assert pod["serviceAccountName"] == "alb-log-collector" and pod["containers"][0]["securityContext"]["readOnlyRootFilesystem"]
        assert next(d for d in docs if d["kind"] == "PersistentVolumeClaim")["spec"]["resources"]["requests"]["storage"] == "1Gi"
        assert next(d for d in docs if d["kind"] == "Service")["spec"]["type"] == "ClusterIP"
        assert next(d for d in docs if d["kind"] == "ServiceMonitor")["metadata"]["labels"] == {"release": "monitoring"}
        rules = yaml.safe_load(next(d for d in docs if d["kind"] == "ConfigMap")["data"]["traffic.yaml"])
        assert len(rules["groups"]) == 1 and rules["groups"][0]["interval"] == "1m"
        assert len(rules["groups"][0]["rules"]) == 8 and all("offset 15m" in r["expr"] for r in rules["groups"][0]["rules"])
        bad = directory / "bad.yaml"
        # Explicitly override a published digest inherited from cluster values.
        bad.write_text('enabled: true\nimage:\n  digest: ""\n')
        result = subprocess.run([HELM, "template", "bad", str(chart), "-f", str(cluster), "-f", str(bad)], capture_output=True)
        assert result.returncode, "Enabling without a digest must fail"

        targets = {p: {"name": "iris-dev-"+p, "region": "ap-northeast-2", "vpc_id": "vpc-0123456789abcdef0", "endpoint": f"https://{p}.eks.amazonaws.com"} for p in ("management", "workload")}
        gitops = directory / "gitops.json"
        gitops.write_text(json.dumps({"revision": "a"*40, "targets": targets, "albTraffic": {"enabled": True}}))
        apps = list(yaml.safe_load_all(command("template", "gitops", ROOT/"helm/gitops", "-f", gitops)))
        app = next(d for d in apps if d and d["kind"] == "Application" and d["metadata"]["name"] == "iris-management-alb-log-collector")
        assert app["spec"]["destination"]["namespace"] == "observability" and app["spec"]["source"]["path"] == "helm/charts/iris-alb-log-collector"
        loki_app = next(d for d in apps if d and d["kind"] == "Application" and d["metadata"]["name"] == "iris-management-loki")
        assert loki_app["spec"]["sources"][0]["helm"]["valueFiles"][-1].endswith("loki-alb-traffic.yaml")
        overlay = directory / "anchor.json"
        overlay.write_text(json.dumps({"externalAlb": {"accessLogs": {"enabled": True}}}))
        anchors = list(yaml.safe_load_all(command("template", "baseline", ROOT/"helm/charts/cluster-baseline", "-f", ROOT/"clusters/aws-dev-workload/values/baseline.yaml", "-f", overlay)))
        anchor = next(d for d in anchors if d["kind"] == "Ingress")
        attributes = anchor["metadata"]["annotations"]["alb.ingress.kubernetes.io/load-balancer-attributes"]
        assert attributes == "access_logs.s3.enabled=true,access_logs.s3.bucket=iris-dev-alb-access-logs-187069338876-ap-northeast-2,access_logs.s3.prefix=alb/workload"

        if args.loki_chart:
            loki_chart = Path(args.loki_chart)
        else:
            command("pull", "loki", "--repo", "https://grafana.github.io/helm-charts", "--version", "7.3.0", "--untar", "--untardir", directory)
            loki_chart = directory / "loki"
        docs = list(yaml.safe_load_all(command("template", "loki", loki_chart, "-f", ROOT/"clusters/aws-dev-management/values/loki.yaml", "-f", ROOT/"clusters/aws-dev-management/values/loki-alb-traffic.yaml", "--namespace", "observability")))
        loki = next(d for d in docs if d["kind"] == "StatefulSet")
        pod = loki["spec"]["template"]["spec"]
        volume = next(v for v in pod["volumes"] if v["name"] == "alb-traffic-rules")
        assert volume["configMap"]["items"] == [{"key": "traffic.yaml", "path": "fake/traffic.yaml"}]
        mount = next(v for v in pod["containers"][0]["volumeMounts"] if v["name"] == "alb-traffic-rules")
        assert mount["readOnly"] and mount["mountPath"] == "/etc/loki/alb-rules"
        configs = [d["data"] for d in docs if d["kind"] == "ConfigMap"]
        loki_config = yaml.safe_load(next(data["config.yaml"] for data in configs if "config.yaml" in data))
        ruler = loki_config["ruler"]
        assert ruler["storage"] == {"type": "local", "local": {"directory": "/etc/loki/alb-rules"}}
        assert ruler["wal"]["dir"] == "/var/loki/ruler-wal" and ruler["rule_path"] == "/var/loki/ruler-work"
        assert ruler["remote_write"]["clients"]["prometheus"]["url"] == "http://monitoring-prometheus.observability:9090/api/v1/write"
        assert any(m["name"] == "storage" and m["mountPath"] == "/var/loki" for m in pod["containers"][0]["volumeMounts"])
        assert any(d["kind"] == "ServiceMonitor" for d in docs)
        assert not any(d["kind"] in {"GrafanaAgent", "MetricsInstance", "LogsInstance"} for d in docs)
        if args.loki_binary:
            version = subprocess.check_output([args.loki_binary, "--version"], stderr=subprocess.STDOUT, text=True)
            assert "version 3.6.12" in version
            config_file = directory / "loki-config.yaml"
            config_file.write_text(yaml.safe_dump(loki_config))
            subprocess.run([args.loki_binary, "-config.file="+str(config_file), "-verify-config=true"], check=True)
    print("ALB chart fixture, staged gates, anchor, Loki rule mount/WAL/remote write: passed. Published image and runtime deployment unverified.")


if __name__ == "__main__":
    main()
