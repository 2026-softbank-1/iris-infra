#!/usr/bin/env python3
"""Exercise real Git diffs and the workflow gates without AWS/deployment calls."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("infra_ci", ROOT / "scripts/terraform-ci-changes.py")
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
WORKFLOW = (ROOT / ".github/workflows/terraform-check.yml").read_text()
JOBS = {m[1]: m[2] for m in re.finditer(r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:|\Z)", WORKFLOW, re.M | re.S)}


def condition(block, indent):
    match = re.search(r"^" + " " * indent + r"if: (.*)$", block, re.M)
    if not match:
        return "success()"
    if match[1] != ">-":
        return match[1]
    remainder = block[match.end() + 1:]
    return " ".join(line.strip() for line in
                    re.match(r"(?:" + " " * (indent + 2) + r"[^\n]*\n)+", remainder)[0].splitlines())


def evaluate(expression, flags, event="push", ref="refs/heads/main", force=False,
             changes="success", check="success", cancelled=False, run_id=100):
    expression = expression.replace("!cancelled()", repr(not cancelled))
    expression = expression.replace("always()", "True").replace("success()", repr(check == "success"))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"needs\.changes\.outputs\.([a-z_]+)",
                        lambda m: repr(str(flags[m[1]]).lower()), expression)
    expression = expression.replace("needs.changes.result", repr(changes)).replace("needs.check.result", repr(check))
    expression = expression.replace("github.event_name", repr(event)).replace("github.ref", repr(ref))
    expression = expression.replace("github.run_id", repr(run_id))
    expression = expression.replace("inputs.force_apply", repr(force))
    return eval(expression, {"__builtins__": {}})


def steps():
    return [m[0] for m in re.finditer(r"^      - (?:name|uses):.*?\n(?=^      - |\Z)",
                                     JOBS["check"], re.M | re.S)]


def named_step(name):
    return next(step for step in steps() if step.startswith("      - name: " + name + "\n"))


class InfrastructureChangesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iris-infra-change-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("terraform/environments/aws/dev/foundation/main.tf", "fixture v1\n")
        self.base = self.commit("initial")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE).decode().strip()

    def write(self, path, text="changed\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def classify(self, base=None, event="push", force=False):
        payload = {"before": base or self.base,
                   "pull_request": {"base": {"sha": base or self.base}}}
        return CI.classify(self.root, event, self.git("rev-parse", "HEAD"), payload, force)

    def test_runtime_dependencies_select_full_checks_and_apply(self):
        paths = ["terraform/bootstrap/aws/main.tf", "terraform/modules/eks/main.tf",
                 "terraform/modules/eks/.terraform.lock.hcl",
                 "terraform/environments/aws/dev/foundation/buildspec.yml",
                 "terraform/environments/aws/dev/foundation/new-template.tpl",
                 "terraform/environments/aws/dev/workload/.scaffold",
                 "terraform/environments/aws/dev/management/.terraform.lock.hcl",
                 "terraform/config/aws-load-balancer-controller-policy.json",
                 "terraform/config/platform-ecr-repositories.json", *CI.APPLY_HELPERS]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(all(CI.select([path]).values()))

    def test_account_tests_examples_and_ci_changes_only_validate(self):
        paths = ["terraform/account/aws/ci-alb-access-logs.tf",
                 "terraform/account/aws/.terraform.lock.hcl",
                 "terraform/modules/eks/tests/eks.tftest.hcl",
                 "terraform/environments/aws/dev/foundation/tests/network.tftest.hcl",
                 "terraform/environments/aws/dev/foundation/terraform.tfvars.example",
                 "terraform/environments/aws/dev/workload/backend.hcl.example",
                 "terraform/config/tests/fixture.json", *CI.TF_CHECK_INPUTS - CI.APPLY_HELPERS]
        for path in paths:
            with self.subTest(path=path):
                flags = CI.select([path])
                self.assertFalse(flags["tf_apply"])
                self.assertTrue(all(flags[name] for name in CI.CHECKS))

    def test_unknown_script_runs_validation_but_never_apply(self):
        flags = CI.select(["scripts/new-helper.py"])
        self.assertTrue(flags["tf_check"])
        self.assertFalse(flags["tf_apply"])

    def test_docs_skip_all_expensive_checks(self):
        paths = ["README.md", "terraform/modules/eks/README.md", "scripts/README.md",
                 "collectors/alb-access-logs/README.md", "helm/charts/iris-service/README.md",
                 "docs/runbooks/terraform-ci.md"]
        self.assertFalse(any(CI.select(paths).values()))

    def test_collector_and_rules_keep_checks_without_terraform(self):
        for path in ("collectors/alb-access-logs/collector.py", "collectors/alb-access-logs/Dockerfile",
                     "collectors/alb-access-logs/test_collector.py", "collectors/alb-access-logs/integration.py",
                     "helm/charts/iris-alb-log-collector/files/traffic.yaml",
                     ".github/workflows/alb-log-collector.yml", "scripts/update-alb-collector-image.py",
                     "scripts/tests/test-collector-ci.py"):
            with self.subTest(path=path):
                flags = CI.select([path])
                self.assertTrue(flags["collector_check"] and flags["helm_check"] and flags["ops_check"])
                self.assertFalse(flags["tf_check"] or flags["tf_apply"])

    def test_helm_digest_cluster_contract_and_helper_changes(self):
        for path in ("helm/images.lock.json", "helm/versions.json",
                     "clusters/aws-dev-management/values/alb-log-collector.yaml",
                     "helm/charts/iris-service/templates/deployment.yaml", "contracts/target.schema.json",
                     *CI.HELM_INPUTS):
            with self.subTest(path=path):
                flags = CI.select([path])
                self.assertTrue(flags["helm_check"] and flags["ops_check"])
                self.assertFalse(flags["tf_check"] or flags["tf_apply"] or flags["collector_check"])

    def test_ops_helpers_run_their_tests_without_terraform(self):
        for path in CI.OPS_INPUTS:
            with self.subTest(path=path):
                flags = CI.select([path])
                self.assertTrue(flags["ops_check"])
                self.assertFalse(flags["tf_check"] or flags["tf_apply"])

    def test_platform_and_shared_build_helper(self):
        for path in CI.PLATFORM_INPUTS - {"terraform/config/platform-ecr-repositories.json"}:
            with self.subTest(path=path):
                flags = CI.select([path])
                self.assertTrue(flags["platform_check"])
                self.assertFalse(flags["tf_check"] or flags["tf_apply"])
        self.assertTrue(CI.select(["examples/github-actions/build-push-ecr.sh"])["collector_check"])

    def test_push_covers_all_commits_and_mixed_changes(self):
        self.write("terraform/environments/aws/dev/foundation/main.tf", "fixture v2\n")
        self.commit("runtime")
        self.write("README.md")
        self.commit("later docs")
        self.assertTrue(self.classify()["tf_apply"])
        flags = CI.select(["collectors/alb-access-logs/collector.py", "terraform/modules/eks/main.tf"])
        self.assertTrue(all(flags.values()))

    def test_delete_rename_and_nul_delimited_paths(self):
        (self.root / "docs").mkdir()
        self.git("mv", "terraform/environments/aws/dev/foundation/main.tf", "docs/moved.txt")
        self.write("docs/file\nwith-newline.txt")
        self.commit("rename")
        self.assertTrue(self.classify()["tf_apply"])
        paths = CI.CI.paths_between(self.root, self.base, "HEAD")
        self.assertIn("docs/file\nwith-newline.txt", paths)
        self.assertIn("terraform/environments/aws/dev/foundation/main.tf", paths)

    def test_pull_request_base_and_merge_tree(self):
        self.write("helm/images.lock.json")
        self.commit("digest PR")
        flags = self.classify(event="pull_request")
        self.assertTrue(flags["helm_check"])
        self.assertFalse(flags["tf_apply"])

    def test_initial_push_and_invalid_missing_shas_fail_closed(self):
        self.assertTrue(self.classify(base="0" * 40)["tf_apply"])
        with self.assertRaises(ValueError):
            self.classify(base="--help")
        with self.assertRaises(KeyError):
            CI.classify(self.root, "push", self.base, {})
        with self.assertRaises(ValueError):
            CI.classify(self.root, "pull_request_target", self.base, {})
        with self.assertRaises(subprocess.CalledProcessError):
            self.classify(base="b" * 40)

    def test_manual_checks_need_explicit_apply_option(self):
        flags = self.classify(event="workflow_dispatch")
        self.assertTrue(all(flags[name] for name in CI.CHECKS))
        self.assertFalse(flags["tf_apply"])
        self.assertTrue(self.classify(event="workflow_dispatch", force=True)["tf_apply"])
        # Force is not an override on ordinary pushes or PRs.
        self.write("README.md")
        self.commit("docs")
        self.assertFalse(self.classify(force=True)["tf_apply"])

    def test_cli_outputs_only_booleans_and_failure_outputs_nothing(self):
        self.write("helm/images.lock.json")
        head = self.commit("digest")
        payload = self.root / "event.json"
        payload.write_text(json.dumps({"before": self.base}))
        output = self.root / "output"
        env = dict(os.environ, GITHUB_EVENT_PATH=str(payload), GITHUB_EVENT_NAME="push",
                   GITHUB_SHA=head, GITHUB_OUTPUT=str(output))
        result = subprocess.run(["python3", str(ROOT / "scripts/terraform-ci-changes.py"), "--root", str(self.root)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tf_apply=false", output.read_text())
        self.assertIn("helm_check=true", output.read_text())
        output.unlink()
        result = subprocess.run(["python3", str(ROOT / "scripts/terraform-ci-changes.py"), "--root", str(self.root)],
                                env={**env, "GITHUB_SHA": "invalid"}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_actual_deploy_guard_is_main_only_and_changed_or_forced(self):
        guard = condition(JOBS["deploy"], 4)
        changed = CI.select(["terraform/modules/eks/main.tf"])
        unchanged = CI.select(["helm/images.lock.json"])
        cases = [
            (changed, "push", "refs/heads/main", False, True),
            (unchanged, "push", "refs/heads/main", False, False),
            (changed, "pull_request", "refs/pull/1/merge", False, False),
            (changed, "pull_request", "refs/heads/main", True, False),
            (changed, "push", "refs/heads/topic", False, False),
            (self.classify(event="workflow_dispatch"), "workflow_dispatch", "refs/heads/main", False, False),
            (self.classify(event="workflow_dispatch", force=True), "workflow_dispatch", "refs/heads/main", True, True),
            (self.classify(event="workflow_dispatch", force=True), "workflow_dispatch", "refs/heads/topic", True, False),
        ]
        for flags, event, ref, force, allowed in cases:
            with self.subTest(event=event, ref=ref, force=force):
                self.assertEqual(evaluate(guard, flags, event, ref, force), allowed)
        for changes, check in (("failure", "success"), ("cancelled", "success"),
                               ("success", "failure"), ("success", "skipped")):
            self.assertFalse(evaluate(guard, changed, changes=changes, check=check))
        for job in ("changes", "check"):
            self.assertNotIn("id-token: write", JOBS[job])
            self.assertNotIn("secrets.", JOBS[job])

    def test_required_check_runs_and_fails_when_detection_failed(self):
        flags = CI.select([])
        self.assertTrue(evaluate(condition(JOBS["check"], 4), flags, changes="failure"))
        block = named_step("Require successful change detection")
        shell = re.search(r"        run: (.*)\n", block)[1]
        for status, code in (("success", 0), ("failure", 1), ("cancelled", 1), ("skipped", 1)):
            result = subprocess.run(["bash", "-c", shell], env={**os.environ, "CHANGES_RESULT": status},
                                    capture_output=True)
            self.assertEqual(result.returncode, code)

    def test_unrelated_main_push_cannot_cancel_deployment_checks(self):
        expression = re.search(r"      group: terraform-check-\$\{\{ (.*) \}\}", JOBS["check"])[1]
        flags = CI.select([])
        self.assertNotEqual(evaluate(expression, flags, run_id=100), evaluate(expression, flags, run_id=101))
        self.assertEqual(evaluate(expression, flags, event="pull_request", ref="refs/pull/1/merge", run_id=100),
                         evaluate(expression, flags, event="pull_request", ref="refs/pull/1/merge", run_id=101))

    def test_helm_only_has_loki_without_prometheus_or_terraform(self):
        flags = CI.select(["helm/images.lock.json"])
        self.assertTrue(evaluate(condition(named_step("Install verified Loki for ruler or Helm validation"), 8), flags))
        self.assertFalse(evaluate(condition(named_step("Verify actual Loki ruler and Prometheus traffic results"), 8), flags))
        self.assertTrue(evaluate(condition(named_step("Render staged and fixture-enabled ALB traffic charts"), 8), flags))
        for block in steps():
            if "hashicorp/setup-terraform" in block or "make tf-check" in block or "terraform-test.sh" in block:
                self.assertFalse(evaluate(condition(block, 8), flags), block)

    def test_full_tf_checks_and_docs_skip_every_expensive_step(self):
        full = CI.select(["terraform/modules/eks/main.tf"])
        docs = CI.select(["docs/runbooks/observability.md"])
        self.assertNotIn("paths:", WORKFLOW)  # Preserve the required status on every PR.
        self.assertIn("default: false", WORKFLOW.split("force_apply:", 1)[1].split("permissions:", 1)[0])
        for block in steps():
            if "        if:" in block:
                self.assertTrue(evaluate(condition(block, 8), full), block)
                self.assertFalse(evaluate(condition(block, 8), docs), block)
        for name in ("Scaffold checks", "Test CI change conditions", "Lint workflows and Bash script"):
            self.assertTrue(evaluate(condition(named_step(name), 8), docs))


if __name__ == "__main__":
    unittest.main()
