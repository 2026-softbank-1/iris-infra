#!/usr/bin/env python3
"""Exercise CI orchestration with fake executables; never contact AWS or Terraform."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
STACKS = (
    "bootstrap/aws",
    "aws/dev/foundation",
    "aws/dev/management",
    "aws/dev/workload",
)
FAKE_TOOL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args}) + "\n")
if tool == "aws":
    assert args == ["sts", "get-caller-identity", "--query", "Account", "--output", "text"]
    print(os.environ["FAKE_ACCOUNT"])
else:
    directory = Path(args[0].removeprefix("-chdir="))
    action = args[1]
    if os.environ.get("FAKE_FAIL") == directory.name + ":" + action:
        sys.exit(2)
    assert os.environ["TF_VAR_aws_account_id"] == "123456789012"
    assert os.environ["TF_VAR_aws_region"] == "ap-northeast-2"
    if action == "plan":
        output = next(arg.removeprefix("-out=") for arg in args if arg.startswith("-out="))
        Path(output).write_text("saved plan for " + str(directory))
    elif action == "show":
        print(os.environ.get("FAKE_PLAN_JSON", '{"format_version":"1.2","resource_changes":[]}'))
    elif action == "apply":
        assert Path(args[-1]).read_text() == "saved plan for " + str(directory)
'''


class TerraformCITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iris-ci-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for name in ("common.sh", "tf-ci.sh", "check-foundation-plan.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        for stack in (*STACKS, "account/aws", "modules/eks"):
            directory = self.directory(stack)
            directory.mkdir(parents=True)
            (directory / "backend.tf").write_text('terraform {\n  backend "s3" {}\n}\n')
            (directory / ".terraform.lock.hcl").write_text("fake lock\n")
        for stack in STACKS[2:]:
            (self.directory(stack) / ".scaffold").touch()
        binaries = self.root / "bin"
        binaries.mkdir()
        for tool in ("aws", "terraform"):
            executable = binaries / tool
            executable.write_text(FAKE_TOOL)
            executable.chmod(0o755)
        self.plans = self.root / "plans"
        self.plans.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "AWS_ACCOUNT_ID": "123456789012",
            "AWS_REGION": "ap-northeast-2",
            "TF_STATE_BUCKET": "iris-tfstate-123456789012-ap-northeast-2",
            "RUNNER_TEMP": str(self.plans),
            "FAKE_ACCOUNT": "123456789012",
            "FAKE_LOG": str(self.log),
            "TF_VAR_operator_principal_arn": "arn:aws:iam::123456789012:role/operator",
        }

    def directory(self, stack):
        prefix = "terraform/environments" if stack.startswith("aws/dev/") else "terraform"
        return self.root / prefix / stack

    def run_script(self, action="apply", *extra):
        return subprocess.run(
            ["bash", str(self.root / "scripts/tf-ci.sh"), action, *extra],
            env=self.env, text=True, capture_output=True, timeout=15,
        )

    def calls(self, tool=None):
        records = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return [record["args"] for record in records if tool is None or record["tool"] == tool]

    def assert_no_plans(self):
        self.assertEqual(list(self.plans.iterdir()), [])

    def test_implemented_stacks_apply_in_order_with_saved_plans(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls("terraform")
        self.assertEqual([call[1] for call in calls], ["init", "plan", "apply", "init", "plan", "show", "apply"])
        self.assertEqual(len(self.calls("aws")), 1)
        for index, stack in enumerate(STACKS[:2]):
            init, plan, apply = calls[:3] if index == 0 else (calls[3], calls[4], calls[6])
            self.assertTrue(all(call[0] == f"-chdir={self.directory(stack)}" for call in (init, plan, apply)))
            self.assertIn(f"-backend-config=key={stack}/terraform.tfstate", init)
            self.assertIn("-lockfile=readonly", init)
            self.assertIn("-backend-config=encrypt=true", init)
            self.assertIn("-backend-config=use_lockfile=true", init)
            self.assertIn('-backend-config=allowed_account_ids=["123456789012"]', init)
            saved = next(arg.removeprefix("-out=") for arg in plan if arg.startswith("-out="))
            self.assertEqual(apply[-1], saved)
            self.assertIn("-lock-timeout=5m", plan)
            self.assertIn("-lock-timeout=5m", apply)
        self.assertIn("skip: aws/dev/management (scaffold)", result.stdout)
        self.assertIn("skip: aws/dev/workload (scaffold)", result.stdout)
        self.assert_no_plans()

    def test_all_four_stacks_use_distinct_state_keys(self):
        for stack in STACKS[2:]:
            (self.directory(stack) / ".scaffold").unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        inits = [call for call in self.calls("terraform") if call[1] == "init"]
        self.assertEqual([call[0] for call in inits], [f"-chdir={self.directory(stack)}" for stack in STACKS])
        self.assertEqual(
            [next(arg for arg in call if arg.startswith("-backend-config=key=")) for call in inits],
            [f"-backend-config=key={stack}/terraform.tfstate" for stack in STACKS],
        )
        self.assert_no_plans()

    def test_protected_foundation_changes_never_apply_or_run_clusters(self):
        for stack in STACKS[2:]:
            (self.directory(stack) / ".scaffold").unlink()
        for actions in (["delete"], ["delete", "create"], ["create", "delete"]):
            for address in ('aws_vpc.shared', 'aws_ecr_repository.platform["was"]', 'aws_subnet.private["management-0"]', 'aws_nat_gateway.egress["0"]', 'aws_iam_role.build_worker'):
                with self.subTest(actions=actions, address=address):
                    self.log.unlink(missing_ok=True)
                    self.env["FAKE_PLAN_JSON"] = json.dumps({"format_version":"1.2", "resource_changes":[{"address":address, "change":{"actions":actions}, "secret":"never-print-this"}]})
                    result=self.run_script()
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual([c[1] for c in self.calls("terraform")], ["init","plan","apply","init","plan","show"])
                    self.assertNotIn("never-print-this", result.stdout + result.stderr)
                    self.assert_no_plans()

    def test_new_nat_and_route_updates_are_allowed(self):
        self.env["FAKE_PLAN_JSON"]=json.dumps({"format_version":"1.2","resource_changes":[
            {"address":'aws_nat_gateway.egress["1"]',"change":{"actions":["create"]}},
            {"address":'aws_route.private_internet["workload-1"]',"change":{"actions":["update"]}}]})
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stderr)

    def test_invalid_show_output_fails_closed(self):
        self.env["FAKE_PLAN_JSON"]="not-json secret"
        result=self.run_script()
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn("not-json secret",result.stdout+result.stderr)
        self.assertEqual(self.calls("terraform")[-1][1],"show")
        self.assert_no_plans()

    def test_missing_operator_is_rejected_before_any_stack_runs(self):
        (self.directory(STACKS[2]) / ".scaffold").unlink()
        del self.env["TF_VAR_operator_principal_arn"]
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertEqual(self.calls(),[])

    def test_plan_mode_never_applies(self):
        result = self.run_script("plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[1] for call in self.calls("terraform")], ["init", "plan", "init", "plan", "show"])
        self.assert_no_plans()

    def test_preflight_fails_before_any_stack_runs(self):
        for broken in ("lock", "backend", "directory"):
            with self.subTest(broken=broken):
                directory = self.directory(STACKS[1])
                lock = directory / ".terraform.lock.hcl"
                backend = directory / "backend.tf"
                if broken == "lock":
                    lock.unlink()
                elif broken == "backend":
                    backend.write_text("# no backend\n")
                else:
                    shutil.rmtree(directory)
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.calls(), [])
                directory.mkdir(exist_ok=True)
                lock.write_text("fake lock\n")
                backend.write_text('terraform {\n  backend "s3" {}\n}\n')

    def test_bootstrap_scaffold_is_an_error(self):
        (self.directory(STACKS[0]) / ".scaffold").touch()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_account_mismatch_never_runs_terraform(self):
        self.env["FAKE_ACCOUNT"] = "999999999999"
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls("terraform"), [])
        self.assert_no_plans()

    def test_invalid_input_never_runs_tools(self):
        for args in (("destroy",), ("apply", "account/aws")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)
                self.assertEqual(self.calls(), [])
        self.env["AWS_ACCOUNT_ID"] = "000000000000"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_failure_stops_later_stacks_and_cleans_plans(self):
        for action in ("init", "plan", "apply"):
            with self.subTest(action=action):
                if self.log.exists():
                    self.log.unlink()
                self.env["FAKE_FAIL"] = "aws:" + action
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                calls = self.calls("terraform")
                self.assertEqual(calls[-1][1], action)
                self.assertTrue(all(call[0] == f"-chdir={self.directory(STACKS[0])}" for call in calls))
                self.assert_no_plans()

    def test_show_failure_blocks_foundation_and_clusters(self):
        for stack in STACKS[2:]:
            (self.directory(stack) / ".scaffold").unlink()
        self.env["FAKE_FAIL"] = "foundation:show"
        self.assertNotEqual(self.run_script().returncode,0)
        calls=self.calls("terraform")
        self.assertEqual(calls[-1][1],"show")
        self.assertEqual(sum(c[1]=="apply" for c in calls),1)
        self.assert_no_plans()

    def test_foundation_failure_stops_clusters_after_bootstrap_apply(self):
        for stack in STACKS[2:]:
            (self.directory(stack) / ".scaffold").unlink()
        self.env["FAKE_FAIL"] = "foundation:apply"
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls("terraform")
        self.assertEqual([call[1] for call in calls], ["init", "plan", "apply", "init", "plan", "show", "apply"])
        self.assertEqual(calls[2][0], f"-chdir={self.directory(STACKS[0])}")
        self.assertEqual(calls[-1][0], f"-chdir={self.directory(STACKS[1])}")
        self.assert_no_plans()


if __name__ == "__main__":
    unittest.main()
