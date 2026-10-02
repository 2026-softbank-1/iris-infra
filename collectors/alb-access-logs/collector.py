"""SQS/S3 ALB access logs -> normalized Loki logs. No AWS resource administration.

Delivery is at-least-once: deterministic timestamp/body + a durable outbox make
replays stable; Loki/ruler deduplication must pass the integration gate before use.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
import math
import os
import re
import shlex
import signal
import sqlite3
import threading
import time
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_plus
from urllib.request import Request, urlopen


LOG = logging.getLogger("alb-access-logs")
NAMESPACE = re.compile(r"^svc-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
REASONS = {"parse", "mapping", "no_target", "late", "too_old", "loki_permanent", "source", "oversize", "expired"}
PAYLOAD_RETENTION = 7 * 86400
CHECKPOINT_RETENTION = 14 * 86400


class Retry(Exception):
    """A recoverable failure; keep the SQS message visible after its timeout."""


class Invalid(Exception):
    pass


def timestamp_ns(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    delta = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def parse_line(line, bucket, key, index, load_balancer):
    if len(line) > 65536:
        raise Invalid("oversize")
    try:
        fields = shlex.split(line)
        if len(fields) < 17 or fields[0] not in {"http", "https", "h2", "grpcs", "ws", "wss"}:
            raise ValueError()
        if callable(load_balancer):
            load_balancer(fields[2])
        elif fields[2] != load_balancer:
            raise ValueError()
        stamp = timestamp_ns(fields[1])  # Completion time; WebSockets are counted at close.
        received, sent = int(fields[10]), int(fields[11])
        status = int(fields[8])
        latency = float(fields[6])
        if received < 0 or sent < 0 or not 100 <= status <= 599 or not math.isfinite(latency):
            raise ValueError()
        target = fields[16]
        result = {
            "record_id": hashlib.sha256(f"{bucket}\0{key}\0{index}".encode()).hexdigest(),
            "elb_status_code": status,
            "target_status_code": int(fields[9]) if fields[9].isdigit() else None,
            "received_bytes": received, "sent_bytes": sent,
            "target_processing_time": latency if latency >= 0 else None,
            "target_group_arn": target,
        }
        # Do not copy request URL, query, user-agent or client IP into Loki.
        return stamp, target, result
    except (ValueError, IndexError) as error:
        raise Invalid("parse") from error


def namespace_from_tags(tags, cluster, group):
    if tags.get("elbv2.k8s.aws/cluster") != cluster or tags.get("ingress.k8s.aws/stack") != group:
        return None
    # LBC resource ID is namespace/ingress-service:port, not the truncated TG name.
    resource = tags.get("ingress.k8s.aws/resource", "")
    namespace, separator, resource_id = resource.partition("/")
    if separator and resource_id and ":" in resource_id and NAMESPACE.fullmatch(namespace):
        return namespace
    return None


def mapping_scope(config):
    return json.dumps([config[key] for key in ("account", "region", "cluster", "group")], separators=(",", ":"))


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, started REAL, done INTEGER DEFAULT 0, staged INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS mappings (arn TEXT PRIMARY KEY, namespace TEXT, seen REAL);
          CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, object_key TEXT,
            timestamp TEXT, namespace TEXT, body TEXT, status TEXT DEFAULT 'pending');
          CREATE INDEX IF NOT EXISTS record_object ON records(object_key, status);
          CREATE TABLE IF NOT EXISTS problems (object_key TEXT, reason TEXT, count INTEGER,
            PRIMARY KEY(object_key, reason));
          CREATE TABLE IF NOT EXISTS totals (reason TEXT PRIMARY KEY, count INTEGER);
          CREATE TABLE IF NOT EXISTS load_balancers (identifier TEXT PRIMARY KEY, seen REAL);
          CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1),
            scope TEXT, identifier TEXT, refreshed REAL);
        """)
        # Additive upgrade: preserve existing outbox IDs, bodies and checkpoints.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(objects)")}
        for name, definition in (("terminal_at", "REAL"), ("terminal_reason", "TEXT"),
                                 ("payload_pruned", "INTEGER DEFAULT 0")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE objects ADD COLUMN {name} {definition}")
        self.db.execute("UPDATE objects SET terminal_at=started,terminal_reason='complete' WHERE done=1 AND terminal_at IS NULL")
        self.db.execute("CREATE INDEX IF NOT EXISTS object_cleanup ON objects(payload_pruned,started)")
        self.db.execute("CREATE INDEX IF NOT EXISTS object_terminal ON objects(done,terminal_at)")
        self.db.commit()

    def mapping(self, arn):
        row = self.db.execute("SELECT namespace FROM mappings WHERE arn=? AND seen>?", (arn, time.time() - PAYLOAD_RETENTION)).fetchone()
        return row[0] if row else None

    def save_mapping(self, arn, namespace):
        self.db.execute("INSERT OR REPLACE INTO mappings VALUES (?,?,?)", (arn, namespace, time.time()))
        self.db.commit()

    def start(self, key):
        self.db.execute("INSERT OR IGNORE INTO objects(key,started) VALUES (?,?)", (key, time.time()))
        self.db.commit()
        return self.db.execute("SELECT started,done,staged FROM objects WHERE key=?", (key,)).fetchone()

    def finish(self, key, reason="complete"):
        self.db.execute("UPDATE objects SET done=1,terminal_at=COALESCE(terminal_at,?),terminal_reason=? WHERE key=?", (time.time(), reason, key))
        self.db.commit()

    def snapshot_for(self, scope):
        row = self.db.execute("SELECT scope,identifier,refreshed FROM snapshot WHERE id=1").fetchone()
        if row and row[0] != scope:
            raise ValueError("Checkpoint belongs to a different AWS/cluster/group scope")
        return row[1:] if row else None

    def save_snapshot(self, scope, identifier, mappings):
        self.snapshot_for(scope)
        now = time.time()
        try:
            self.db.execute("INSERT OR REPLACE INTO load_balancers VALUES (?,?)", (identifier, now))
            self.db.executemany("INSERT OR REPLACE INTO mappings VALUES (?,?,?)", [(arn, namespace, now) for arn, namespace in mappings])
            self.db.execute("INSERT OR REPLACE INTO snapshot VALUES (1,?,?,?)", (scope, identifier, now))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return now

    def known_load_balancer(self, identifier):
        return self.db.execute("SELECT 1 FROM load_balancers WHERE identifier=? AND seen>?", (identifier, time.time()-PAYLOAD_RETENTION)).fetchone() is not None

    def expire_payload(self, key, now=None):
        now = time.time() if now is None else now
        row = self.db.execute("SELECT started,done,payload_pruned FROM objects WHERE key=?", (key,)).fetchone()
        if not row or row[0] > now-PAYLOAD_RETENTION or row[2]:
            return
        try:
            if not row[1]:
                pending = self.db.execute("SELECT count(*) FROM records WHERE object_key=? AND status='pending'", (key,)).fetchone()[0]
                if pending:
                    self.problem(key, "expired", pending)
                self.db.execute("UPDATE objects SET done=1,terminal_at=?,terminal_reason='expired' WHERE key=?", (now, key))
            self.db.execute("DELETE FROM records WHERE object_key=?", (key,))
            self.db.execute("DELETE FROM problems WHERE object_key=?", (key,))
            self.db.execute("UPDATE objects SET payload_pruned=1 WHERE key=?", (key,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def problem(self, key, reason, count=1):
        assert reason in REASONS
        self.db.execute("INSERT INTO problems VALUES (?,?,?) ON CONFLICT(object_key,reason) DO UPDATE SET count=count+excluded.count", (key, reason, count))
        self.db.execute("INSERT INTO totals VALUES (?,?) ON CONFLICT(reason) DO UPDATE SET count=count+excluded.count", (reason, count))

    def prune(self):
        now = time.time()
        # Bounded maintenance; retry failures must not prevent payload expiration.
        keys = [r[0] for r in self.db.execute("SELECT key FROM objects WHERE payload_pruned=0 AND started<=? ORDER BY started LIMIT 100", (now-PAYLOAD_RETENTION,))]
        for key in keys:
            self.expire_payload(key, now)
        self.db.execute("DELETE FROM objects WHERE key IN (SELECT key FROM objects WHERE done=1 AND payload_pruned=1 AND terminal_at<=? LIMIT 100)", (now-CHECKPOINT_RETENTION,))
        self.db.execute("DELETE FROM mappings WHERE seen<=?", (now-PAYLOAD_RETENTION,))
        self.db.execute("DELETE FROM load_balancers WHERE seen<=?", (now-PAYLOAD_RETENTION,))
        self.db.commit()
        # Free pages are reused by SQLite; avoid a blocking full VACUUM.
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")


class Loki:
    def __init__(self, url, cluster):
        self.url, self.cluster = url, cluster

    def push(self, rows):
        streams = {}
        for record_id, stamp, namespace, body in rows:
            streams.setdefault(namespace, []).append([stamp, body])
        payload = {"streams": [{"stream": {"job": "iris-alb-access", "cluster": self.cluster,
                    "k8s_namespace_name": namespace}, "values": sorted(values, key=lambda v: int(v[0]))}
                   for namespace, values in streams.items()]}
        request = Request(self.url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                if not 200 <= response.status < 300:
                    raise Retry("loki")
        except HTTPError as error:
            # Error text can contain the log body. Classify without printing it.
            with closing(error):
                message = error.read(4096).decode(errors="replace")
            if error.code == 429 or error.code >= 500:
                raise Retry("loki") from None
            if error.code == 400:
                reason = "too_old" if any(s in message for s in ("too far behind", "too old", "too_far_behind", "too_old")) else "loki_permanent"
                raise Invalid(reason) from None
            # Authentication, URL/config errors must not discard valid logs.
            raise Retry("loki_configuration") from None
        except (URLError, TimeoutError, OSError):
            raise Retry("loki") from None


class Collector:
    def __init__(self, s3, sqs, elb, state, loki, config):
        self.s3, self.sqs, self.elb, self.state, self.loki, self.config = s3, sqs, elb, state, loki, config
        self.snapshot_at = 0
        self.last_poll = 0
        self.current_receipt = None
        self.visibility_at = 0
        self.stats = Counter()
        self.cleanup_at = None
        self.scope = mapping_scope(config)
        previous = self.state.snapshot_for(self.scope)
        if previous:
            self.config["load_balancer"] = previous[0]

    def refresh(self):
        try:
            # Restrict discovery to the named shared ALB, then validate each TG's owner tags.
            lbs = self.elb.describe_load_balancers(Names=[self.config["group"]])["LoadBalancers"]
            prefix = f'arn:aws:elasticloadbalancing:{self.config["region"]}:{self.config["account"]}:loadbalancer/app/{self.config["group"]}/'
            if len(lbs) != 1 or not lbs[0]["LoadBalancerArn"].startswith(prefix) or not re.fullmatch(r"[a-zA-Z0-9]+", lbs[0]["LoadBalancerArn"][len(prefix):]):
                raise Retry("load_balancer")
            candidate = lbs[0]["LoadBalancerArn"].split("loadbalancer/", 1)[1]
            paginator = self.elb.get_paginator("describe_target_groups")
            arns = [tg["TargetGroupArn"] for page in paginator.paginate(LoadBalancerArn=lbs[0]["LoadBalancerArn"]) for tg in page["TargetGroups"]]
            updates = []
            tg_prefix = f'arn:aws:elasticloadbalancing:{self.config["region"]}:{self.config["account"]}:targetgroup/'
            for start in range(0, len(arns), 20):
                requested = set(arns[start:start+20])
                if any(not arn.startswith(tg_prefix) for arn in requested):
                    raise Retry("mapping_refresh")
                descriptions = self.elb.describe_tags(ResourceArns=list(requested))["TagDescriptions"]
                if {row["ResourceArn"] for row in descriptions} != requested:
                    raise Retry("mapping_refresh")
                for description in descriptions:
                    tags = {tag["Key"]: tag["Value"] for tag in description["Tags"]}
                    namespace = namespace_from_tags(tags, self.config["cluster"], self.config["group"])
                    if namespace:
                        updates.append((description["ResourceArn"], namespace))
            refreshed = self.state.save_snapshot(self.scope, candidate, updates)
            self.config["load_balancer"] = candidate
            self.snapshot_at = refreshed
        except Retry:
            raise
        except Exception:
            raise Retry("mapping_refresh") from None

    def extend_visibility(self):
        if self.current_receipt and time.monotonic() - self.visibility_at >= 30:
            self.sqs.change_message_visibility(QueueUrl=self.config["queue"], ReceiptHandle=self.current_receipt, VisibilityTimeout=120)
            self.visibility_at = time.monotonic()

    def ingest_object(self, key):
        self.state.expire_payload(key)
        started, done, staged = self.state.start(key)
        if done:
            return
        if not key.startswith(self.config["prefix"]) or not key.endswith(".log.gz"):
            raise Invalid("source")
        if not staged:
            self.stage_object(key, started)
        while True:
            rows = self.state.db.execute("SELECT id,timestamp,namespace,body FROM records WHERE object_key=? AND status='pending' ORDER BY CAST(timestamp AS INTEGER),id LIMIT 100", (key,)).fetchall()
            if not rows:
                break
            self.extend_visibility()
            try:
                self.loki.push(rows)
                for row in rows:
                    self.state.db.execute("UPDATE records SET status='sent' WHERE id=?", (row[0],))
                self.state.db.commit()
            except Invalid:
                # A rejected batch may have accepted some entries. Replay each identical entry.
                for row in rows:
                    self.extend_visibility()
                    try:
                        self.loki.push([row])
                        self.state.db.execute("UPDATE records SET status='sent' WHERE id=?", (row[0],))
                    except Invalid as error:
                        reason = str(error)
                        self.state.problem(key, reason)
                        self.state.db.execute("UPDATE records SET status=? WHERE id=?", (reason, row[0]))
                    self.state.db.commit()
        problems = dict(self.state.db.execute("SELECT reason,count FROM problems WHERE object_key=? AND reason!='late'", (key,)))
        if problems:
            self.quarantine(key, problems)
        self.state.finish(key)

    def validate_load_balancer(self, identifier, started):
        prefix = f'app/{self.config["group"]}/'
        if not identifier.startswith(prefix) or not re.fullmatch(r"[a-zA-Z0-9]+", identifier[len(prefix):]):
            raise Invalid("source")
        if not self.state.known_load_balancer(identifier):
            if time.time()-started < 300:
                raise Retry("mapping")
            raise Invalid("mapping")

    def stage_object(self, key, started):
        # Parsing is transactional: failed lookup before deadline leaves no partial staging.
        try:
            result = self.s3.get_object(Bucket=self.config["bucket"], Key=key)
            with closing(result["Body"]) as source:
                compressed = source.read(self.config["max_bytes"] + 1)
            if len(compressed) > self.config["max_bytes"]:
                raise Invalid("oversize")
            total = 0
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
                index = 0
                while True:
                    raw = source.readline(65538)
                    if not raw:
                        break
                    total += len(raw)
                    if total > self.config["max_bytes"] or len(raw) > 65536:
                        raise Invalid("oversize")
                    index += 1
                    self.extend_visibility()
                    try:
                        stamp, arn, body = parse_line(raw.decode("utf-8"), self.config["bucket"], key, index, lambda identifier: self.validate_load_balancer(identifier, started))
                    except Invalid as error:
                        self.state.problem(key, str(error))
                        continue
                    except UnicodeError:
                        self.state.problem(key, "parse")
                        continue
                    namespace = self.state.mapping(arn)
                    if not namespace:
                        if arn == "-":
                            self.state.problem(key, "no_target")
                            continue
                        if time.time() - started < 300:
                            raise Retry("mapping")
                        self.state.problem(key, "mapping")
                        continue
                    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    inserted = self.state.db.execute("INSERT OR IGNORE INTO records(id,object_key,timestamp,namespace,body) VALUES (?,?,?,?,?)", (body["record_id"], key, str(stamp), namespace, serialized)).rowcount
                    if inserted and time.time() - stamp/1e9 > 15*60:
                        self.state.problem(key, "late")
            self.state.db.execute("UPDATE objects SET staged=1 WHERE key=?", (key,))
            self.state.db.commit()
        except (Retry, Invalid):
            self.state.db.rollback()
            raise
        except (gzip.BadGzipFile, EOFError, UnicodeError, zlib.error):
            self.state.db.rollback()
            raise Invalid("parse") from None
        except Exception:
            self.state.db.rollback()
            raise Retry("source") from None

    def quarantine(self, key, problems):
        # No raw request content goes into diagnostics or stdout. S3 remains the recovery source.
        self.sqs.send_message(QueueUrl=self.config["dlq"], MessageBody=json.dumps({"bucket": self.config["bucket"], "key": key, "reasons": problems}, sort_keys=True))

    def process_message(self, message):
        self.current_receipt = message["ReceiptHandle"]
        self.visibility_at = 0
        try:
            event = json.loads(message["Body"])
            if not isinstance(event, dict):
                raise Invalid("source")
            if event.get("Event") == "s3:TestEvent":
                if event.get("Bucket") != self.config["bucket"]:
                    raise Invalid("source")
            else:
                records = event.get("Records")
                if not isinstance(records, list) or not records:
                    raise Invalid("source")
                for record in records:
                    if not isinstance(record, dict):
                        raise Invalid("source")
                    if record.get("eventSource") != "aws:s3" or not record.get("eventName", "").startswith("ObjectCreated:") or record.get("awsRegion") != self.config["region"]:
                        raise Invalid("source")
                    if record["s3"]["bucket"]["name"] != self.config["bucket"]:
                        raise Invalid("source")
                    key = unquote_plus(record["s3"]["object"]["key"])
                    try:
                        self.ingest_object(key)
                    except Invalid as error:
                        self.quarantine(key, {str(error): 1})
                        self.state.problem(key, str(error))
                        self.state.finish(key, str(error))
            self.sqs.delete_message(QueueUrl=self.config["queue"], ReceiptHandle=message["ReceiptHandle"])
        except (ValueError, KeyError, TypeError, Invalid):
            self.quarantine("invalid-notification", {"source": 1})
            self.sqs.delete_message(QueueUrl=self.config["queue"], ReceiptHandle=message["ReceiptHandle"])
        finally:
            self.current_receipt = None

    def loop(self, stop):
        while not stop.is_set():
            try:
                if self.cleanup_at is None or time.monotonic()-self.cleanup_at >= 60:
                    self.state.prune()
                    self.cleanup_at = time.monotonic()
                if time.time() - self.snapshot_at >= 60:
                    try:
                        self.refresh()
                    except Retry:
                        if not self.snapshot_at:
                            raise
                        self.stats["refresh_failures"] += 1
                response = self.sqs.receive_message(QueueUrl=self.config["queue"], WaitTimeSeconds=20, MaxNumberOfMessages=1)
                self.last_poll = time.time()
                for message in response.get("Messages", []):
                    self.process_message(message)
            except Exception:
                self.stats["retries"] += 1
                LOG.warning("Processing deferred; inspect health, queue age and diagnostic counters")
                stop.wait(5)


def serve_status(collector, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            fresh = time.time() - collector.snapshot_at < 300 and time.time() - collector.last_poll < 120
            if self.path in {"/healthz", "/readyz"}:
                self.send_response(200 if self.path == "/healthz" or fresh else 503)
                self.end_headers()
                return
            if self.path != "/metrics":
                self.send_response(404)
                self.end_headers()
                return
            # Separate DB connection because this endpoint is served from another thread.
            with closing(sqlite3.connect(collector.config["state"])) as db:
                totals = list(db.execute("SELECT reason,count FROM totals"))
            lines = ["# TYPE iris_alb_log_collector_ready gauge", f"iris_alb_log_collector_ready {int(fresh)}",
                     "# TYPE iris_alb_log_collector_mapping_timestamp_seconds gauge", f"iris_alb_log_collector_mapping_timestamp_seconds {collector.snapshot_at}",
                     "# TYPE iris_alb_log_collector_records_total counter"]
            lines += [f'iris_alb_log_collector_records_total{{reason="{reason}"}} {count}' for reason, count in totals]
            lines += ["# TYPE iris_alb_log_collector_operations_total counter"]
            lines += [f'iris_alb_log_collector_operations_total{{reason="{reason}"}} {count}' for reason, count in list(collector.stats.items())]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(("\n".join(lines)+"\n").encode())
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    import boto3
    from botocore.config import Config
    config = {key: os.environ[name] for key, name in {
        "bucket": "S3_BUCKET", "prefix": "S3_PREFIX", "queue": "SQS_QUEUE_URL", "dlq": "SQS_DLQ_URL",
        "cluster": "WORKLOAD_CLUSTER", "group": "ALB_GROUP", "account": "AWS_ACCOUNT_ID",
        "region": "AWS_REGION", "state": "STATE_PATH"}.items()}
    config["max_bytes"] = 64*1024*1024
    session = boto3.Session(region_name=config["region"])
    sdk = Config(connect_timeout=5, read_timeout=30, retries={"mode": "standard", "max_attempts": 3})
    state = State(config["state"])
    collector = Collector(*(session.client(name, config=sdk) for name in ("s3", "sqs", "elbv2")), state,
                          Loki(os.environ["LOKI_PUSH_URL"], config["cluster"]), config)
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_args: stop.set())
    server = serve_status(collector, 8080)
    try:
        collector.loop(stop)
    finally:
        server.shutdown()
        state.db.close()


if __name__ == "__main__":
    main()
