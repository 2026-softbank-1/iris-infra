#!/usr/bin/env python3
"""Render digest gates against isolated repository fixtures, never deployment inputs."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("alb_checker", ROOT / "scripts/check-alb-traffic.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class DigestGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory(prefix="iris-alb-chart-test-")
        cls.loki_chart = os.environ.get("IRIS_ALB_LOKI_CHART")
        if not cls.loki_chart:
            CHECKER.command("pull", "loki", "--repo", "https://grafana.github.io/helm-charts",
                            "--version", "7.3.0", "--untar", "--untardir", cls.cache.name)
            cls.loki_chart = str(Path(cls.cache.name) / "loki")

    @classmethod
    def tearDownClass(cls):
        cls.cache.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="iris-alb-digest-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in ("helm/charts/iris-alb-log-collector", "helm/charts/cluster-baseline", "helm/gitops"):
            shutil.copytree(ROOT / directory, self.root / directory)
        shutil.copytree(ROOT / "clusters", self.root / "clusters")
        shutil.copyfile(ROOT / "helm/versions.json", self.root / "helm/versions.json")
        self.path = self.root / "clusters/aws-dev-management/values/alb-log-collector.yaml"
        self.values = yaml.safe_load(self.path.read_text())
        self.values["enabled"] = False
        self.values["image"]["digest"] = ""
        self.lock = {"images": {}}

    def check(self, deployment=False):
        self.path.write_text(yaml.safe_dump(self.values))
        (self.root / "helm/images.lock.json").write_text(json.dumps(self.lock))
        args = ["check-alb-traffic.py", "--loki-chart", self.loki_chart]
        if deployment:
            args.append("--deployment")
        with patch.object(CHECKER, "ROOT", self.root), patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()):
            CHECKER.main()

    def pin(self):
        # Test-only digest: it is not proof of an ECR publication.
        digest = "sha256:" + "b" * 64
        self.values["image"]["digest"] = digest
        self.lock["images"][self.values["image"]["repository"]] = digest

    def test_staged_empty_digest_passes(self):
        self.check()

    def test_pinned_digest_passes_staged_and_deployment_checks(self):
        self.pin()
        self.check()
        self.check(deployment=True)
        self.values["enabled"] = True
        self.check(deployment=True)

    def test_deployment_missing_or_mismatched_digest_fails(self):
        with self.assertRaisesRegex(AssertionError, "Publish and lock"):
            self.check(deployment=True)
        self.pin()
        self.lock["images"][self.values["image"]["repository"]] = "sha256:" + "c" * 64
        with self.assertRaisesRegex(AssertionError, "Publish and lock"):
            self.check(deployment=True)


if __name__ == "__main__":
    unittest.main()
