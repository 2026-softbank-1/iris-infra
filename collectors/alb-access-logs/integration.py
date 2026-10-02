"""Real Loki/ruler/Prometheus gate. All data/processes live in a temporary directory.

Use official Loki 3.6.12 and Prometheus 3.15.0 binaries; never a dev endpoint.
The test accelerates rule evaluation to 1s but keeps production windows/offsets.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load("fixture", HERE / "test_collector.py")
c = fixtures.c


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(Request(url, data, {"Content-Type": "application/json"}), timeout=5) as response:
        body = response.read()
        return json.loads(body) if body.startswith(b"{") else body


def until(check, label, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except Exception as error:
            last = type(error).__name__
        time.sleep(0.2)
    raise AssertionError(f"Timed out: {label}; last error type={last}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loki-binary", required=True)
    parser.add_argument("--prometheus-binary", required=True)
    args = parser.parse_args()
    for executable, version in [(args.loki_binary, "3.6.12"), (args.prometheus_binary, "3.15.0")]:
        actual = subprocess.check_output([executable, "--version"], stderr=subprocess.STDOUT, text=True)
        assert f"version {version}" in actual, "Use pinned binaries"
    with tempfile.TemporaryDirectory(prefix="iris-alb-integration-") as temporary:
        directory = Path(temporary)
        loki_port, prometheus_port, grpc_port = port(), port(), port()
        loki_url, prometheus_url = f"http://127.0.0.1:{loki_port}", f"http://127.0.0.1:{prometheus_port}"
        rules = json.loads((ROOT / "helm/charts/iris-alb-log-collector/files/traffic.yaml").read_text())
        assert len(rules["groups"]) == 1 and rules["groups"][0]["interval"] == "1m"
        assert all("offset 15m" in rule["expr"] for rule in rules["groups"][0]["rules"])
        rules["groups"][0]["interval"] = "1s"
        (directory / "rules/fake").mkdir(parents=True)
        rule_file = directory / "rules/fake/traffic.yaml"
        rule_file.write_text(json.dumps(rules))
        config = {
            "auth_enabled": False,
            "server": {"http_listen_address": "127.0.0.1", "http_listen_port": loki_port, "grpc_listen_port": grpc_port},
            "common": {"path_prefix": str(directory / "loki"), "instance_addr": "127.0.0.1", "replication_factor": 1,
                       "ring": {"kvstore": {"store": "inmemory"}}, "storage": {"filesystem": {"chunks_directory": str(directory / "chunks"), "rules_directory": str(directory / "unused-rules")}}},
            "schema_config": {"configs": [{"from": "2020-01-01", "store": "tsdb", "object_store": "filesystem", "schema": "v13", "index": {"prefix": "index_", "period": "24h"}}]},
            "ingester": {"wal": {"dir": str(directory / "loki/wal")}},
            "ruler": {"storage": {"type": "local", "local": {"directory": str(directory / "rules")}},
                      "rule_path": str(directory / "loki/ruler-work"), "wal": {"dir": str(directory / "loki/ruler-wal")},
                      "evaluation_interval": "1s", "poll_interval": "1s",
                      "remote_write": {"enabled": True, "clients": {"prometheus": {"url": prometheus_url + "/api/v1/write", "queue_config": {"batch_send_deadline": "1s"}}}}},
            "analytics": {"reporting_enabled": False},
        }
        loki_config = directory / "loki.json"
        loki_config.write_text(json.dumps(config))
        prometheus_config = directory / "prometheus.json"
        prometheus_config.write_text(json.dumps({"global": {"scrape_interval": "1s"}, "scrape_configs": []}))
        children, logs = [], []

        def start_loki():
            log = open(directory / f"loki-{len(children)}.log", "w")
            logs.append(log)
            process = subprocess.Popen([args.loki_binary, "-config.file="+str(loki_config), "-log.level=warn"], stdout=log, stderr=log)
            children.append(process)
            until(lambda: process.poll() is None and http(loki_url+"/ready"), "Loki ready")
            return process

        def samples(metric, namespace="svc-1", **labels):
            selector = ",".join(f'{name}="{value}"' for name, value in {"k8s_namespace_name": namespace, **labels}.items())
            response = http(prometheus_url+"/api/v1/query?"+urlencode({"query": f"{metric}{{{selector}}}"}))
            return [(float(row["value"][0]), float(row["value"][1])) for row in response["data"]["result"]]

        def values(metric, namespace="svc-1", **labels):
            return [value for _, value in samples(metric, namespace, **labels)]

        def expect(metric, value, namespace="svc-1", **labels):
            until(lambda: any(abs(v-value) < 1e-6 for v in values(metric, namespace, **labels)), f"{metric}={value}", timeout=20)

        try:
            log = open(directory / "prometheus.log", "w")
            logs.append(log)
            process = subprocess.Popen([args.prometheus_binary, "--config.file="+str(prometheus_config), "--storage.tsdb.path="+str(directory/"prometheus"), "--web.listen-address=127.0.0.1:"+str(prometheus_port), "--web.enable-remote-write-receiver", "--log.level=warn"], stdout=log, stderr=log)
            children.append(process)
            until(lambda: process.poll() is None and http(prometheus_url+"/-/ready"), "Prometheus ready")
            loki_process = start_loki()
            state = c.State(str(directory / "checkpoint.sqlite"))
            config = {"bucket": "bucket", "prefix": "alb/workload/AWSLogs/123456789012/elasticloadbalancing/ap-northeast-2/", "queue": "queue", "dlq": "dlq", "group": "iris-service-external", "cluster": "iris-dev-workload", "account": "123456789012", "region": "ap-northeast-2", "state": str(directory/"checkpoint.sqlite"), "max_bytes": 1024*1024, "load_balancer": fixtures.LB}
            other_tg = fixtures.TG + "other"
            replacement_tg = fixtures.TG + "replace"
            # All initial events are in the 1m delayed window, not the live window.
            stamp = datetime.fromtimestamp(time.time()-901, timezone.utc).isoformat()
            source = fixtures.line(200, "0.1", stamp=stamp)+fixtures.line(404, "0.2", stamp=stamp)+fixtures.line(503, "-1", stamp=stamp)+fixtures.line(200, "0.4", target=other_tg, stamp=stamp)
            sqs, loki = fixtures.FakeSQS(), c.Loki(loki_url+"/loki/api/v1/push", "iris-dev-workload")
            worker = c.Collector(fixtures.FakeS3(source), sqs, fixtures.FakeELB(mappings={fixtures.TG: "svc-1", other_tg: "svc-2", replacement_tg: "svc-replace"}), state, loki, config)
            worker.refresh()
            message = {"ReceiptHandle": "receipt", "Body": json.dumps({"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "awsRegion": "ap-northeast-2", "s3": {"bucket": {"name": "bucket"}, "object": {"key": fixtures.KEY}}}]})}
            worker.process_message(message)
            expect("iris_service_requests_1m", 3)
            expect("iris_service_requests_1m", 1, "svc-2")
            expect("iris_service_errors_1m", 1, status_class="4xx")
            expect("iris_service_errors_1m", 1, status_class="5xx")
            expect("iris_service_errors_1m", 0, "svc-2", status_class="5xx")
            expect("iris_service_public_network_bytes_1m", 369, direction="receive")
            expect("iris_service_public_network_bytes_1m", 1368, direction="transmit")
            expect("iris_service_response_time_seconds_avg5m", 0.15)
            expect("iris_service_response_time_seconds_p50_5m", 0.15)
            expect("iris_service_response_time_seconds_p95_5m", 0.195)
            assert values("iris_service_requests_1m", "svc-absent") == [], "Missing must not become zero"
            # Verify production offset and adjacent minute boundaries at an exact event timestamp.
            event_ns = c.timestamp_ns(stamp)
            query = 'sum(count_over_time({job="iris-alb-access",k8s_namespace_name="svc-1"}[1m] offset 15m))'
            def at(ns):
                response = http(loki_url+"/loki/api/v1/query?"+urlencode({"query": query, "time": str(ns)}))
                return sum(float(row["value"][1]) for row in response["data"]["result"])
            assert at(event_ns+900*10**9) == 3, "Window end includes event"
            assert at(event_ns+960*10**9) == 0, "Adjacent minute must exclude its left boundary"
            rows = state.db.execute("SELECT id,timestamp,namespace,body FROM records ORDER BY id").fetchall()
            loki.push(rows)
            worker.process_message(message)  # Duplicate SQS notification, then replay from a lost ack.
            time.sleep(2)
            expect("iris_service_requests_1m", 3)
            expect("iris_service_public_network_bytes_1m", 369, direction="receive")
            print("PASS real ruler: namespace separation, errors/zero/missing, avg/p50/p95 and replay bytes", flush=True)

            # A batch has one too-old entry and one accepted entry. Per-record replay must not double count.
            old_stamp = datetime.fromtimestamp(time.time()-3*3600, timezone.utc).isoformat()
            worker.s3.contents = fixtures.line(stamp=old_stamp)+fixtures.line(stamp=stamp)
            key = fixtures.KEY.replace("a.log.gz", "partial.log.gz")
            worker.ingest_object(key)
            expect("iris_service_requests_1m", 4)
            expect("iris_service_public_network_bytes_1m", 492, direction="receive")
            assert sqs.dlq and "too_old" in sqs.dlq[-1]["reasons"]
            print("PASS partial acceptance: old record quarantined, accepted record counted once", flush=True)

            class LostAck:
                first = True

                def push(self, rows):
                    loki.push(rows)
                    if self.first:
                        self.first = False
                        raise c.Retry("lost_ack")

            worker.loki = LostAck()
            worker.s3.contents = fixtures.line(stamp=stamp)
            key = fixtures.KEY.replace("a.log.gz", "lost-ack.log.gz")
            try:
                worker.ingest_object(key)
                raise AssertionError("Expected simulated lost ack")
            except c.Retry:
                pass
            state.db.close()
            state = c.State(str(directory / "checkpoint.sqlite"))
            worker.state = state
            worker.ingest_object(key)
            expect("iris_service_requests_1m", 5)
            expect("iris_service_public_network_bytes_1m", 615, direction="receive")
            http(loki_url+"/flush", {})
            loki_process.terminate()
            loki_process.wait(timeout=40)
            restarted_at = time.time()
            loki_process = start_loki()
            loki.push(rows)
            time.sleep(2)
            until(lambda: any(stamp > restarted_at and value == 5 for stamp, value in samples("iris_service_requests_1m")), "fresh ruler sample after restart")
            expect("iris_service_requests_1m", 5)
            expect("iris_service_public_network_bytes_1m", 615, direction="receive")
            print("PASS flush/restart/lost-ack: actual ruler count and bytes stay unchanged", flush=True)

            # ConfigMap-equivalent rule source update must be picked up by polling.
            rules["groups"][0]["rules"].append({"record": "iris_alb_test_rule_reload", "expr": rules["groups"][0]["rules"][0]["expr"]})
            rule_file.write_text(json.dumps(rules))
            expect("iris_alb_test_rule_reload", 5)
            print("PASS local rule source reload and persistent ruler WAL; deployment still untested", flush=True)

            # The previous ALB remains trusted after a failed refresh and a successful replacement.
            before = state.snapshot_for(worker.scope)
            new_lb, new_tg = "app/iris-service-external/456", replacement_tg+"new"
            worker.elb = fixtures.FakeELB(new_lb, {new_tg: "svc-replace"})
            worker.elb.fail = "tags"
            try:
                worker.refresh()
                raise AssertionError("Expected failed tag discovery")
            except c.Retry:
                pass
            assert state.snapshot_for(worker.scope) == before and config["load_balancer"] == fixtures.LB
            replacement_stamp = datetime.fromtimestamp(time.time()-901, timezone.utc).isoformat()
            worker.loki = loki
            worker.s3.contents = fixtures.line(target=replacement_tg, stamp=replacement_stamp)
            worker.ingest_object(fixtures.KEY.replace("a.log.gz", "replace-before.log.gz"))
            expect("iris_service_requests_1m", 1, "svc-replace")
            worker.elb.fail = None
            worker.refresh()
            state.db.close()
            state = c.State(config["state"])
            worker = c.Collector(fixtures.FakeS3(fixtures.line(target=replacement_tg, stamp=replacement_stamp)+fixtures.line(target=new_tg, stamp=replacement_stamp, load_balancer=new_lb)), sqs, fixtures.FakeELB(new_lb, {new_tg: "svc-replace"}), state, loki, config)
            worker.ingest_object(fixtures.KEY.replace("a.log.gz", "replace-after.log.gz"))
            expect("iris_service_requests_1m", 3, "svc-replace")
            expect("iris_service_public_network_bytes_1m", 369, "svc-replace", direction="receive")
            expect("iris_service_public_network_bytes_1m", 1368, "svc-replace", direction="transmit")
            state.db.close()
            print("PASS ALB replacement/failed refresh/restarted history: real ruler request and byte totals", flush=True)
        except Exception:
            for log in logs:
                log.flush()
            # Only isolated fixtures appear in these logs; no dev config/credentials are used.
            for path in directory.glob("*.log"):
                print(path.name+":\n"+path.read_text()[-5000:])
            raise
        finally:
            for process in reversed(children):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for log in logs:
                log.close()


if __name__ == "__main__":
    main()
