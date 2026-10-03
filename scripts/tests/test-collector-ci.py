#!/usr/bin/env python3
"""Use real isolated Git histories to test image selection and PR provenance."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CI = load("ci", ROOT / "scripts/collector-ci-changes.py")
UP = load("up", ROOT / "scripts/update-alb-collector-image.py")
REPO = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/alb-log-collector"
DIGEST = "sha256:" + "b" * 64
NEXT = "sha256:" + "c" * 64


class CollectorCITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iris-collector-ci-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("collectors/alb-access-logs/collector.py", "runtime v1\n")
        self.write("collectors/alb-access-logs/Dockerfile", "FROM scratch\n")
        self.write("collectors/alb-access-logs/requirements.txt", "locked\n")
        self.write(CI.VALUES, '# preserve settings\nenabled: false\nimage:\n'
                   f'  repository: {REPO}\n  digest: ""\nsource:\n  cluster: iris-dev-workload\n')
        self.write(CI.LOCK, json.dumps({"schema_version": 1, "images": {"other:image": NEXT},
                                       "verification_images": {"fixture": "unchanged"}}, indent=2) + "\n")
        self.base = self.commit("initial")

    def git(self, *args):
        return CI.git(self.root, *args).decode().strip()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def classify(self, base=None, event="push", force=False, **extra):
        base = base or self.base
        payload = {"before": base, "pull_request": {"base": {"sha": base}}, **extra}
        return CI.classify(self.root, event, self.git("rev-parse", "HEAD"), payload, force)

    def update(self, digest=DIGEST, source=None, run=100, attempt=1, branch=None):
        return UP.update(self.root, REPO, digest, source or self.base, run, attempt, branch)

    def bot_branch(self, run=100):
        self.git("checkout", "-qb", CI.BOT_BRANCH)
        self.assertEqual(self.update(run=run), "updated")
        self.commit("digest PR")
        self.git("checkout", "main")
        return CI.BOT_BRANCH

    def test_runtime_and_build_inputs_need_image(self):
        for path in ("collectors/alb-access-logs/collector.py",
                     "collectors/alb-access-logs/requirements.txt",
                     "collectors/alb-access-logs/Dockerfile",
                     "collectors/alb-access-logs/future.data", *sorted(CI.BUILD_INPUTS)):
            with self.subTest(path=path):
                self.assertTrue(CI.image_input(path))

    def test_tests_rules_docs_and_digest_do_not_rebuild(self):
        for path in ("collectors/alb-access-logs/test_collector.py",
                     "collectors/alb-access-logs/integration.py",
                     "helm/charts/iris-alb-log-collector/files/traffic.yaml",
                     "scripts/tests/test-collector-ci.py"):
            with self.subTest(path=path):
                self.write(path, "test change\n")
                before = self.git("rev-parse", "HEAD")
                self.commit(path)
                self.assertEqual(self.classify(before), {"image_changed": False, "test_changed": True})
        for path in ("collectors/alb-access-logs/README.md", CI.VALUES, CI.LOCK,
                     "docs/unrelated.md", "terraform/example.tf"):
            with self.subTest(path=path):
                self.write(path, "unrelated change\n")
                before = self.git("rev-parse", "HEAD")
                self.commit(path)
                self.assertEqual(self.classify(before), {"image_changed": False, "test_changed": False})

    def test_push_compares_all_commits_not_only_last(self):
        self.write("collectors/alb-access-logs/collector.py", "runtime v2\n")
        self.commit("code")
        self.write("README.md", "docs\n")
        self.commit("docs after code")
        self.assertTrue(self.classify()["image_changed"])

    def test_deleted_or_renamed_runtime_and_nul_paths(self):
        self.git("mv", "collectors/alb-access-logs/collector.py", "elsewhere.py")
        self.write("docs/line\nbreak.md", "a path must not split on newline\n")
        self.commit("rename and odd file")
        self.assertTrue(self.classify()["image_changed"])
        self.assertIn("docs/line\nbreak.md", CI.paths_between(self.root, self.base, "HEAD"))

    def test_zero_before_initial_push(self):
        self.assertTrue(self.classify("0" * 40)["image_changed"])

    def test_pull_request_including_fork_uses_base_and_merge_tree(self):
        self.write("collectors/alb-access-logs/collector.py", "fork code\n")
        self.commit("fork fixture")
        self.assertTrue(self.classify(event="pull_request")["image_changed"])
        self.assertNotIn("publish", self.classify(event="pull_request"))

    def test_manual_rebuild_is_explicit(self):
        self.assertFalse(self.classify(event="workflow_dispatch")["image_changed"])
        self.assertTrue(self.classify(event="workflow_dispatch", force=True)["image_changed"])

    def test_invalid_sha_and_event_fail_closed(self):
        with self.assertRaises(ValueError):
            self.classify("--help")
        with self.assertRaises(ValueError):
            self.classify(event="pull_request_target")

    def test_update_changes_only_digest_and_single_lock_entry(self):
        old = (self.root / CI.VALUES).read_text()
        self.assertEqual(self.update(), "updated")
        new = (self.root / CI.VALUES).read_text()
        self.assertEqual(UP.DIGEST_LINE.sub("", old), UP.DIGEST_LINE.sub("", new))
        lock = json.loads((self.root / CI.LOCK).read_text())
        self.assertEqual(lock["images"], {"other:image": NEXT, REPO: DIGEST})
        self.assertEqual(lock["verification_images"], {"fixture": "unchanged"})
        self.assertEqual(set(self.git("diff", "--name-only").splitlines()), {CI.VALUES, CI.LOCK})
        self.commit("pin")
        CI.check_provenance(self.root)

    def test_repeat_and_same_digest_do_not_create_duplicate_changes(self):
        self.update()
        self.commit("pin")
        self.assertEqual(self.update(), "older-run")
        self.assertEqual(self.update(run=101), "unchanged")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_invalid_publication_never_modifies_values(self):
        old = (self.root / CI.VALUES).read_bytes()
        for repo, digest, source, run, attempt in (
                (REPO.replace("alb-log-collector", "was"), DIGEST, self.base, 1, 1),
                (REPO, "bad-digest", self.base, 1, 1),
                (REPO, DIGEST, "invalid", 1, 1),
                (REPO, DIGEST, self.base, 0, 1),
                (REPO, DIGEST, self.base, 1, 0)):
            with self.subTest(repo=repo, digest=digest, source=source, run=run, attempt=attempt):
                with self.assertRaises(ValueError):
                    UP.update(self.root, repo, digest, source, run, attempt)
                self.assertEqual((self.root / CI.VALUES).read_bytes(), old)

    def test_repository_mismatch_fails_before_writes(self):
        self.write(CI.VALUES, (self.root / CI.VALUES).read_text().replace(REPO, "another/repository"))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.update()
        self.assertNotIn(REPO, json.loads((self.root / CI.LOCK).read_text())["images"])

    def test_stale_source_skips_without_writing(self):
        self.write("collectors/alb-access-logs/collector.py", "new source\n")
        self.commit("new code")
        self.assertEqual(self.update(), "stale-source")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_docs_only_main_change_still_allows_publication(self):
        self.write("README.md", "docs\n")
        self.commit("docs")
        self.assertEqual(self.update(), "updated")
        self.commit("digest")
        CI.check_provenance(self.root)

    def test_same_input_older_run_cannot_overwrite_newer_bot_pr(self):
        branch = self.bot_branch(run=200)
        self.assertEqual(self.update(digest=NEXT, run=100, branch=branch), "older-run")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.update(digest=NEXT, run=200, attempt=2, branch=branch), "updated")

    def test_new_runtime_can_replace_previous_image_pr(self):
        branch = self.bot_branch()
        self.write("collectors/alb-access-logs/collector.py", "new runtime\n")
        current = self.commit("new runtime")
        self.assertEqual(self.update(digest=NEXT, source=current, run=101, branch=branch), "updated")

    def test_merged_digest_cannot_be_regressed(self):
        branch = self.bot_branch(run=200)
        self.git("merge", "--ff-only", branch)
        self.assertEqual(self.update(digest=NEXT, run=100), "older-run")

    def test_updated_merge_tree_rejects_stale_green_pr(self):
        branch = self.bot_branch()
        self.write("collectors/alb-access-logs/collector.py", "new runtime\n")
        current = self.commit("code after digest PR checks")
        self.git("checkout", branch)
        self.git("merge", "--no-edit", "main")
        with self.assertRaisesRegex(ValueError, "different inputs"):
            CI.check_provenance(self.root)
        self.git("checkout", "main")
        self.assertEqual(self.update(digest=NEXT, source=current, run=101, branch=branch), "updated")

    def test_unrelated_branch_edit_is_never_overwritten(self):
        branch = self.bot_branch()
        self.git("checkout", branch)
        self.write("human.txt", "preserve me\n")
        self.commit("human edit")
        self.git("checkout", "main")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            self.update(digest=NEXT, run=101, branch=branch)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_other_values_or_lock_edits_on_bot_branch_fail(self):
        branch = self.bot_branch()
        self.git("checkout", branch)
        self.write(CI.VALUES, (self.root / CI.VALUES).read_text().replace("enabled: false", "enabled: true"))
        self.commit("human enable")
        self.git("checkout", "main")
        with self.assertRaisesRegex(ValueError, "outside its digest"):
            self.update(digest=NEXT, run=101, branch=branch)

    def test_provenance_missing_or_lock_mismatch_fails(self):
        with self.assertRaisesRegex(ValueError, "provenance"):
            CI.check_provenance(self.root)
        self.update()
        self.write(CI.LOCK, json.dumps({"images": {REPO: NEXT}}))
        self.commit("mismatch")
        with self.assertRaisesRegex(ValueError, "lock differ"):
            CI.check_provenance(self.root)

    def test_existing_branch_other_lock_entries_are_preserved(self):
        branch = self.bot_branch()
        self.git("checkout", branch)
        lock = json.loads((self.root / CI.LOCK).read_text())
        lock["images"]["other:image"] = DIGEST
        self.write(CI.LOCK, json.dumps(lock))
        self.commit("human lock edit")
        self.git("checkout", "main")
        with self.assertRaisesRegex(ValueError, "other image lock"):
            self.update(digest=NEXT, run=101, branch=branch)

    def test_workflow_privileged_jobs_are_main_only_even_with_successful_needs(self):
        text = (ROOT / ".github/workflows/alb-log-collector.yml").read_text()
        jobs = {m[1]: m[2] for m in re.finditer(r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:|\Z)", text, re.M | re.S)}
        for name in ("preflight", "publish", "image-pr"):
            guard = re.search(r"    if: >-\n((?:      .*\n)+)", jobs[name])[1]
            for event, ref, allowed in (("pull_request", "refs/pull/1/merge", False),
                                        ("pull_request", "refs/heads/main", False),
                                        ("push", "refs/heads/topic", False),
                                        ("workflow_dispatch", "refs/heads/topic", False),
                                        ("push", "refs/heads/main", True),
                                        ("workflow_dispatch", "refs/heads/main", True)):
                expr = " ".join(guard.split())
                expr = expr.replace("!cancelled()", "True").replace("&&", " and ").replace("||", " or ")
                expr = re.sub(r"needs\.[a-z-]+\.outputs\.image_changed", "'true'", expr)
                expr = re.sub(r"needs\.[a-z-]+\.result", "'success'", expr)
                expr = expr.replace("github.ref", repr(ref)).replace("github.event_name", repr(event))
                with self.subTest(job=name, event=event, ref=ref):
                    self.assertEqual(eval(expr, {"__builtins__": {}}), allowed)
        for name in ("changes", "verify", "collector-ci"):
            self.assertNotIn("secrets.", jobs[name])
            self.assertNotIn("id-token: write", jobs[name])
        self.assertNotIn("secrets.ALB_COLLECTOR_PR_TOKEN", jobs["publish"])
        self.assertNotIn("TERRAFORM_APPLY_ROLE_ARN", text)

    def test_preflight_missing_configuration_fails_without_echoing_token(self):
        text = (ROOT / ".github/workflows/alb-log-collector.yml").read_text()
        shell = text.split("Check main publication configuration before building", 1)[1]
        shell = shell.split("        run: |\n", 1)[1].split("\n  publish:", 1)[0]
        shell = "set -e\n" + "\n".join(line[10:] for line in shell.splitlines())
        env = dict(os.environ, PR_TOKEN="fixture-secret-not-for-output", AWS_ACCOUNT_ID="123456789012",
                   AWS_REGION="ap-northeast-2", PUSH_ROLE="arn:aws:iam::123456789012:role/iris-dev-github-ecr-alb-log-collector")
        result = subprocess.run(["bash", "-c", shell], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for key, value in (("PR_TOKEN", ""), ("AWS_REGION", "us-east-1"),
                           ("AWS_ACCOUNT_ID", "000000000000"),
                           ("PUSH_ROLE", "arn:aws:iam::123456789012:role/terraform-admin")):
            with self.subTest(key=key):
                result = subprocess.run(["bash", "-c", shell], env={**env, key: value}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(env["PR_TOKEN"], result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
