import gzip
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("collector", Path(__file__).with_name("collector.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

LB = "app/iris-service-external/123"
TG = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/k8s-svc/123"
KEY = "alb/workload/AWSLogs/123456789012/elasticloadbalancing/ap-northeast-2/2026/10/03/a.log.gz"


def line(status=200, latency="0.125", target=TG, stamp="2026-10-03T00:00:00.123456Z", tail="", load_balancer=LB):
    return f'https {stamp} {load_balancer} 1.2.3.4:123 10.0.0.1:80 0.001 {latency} 0.001 {status} {status} 123 456 "GET https://secret.example/path?token=PRIVATE HTTP/1.1" "agent with spaces" cipher tls {target} "trace" "domain" "cert" 1 time "forward" "-" "-" "10.0.0.1:80" "200" "-" "-"{tail}\n'


class FakeELB:
    def __init__(self, identifier=LB, mappings=None):
        self.identifier = identifier
        self.mappings = {TG: "svc-1"} if mappings is None else mappings
        self.fail = None

    def describe_load_balancers(self, **_kwargs):
        if self.fail == "lb":
            raise RuntimeError("ELB unavailable")
        return {"LoadBalancers": [{"LoadBalancerArn": "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/"+self.identifier}]}

    def get_paginator(self, _name):
        return self

    def paginate(self, **_kwargs):
        if self.fail == "pages":
            raise RuntimeError("TargetGroup API unavailable")
        return [{"TargetGroups": [{"TargetGroupArn": arn} for arn in self.mappings]}]

    def describe_tags(self, ResourceArns):
        if self.fail == "tags":
            raise RuntimeError("Tags unavailable")
        rows = []
        for arn in ResourceArns:
            tags = {"elbv2.k8s.aws/cluster": "iris-dev-workload", "ingress.k8s.aws/stack": "iris-service-external", "ingress.k8s.aws/resource": self.mappings[arn]+"/app-app:http"}
            rows.append({"ResourceArn": arn, "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]})
        return {"TagDescriptions": rows}


class FakeS3:
    def __init__(self, contents):
        self.contents = contents

    def get_object(self, **_kwargs):
        return {"Body": io.BytesIO(gzip.compress(self.contents.encode()))}


class FakeSQS:
    def __init__(self):
        self.deleted, self.dlq, self.visibility = [], [], []

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    def send_message(self, **kwargs):
        self.dlq.append(json.loads(kwargs["MessageBody"]))

    def change_message_visibility(self, **kwargs):
        self.visibility.append(kwargs)


class FakeLoki:
    def __init__(self):
        self.accepted = {}
        self.calls = []
        self.fail_once = False
        self.reject_id = None

    def push(self, rows):
        self.calls.append(rows)
        for row in rows:
            if row[0] != self.reject_id:
                self.accepted[row[0]] = row
        if self.fail_once:
            self.fail_once = False
            raise c.Retry("lost_ack")
        if any(row[0] == self.reject_id for row in rows):
            raise c.Invalid("too_old")


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = {"bucket": "bucket", "prefix": "alb/workload/AWSLogs/123456789012/elasticloadbalancing/ap-northeast-2/", "queue": "queue", "dlq": "dlq", "group": "iris-service-external", "cluster": "iris-dev-workload", "account": "123456789012", "region": "ap-northeast-2", "state": self.tmp.name+"/state.db", "max_bytes": 1024*1024, "load_balancer": LB}
        self.state = c.State(self.config["state"])
        self.state.save_snapshot(c.mapping_scope(self.config), LB, [(TG, "svc-1")])
        self.sqs, self.loki = FakeSQS(), FakeLoki()
        self.worker = c.Collector(FakeS3(line()), self.sqs, FakeELB(), self.state, self.loki, self.config)

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def message(self, key=KEY):
        return {"ReceiptHandle": "receipt", "Body": json.dumps({"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "awsRegion": "ap-northeast-2", "s3": {"bucket": {"name": "bucket"}, "object": {"key": key}}}]})}

    def test_parser_privacy_and_unknown_tail(self):
        stamp, arn, record = c.parse_line(line(503, tail=' "future column"'), "bucket", KEY, 1, LB)
        self.assertEqual(stamp, 1790985600123456000)
        self.assertEqual(arn, TG)
        self.assertEqual(record["received_bytes"], 123)
        self.assertNotIn("PRIVATE", json.dumps(record))
        self.assertNotIn("1.2.3.4", json.dumps(record))
        self.assertEqual(record["elb_status_code"], 503)

    def test_invalid_latency_not_in_quantiles(self):
        self.assertIsNone(c.parse_line(line(latency="-1"), "bucket", KEY, 1, LB)[2]["target_processing_time"])
        with self.assertRaises(c.Invalid):
            c.parse_line(line(latency="nan"), "bucket", KEY, 1, LB)

    def test_distinct_requests_at_same_timestamp(self):
        self.worker.s3.contents = line()+line()
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.accepted), 2)
        self.assertEqual(len(self.sqs.deleted), 1)

    def test_duplicate_notification_and_restart(self):
        self.worker.process_message(self.message())
        self.state.db.close()
        self.state = c.State(self.config["state"])
        self.worker.state = self.state
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.calls), 1)
        self.assertEqual(len(self.sqs.deleted), 2)

    def test_crash_after_ack_replay_identical(self):
        self.loki.fail_once = True
        with self.assertRaises(c.Retry):
            self.worker.process_message(self.message())
        self.assertFalse(self.sqs.deleted)
        self.worker.process_message(self.message())
        self.assertEqual(self.loki.calls[0], self.loki.calls[1])
        self.assertEqual(len(self.loki.accepted), 1)

    def test_staged_object_not_refetched_or_diagnostics_recounted(self):
        self.worker.s3.contents = "invalid\n"+line()
        self.loki.fail_once = True
        with self.assertRaises(c.Retry):
            self.worker.process_message(self.message())
        self.worker.s3.get_object = lambda **kwargs: self.fail("outbox must resume without S3")
        self.worker.process_message(self.message())
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='parse'").fetchone()[0], 1)
        self.assertEqual(len(self.loki.accepted), 1)

    def test_loki_http_classification(self):
        loki = c.Loki("http://localhost/loki/api/v1/push", "cluster")
        row = ("id", "123", "svc-1", "{}")
        for status in (429, 500, 503, 401, 404):
            error = HTTPError(loki.url, status, "error", {}, io.BytesIO(b"private body"))
            with patch.object(c, "urlopen", side_effect=error), self.assertRaises(c.Retry):
                loki.push([row])
        error = HTTPError(loki.url, 400, "error", {}, io.BytesIO(b"entry too far behind"))
        with patch.object(c, "urlopen", side_effect=error), self.assertRaisesRegex(c.Invalid, "too_old"):
            loki.push([row])

    def test_failed_mapping_refresh_keeps_last_complete_snapshot(self):
        class Paginator:
            def paginate(self, **kwargs):
                return [{"TargetGroups": [{"TargetGroupArn": TG}]}]

        class ELB:
            fail = False

            def describe_load_balancers(self, **kwargs):
                return {"LoadBalancers": [{"LoadBalancerArn": "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/"+LB}]}

            def get_paginator(self, name):
                return Paginator()

            def describe_tags(self, **kwargs):
                if self.fail:
                    raise RuntimeError("API unavailable")
                tags = {"elbv2.k8s.aws/cluster": "iris-dev-workload", "ingress.k8s.aws/stack": "iris-service-external", "ingress.k8s.aws/resource": "svc-2/app-app:http"}
                return {"TagDescriptions": [{"ResourceArn": TG, "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}]}

        self.worker.elb = ELB()
        self.worker.refresh()
        snapshot = self.worker.snapshot_at
        self.assertEqual(self.state.mapping(TG), "svc-2")
        self.worker.elb.fail = True
        with self.assertRaises(c.Retry):
            self.worker.refresh()
        self.assertEqual(self.worker.snapshot_at, snapshot)
        self.assertEqual(self.state.mapping(TG), "svc-2")

    def test_alb_replace_failed_refresh_keeps_old_snapshot(self):
        self.worker.refresh()
        before = self.state.snapshot_for(self.worker.scope)
        for stage in ("lb", "pages", "tags"):
            self.worker.elb = FakeELB("app/iris-service-external/456", {TG+"new": "svc-2"})
            self.worker.elb.fail = stage
            with self.assertRaises(c.Retry):
                self.worker.refresh()
            self.assertEqual(self.state.snapshot_for(self.worker.scope), before)
            self.assertEqual(self.config["load_balancer"], LB)
            self.assertEqual(self.worker.snapshot_at, before[1])
            self.assertFalse(self.state.known_load_balancer("app/iris-service-external/456"))
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.accepted), 1)
        self.assertFalse(self.sqs.dlq)

    def test_snapshot_commit_failure_rolls_back_all_candidates(self):
        self.worker.refresh()
        before = self.state.snapshot_for(self.worker.scope)
        connection = self.state.db

        class FailedCommit:
            def __getattr__(self, name):
                return getattr(connection, name)

            def commit(self):
                raise sqlite3.OperationalError("injected commit failure")

        self.worker.elb = FakeELB("app/iris-service-external/456", {TG+"new": "svc-2"})
        self.state.db = FailedCommit()
        try:
            with self.assertRaises(c.Retry):
                self.worker.refresh()
        finally:
            self.state.db = connection
        self.assertEqual(self.state.snapshot_for(self.worker.scope), before)
        self.assertEqual(self.config["load_balancer"], LB)
        self.assertIsNone(self.state.mapping(TG+"new"))
        self.assertFalse(self.state.known_load_balancer("app/iris-service-external/456"))

    def test_later_tag_batch_failure_keeps_entire_snapshot(self):
        self.worker.refresh()
        before = self.state.snapshot_for(self.worker.scope)

        class SecondBatchFailure(FakeELB):
            calls = 0

            def describe_tags(self, ResourceArns):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("second tag batch unavailable")
                return super().describe_tags(ResourceArns)

        mappings = {TG+str(index): "svc-2" for index in range(21)}
        self.worker.elb = SecondBatchFailure("app/iris-service-external/456", mappings)
        with self.assertRaises(c.Retry):
            self.worker.refresh()
        self.assertEqual(self.state.snapshot_for(self.worker.scope), before)
        self.assertEqual(self.config["load_balancer"], LB)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM mappings").fetchone()[0], 1)
        self.assertFalse(self.state.known_load_balancer("app/iris-service-external/456"))

    def test_discovery_scope_rejection_keeps_last_snapshot(self):
        before = self.state.snapshot_for(self.worker.scope)
        arn = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/"+LB
        for invalid in (arn.replace("ap-northeast-2", "us-east-1"), arn.replace("123456789012", "999999999999"), arn.replace("iris-service-external", "unrelated")):
            with patch.object(self.worker.elb, "describe_load_balancers", return_value={"LoadBalancers": [{"LoadBalancerArn": invalid}]}), self.assertRaises(c.Retry):
                self.worker.refresh()
            self.assertEqual(self.state.snapshot_for(self.worker.scope), before)
        self.worker.elb = FakeELB("app/iris-service-external/456", {TG.replace("123456789012", "999999999999"): "svc-2"})
        with self.assertRaises(c.Retry):
            self.worker.refresh()
        self.assertEqual(self.state.snapshot_for(self.worker.scope), before)
        self.assertFalse(self.state.known_load_balancer("app/iris-service-external/456"))

    def test_successful_alb_replace_accepts_old_and_new_logs_after_restart(self):
        new_lb, new_tg = "app/iris-service-external/456", TG+"new"
        self.worker.elb = FakeELB(new_lb, {new_tg: "svc-2"})
        self.worker.refresh()
        self.state.db.close()
        self.state = c.State(self.config["state"])
        self.worker = c.Collector(FakeS3(line()+line(target=new_tg, load_balancer=new_lb)), self.sqs, FakeELB(new_lb, {new_tg: "svc-2"}), self.state, self.loki, self.config)
        self.worker.process_message(self.message())
        self.assertEqual({row[2] for row in self.loki.accepted.values()}, {"svc-1", "svc-2"})
        self.assertFalse(self.sqs.dlq)
        self.assertEqual(self.config["load_balancer"], new_lb)

    def test_unknown_alb_waits_for_snapshot_then_accepts(self):
        new_lb = "app/iris-service-external/456"
        self.worker.s3.contents = line(load_balancer=new_lb)
        with self.assertRaises(c.Retry):
            self.worker.process_message(self.message())
        self.assertFalse(self.sqs.deleted)
        self.worker.elb = FakeELB(new_lb)
        self.worker.refresh()
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.accepted), 1)

    def test_unknown_alb_mapping_deadline_quarantines_without_guessing(self):
        self.worker.s3.contents = line(load_balancer="app/iris-service-external/456")
        self.state.start(KEY)
        self.state.db.execute("UPDATE objects SET started=? WHERE key=?", (time.time()-301, KEY))
        self.state.db.commit()
        self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"mapping": 1})
        self.assertEqual(len(self.sqs.deleted), 1)

    def test_cached_alb_history_expires_and_unrelated_alb_is_rejected(self):
        base = time.time()
        with patch.object(c.time, "time", return_value=base+c.PAYLOAD_RETENTION):
            self.assertFalse(self.state.known_load_balancer(LB))
            self.state.prune()
            self.assertFalse(self.state.db.execute("SELECT * FROM load_balancers").fetchall())
        self.worker.s3.contents = line(load_balancer="app/unrelated/123")
        self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"source": 1})

    def test_scope_change_does_not_reuse_cached_history(self):
        changed = {**self.config, "group": "unrelated"}
        with self.assertRaisesRegex(ValueError, "different"):
            c.Collector(self.worker.s3, self.sqs, FakeELB(), self.state, self.loki, changed)

    def stage_pending(self, contents=None):
        self.worker.s3.contents = line() if contents is None else contents
        self.loki.fail_once = True
        base = time.time()
        with patch.object(c.time, "time", return_value=base):
            with self.assertRaises(c.Retry):
                self.worker.process_message(self.message())
        return base

    def test_pending_expiry_boundary_and_duplicate_after_restart(self):
        base = self.stage_pending()
        with patch.object(c.time, "time", return_value=base+c.PAYLOAD_RETENTION-1):
            self.state.prune()
            self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 1)
        with patch.object(c.time, "time", return_value=base+c.PAYLOAD_RETENTION):
            self.state.prune()
            self.assertEqual(self.state.db.execute("SELECT terminal_reason,payload_pruned FROM objects").fetchone(), ("expired", 1))
            self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
            self.state.db.close()
            self.state = c.State(self.config["state"])
            self.worker.state = self.state
            self.worker.s3.get_object = lambda **kwargs: self.fail("expired object must not be read")
            self.worker.loki.push = lambda rows: self.fail("expired object must not be pushed")
            self.worker.sqs.send_message = lambda **kwargs: self.fail("expiry must not depend on DLQ")
            self.worker.process_message(self.message())
            self.worker.process_message(self.message())
            self.state.prune()
        self.assertEqual(len(self.sqs.deleted), 2)
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='expired'").fetchone()[0], 1)

    def test_mixed_sent_pending_expiry_counts_only_pending(self):
        base = self.stage_pending(line()+line())
        first = self.state.db.execute("SELECT id FROM records LIMIT 1").fetchone()[0]
        self.state.db.execute("UPDATE records SET status='sent' WHERE id=?", (first,))
        self.state.db.commit()
        with patch.object(c.time, "time", return_value=base+8*86400):
            self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='expired'").fetchone()[0], 1)

    def test_expiry_commit_failure_preserves_payload_and_counter(self):
        base = self.stage_pending()
        connection = self.state.db

        class FailedCommit:
            def __getattr__(self, name):
                return getattr(connection, name)

            def commit(self):
                raise sqlite3.OperationalError("injected commit failure")

        self.state.db = FailedCommit()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.state.expire_payload(KEY, base+c.PAYLOAD_RETENTION)
        finally:
            self.state.db = connection
        self.assertEqual(self.state.db.execute("SELECT done,payload_pruned FROM objects").fetchone(), (0, 0))
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 1)
        self.assertFalse(self.state.db.execute("SELECT * FROM totals WHERE reason='expired'").fetchall())
        self.state.expire_payload(KEY, base+c.PAYLOAD_RETENTION)
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='expired'").fetchone()[0], 1)

    def test_completed_payload_pruned_but_checkpoint_retained_14_days(self):
        base = time.time()
        with patch.object(c.time, "time", return_value=base):
            self.worker.process_message(self.message())
        with patch.object(c.time, "time", return_value=base+c.PAYLOAD_RETENTION):
            self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
        self.assertEqual(self.state.db.execute("SELECT terminal_reason FROM objects").fetchone()[0], "complete")
        self.assertFalse(self.state.db.execute("SELECT * FROM totals WHERE reason='expired'").fetchall())
        with patch.object(c.time, "time", return_value=base+c.CHECKPOINT_RETENTION-1):
            self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM objects").fetchone()[0], 1)
        with patch.object(c.time, "time", return_value=base+c.CHECKPOINT_RETENTION):
            self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM objects").fetchone()[0], 0)

    def test_expired_checkpoint_retained_from_expiry_not_first_receive(self):
        base = self.stage_pending()
        expiry = base+c.PAYLOAD_RETENTION
        with patch.object(c.time, "time", return_value=expiry):
            self.state.prune()
        with patch.object(c.time, "time", return_value=expiry+c.CHECKPOINT_RETENTION-1):
            self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM objects").fetchone()[0], 1)
        with patch.object(c.time, "time", return_value=expiry+c.CHECKPOINT_RETENTION):
            self.state.prune()
        self.assertFalse(self.state.db.execute("SELECT * FROM objects").fetchall())

    def test_expiry_on_redelivery_without_periodic_prune(self):
        base = self.stage_pending()
        self.worker.s3.get_object = lambda **kwargs: self.fail("no S3 read after expiry")
        with patch.object(c.time, "time", return_value=base+8*86400):
            self.worker.process_message(self.message())
        self.assertEqual(len(self.sqs.deleted), 1)
        self.assertEqual(self.state.db.execute("SELECT terminal_reason FROM objects").fetchone()[0], "expired")

    def test_cleanup_runs_during_continuous_refresh_or_loki_failures(self):
        class Stop:
            remaining = 2

            def is_set(self):
                return self.remaining == 0

            def wait(self, _seconds):
                self.remaining -= 1

        base = self.stage_pending()
        self.worker.elb.fail = "lb"
        self.worker.sqs.receive_message = lambda **kwargs: self.fail("initial refresh must succeed before polling")
        self.worker.sqs.send_message = lambda **kwargs: self.fail("expiry must not call DLQ")
        with patch.object(c.time, "time", return_value=base+8*86400), patch.object(c.time, "monotonic", side_effect=lambda: time_value[0]):
            time_value = [0]
            stop = Stop()
            original_wait = stop.wait

            def wait(seconds):
                time_value[0] += 61
                original_wait(seconds)

            stop.wait = wait
            self.worker.loop(stop)
        self.assertFalse(self.state.db.execute("SELECT * FROM records").fetchall())
        self.assertEqual(self.worker.stats["retries"], 2)

        # A subsequent Loki outage cannot prevent other expired payloads being cleaned.
        old = KEY.replace("a.log.gz", "other.log.gz")
        self.state.start(old)
        self.state.db.execute("UPDATE objects SET started=? WHERE key=?", (base, old))
        self.state.db.execute("INSERT INTO records(id,object_key,timestamp,namespace,body) VALUES ('old',?,'123','svc-1','{}')", (old,))
        self.state.db.commit()
        self.worker.elb.fail = None
        self.worker.cleanup_at = None
        self.worker.sqs.receive_message = lambda **kwargs: {"Messages": [self.message(KEY.replace("a.log.gz", "new.log.gz"))]}
        self.worker.loki.push = lambda rows: (_ for _ in ()).throw(c.Retry("loki"))
        with patch.object(c.time, "time", return_value=base+8*86400):
            self.worker.loop(Stop())
        self.assertFalse(self.state.db.execute("SELECT * FROM records WHERE object_key=?", (old,)).fetchall())
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='expired'").fetchone()[0], 2)

    def test_cleanup_is_batched_and_reuses_free_pages(self):
        base = time.time()
        for index in range(110):
            self.state.start(str(index))
            self.state.db.execute("UPDATE objects SET started=? WHERE key=?", (base-8*86400, str(index)))
            self.state.db.execute("INSERT INTO records(id,object_key,timestamp,namespace,body) VALUES (?,?, '123','svc-1',?)", (str(index), str(index), "x"*8192))
        self.state.db.commit()
        pages = self.state.db.execute("PRAGMA page_count").fetchone()[0]
        self.state.prune()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM records").fetchone()[0], 10)
        self.state.prune()
        self.assertFalse(self.state.db.execute("SELECT * FROM records").fetchall())
        self.assertGreater(self.state.db.execute("PRAGMA freelist_count").fetchone()[0], 0)
        for index in range(110):
            self.state.db.execute("INSERT INTO records(id,object_key,timestamp,namespace,body) VALUES (?,?,'123','svc-1',?)", ("new"+str(index), "new", "x"*8192))
        self.state.db.commit()
        self.assertLessEqual(self.state.db.execute("PRAGMA page_count").fetchone()[0], pages+5)
        busy, frames, checkpointed = self.state.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        self.assertEqual(busy, 0)
        self.assertEqual(frames, checkpointed)

    def test_additive_legacy_state_upgrade_preserves_outbox_and_old_reads(self):
        path = self.tmp.name+"/legacy.db"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("CREATE TABLE objects (key TEXT PRIMARY KEY,started REAL,done INTEGER DEFAULT 0,staged INTEGER DEFAULT 0); CREATE TABLE mappings (arn TEXT PRIMARY KEY,namespace TEXT,seen REAL); CREATE TABLE records (id TEXT PRIMARY KEY,object_key TEXT,timestamp TEXT,namespace TEXT,body TEXT,status TEXT DEFAULT 'pending');")
            db.execute("INSERT INTO objects VALUES ('object',?,1,1)", (time.time(),))
            db.execute("INSERT INTO records VALUES ('id','object','123','svc-1','{}','sent')")
            db.commit()
        migrated = c.State(path)
        try:
            self.assertEqual(migrated.db.execute("SELECT * FROM records").fetchone(), ("id", "object", "123", "svc-1", "{}", "sent"))
            self.assertEqual(migrated.db.execute("SELECT started,done,staged FROM objects WHERE key='object'").fetchone()[1:], (1, 1))
            # The old v2 SQL remains readable/writable after adding columns/tables.
            migrated.db.execute("INSERT OR IGNORE INTO objects(key,started) VALUES ('legacy-write',?)", (time.time(),))
            migrated.db.execute("INSERT OR REPLACE INTO mappings VALUES (?,?,?)", (TG, "svc-1", time.time()))
            migrated.db.commit()
            self.assertEqual(migrated.mapping(TG), "svc-1")
            self.assertIsNone(migrated.snapshot_for(self.worker.scope))
        finally:
            migrated.db.close()

    def test_partial_push_acceptance(self):
        self.worker.s3.contents = line()+line()
        self.loki.reject_id = c.parse_line(line(), "bucket", KEY, 2, LB)[2]["record_id"]
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.accepted), 1)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"too_old": 1})
        self.assertEqual(len(self.sqs.deleted), 1)

    def test_new_target_retried_then_mapping_snapshot(self):
        self.state.db.execute("DELETE FROM mappings")
        self.state.db.commit()
        with self.assertRaises(c.Retry):
            self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertFalse(self.sqs.deleted)
        self.state.save_mapping(TG, "svc-2")
        self.worker.process_message(self.message())
        self.assertEqual(next(iter(self.loki.accepted.values()))[2], "svc-2")

    def test_mapping_deadline_quarantine(self):
        self.state.db.execute("DELETE FROM mappings")
        self.state.start(KEY)
        self.state.db.execute("UPDATE objects SET started=?", (time.time()-301,))
        self.state.db.commit()
        self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"mapping": 1})

    def test_no_target_not_assigned_service(self):
        self.worker.s3.contents = line(target="-")
        self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"no_target": 1})

    def test_malformed_line_does_not_discard_valid_entries(self):
        self.worker.s3.contents = "invalid\n"+line(404)
        self.worker.process_message(self.message())
        self.assertEqual(len(self.loki.accepted), 1)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"parse": 1})

    def test_wrong_source_never_read(self):
        self.worker.s3.get_object = lambda **kwargs: self.fail("must not fetch unrelated object")
        self.worker.process_message(self.message("unrelated/file.log.gz"))
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"source": 1})

    def test_invalid_notification_types_quarantined_and_acked(self):
        for body in ([], {"Records": "invalid"}, {"Records": [42]}):
            message = {"ReceiptHandle": "receipt", "Body": json.dumps(body)}
            self.worker.process_message(message)
        self.assertEqual(len(self.sqs.deleted), 3)
        self.assertEqual(len(self.sqs.dlq), 3)
        self.assertTrue(all(row["reasons"] == {"source": 1} for row in self.sqs.dlq))

    def test_bounded_gzip(self):
        self.worker.s3.contents = line()*10
        self.worker.config["max_bytes"] = 200
        self.worker.process_message(self.message())
        self.assertFalse(self.loki.accepted)
        self.assertEqual(self.sqs.dlq[0]["reasons"], {"oversize": 1})

    def test_late_arrival_keeps_original_timestamp(self):
        with patch.object(c.time, "time", return_value=1790985600.123456+901):
            self.worker.process_message(self.message())
        row = next(iter(self.loki.accepted.values()))
        self.assertEqual(row[1], "1790985600123456000")
        self.assertEqual(self.state.db.execute("SELECT count FROM totals WHERE reason='late'").fetchone()[0], 1)

    def test_tag_owner_and_namespace_boundaries(self):
        tags = {"elbv2.k8s.aws/cluster": "iris-dev-workload", "ingress.k8s.aws/stack": "iris-service-external", "ingress.k8s.aws/resource": "svc-123/app-app:http"}
        self.assertEqual(c.namespace_from_tags(tags, "iris-dev-workload", "iris-service-external"), "svc-123")
        for change in ({"elbv2.k8s.aws/cluster": "other"}, {"ingress.k8s.aws/stack": "other"}, {"ingress.k8s.aws/resource": "iris-platform/app-app:http"}):
            self.assertIsNone(c.namespace_from_tags(tags | change, "iris-dev-workload", "iris-service-external"))


if __name__ == "__main__":
    unittest.main()
