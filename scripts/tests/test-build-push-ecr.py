#!/usr/bin/env python3
"""Exercise the copyable script with fake tools; opt in to a local Docker build."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "examples/github-actions/build-push-ecr.sh"
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
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
    print(os.environ.get("FAKE_ACCOUNT", "123456789012"))
else:
    assert args[:2] == ["buildx", "build"]
    if os.environ.get("FAKE_DOCKER_FAILURE"):
        sys.exit(2)
    metadata = Path(args[args.index("--metadata-file") + 1])
    digest = os.environ.get("FAKE_DIGEST", "sha256:" + "b" * 64)
    metadata.write_text(json.dumps({"containerimage.digest": digest}))
'''


class BuildPushTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iris-ecr-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.context = self.directory / "build context"
        self.context.mkdir()
        (self.context / "Dockerfile").write_text("FROM scratch\nCOPY hello /hello\n")
        (self.context / "hello").write_text("fixture\n")
        binaries = self.directory / "bin"
        binaries.mkdir()
        for tool in ("docker", "aws"):
            path = binaries / tool
            path.write_text(FAKE_TOOL)
            path.chmod(0o755)
        self.log = self.directory / "calls.jsonl"
        self.output = self.directory / "outputs"
        self.env = {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "GITHUB_SHA": SHA,
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.directory / "summary"),
            "RUNNER_TEMP": str(self.directory),
            "DOCKERFILE": str(self.context / "Dockerfile"),
            "DOCKER_BUILD_CONTEXT": str(self.context),
            "FAKE_LOG": str(self.log),
        }

    def run_script(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args], env=self.env, text=True,
                              capture_output=True, timeout=20)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines()) if self.output.exists() else {}

    def configure_push(self):
        self.env.update(AWS_ACCOUNT_ID="123456789012", AWS_REGION="ap-northeast-2",
                        ECR_REPOSITORY="iris/was")

    def assert_clean_metadata(self):
        self.assertEqual(list(self.directory.glob("iris-build-metadata.*")), [])

    def test_build_needs_no_aws_configuration_and_never_pushes(self):
        result = self.run_script("build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["tool"] for call in self.calls()], ["docker"])
        args = self.calls()[0]["args"]
        self.assertIn("--load", args)
        self.assertNotIn("--push", args)
        self.assertEqual(args[-1], str(self.context))
        self.assertNotIn("image_ref", self.outputs())
        self.assert_clean_metadata()

    def test_push_returns_exact_registry_and_digest_reference(self):
        self.configure_push()
        result = self.run_script("push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["tool"] for call in self.calls()], ["aws", "docker"])
        args = self.calls()[1]["args"]
        self.assertIn("--push", args)
        self.assertNotIn("--load", args)
        repository = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/was"
        self.assertEqual(self.outputs()["image_ref"], repository + "@" + DIGEST)
        self.assertEqual(self.outputs()["image_digest"], DIGEST)
        self.assertEqual(self.outputs()["image_uri"], repository + f":sha-{SHA}-12345-1")
        self.assertNotIn("--cache-to", args)
        self.assert_clean_metadata()

    def test_rerun_uses_a_new_immutable_tag(self):
        self.configure_push()
        self.assertEqual(self.run_script("push").returncode, 0)
        first = self.outputs()["image_tag"]
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        self.assertEqual(self.run_script("push").returncode, 0)
        self.assertNotEqual(first, self.outputs()["image_tag"])

    def test_collector_uses_its_context_and_own_ecr_repository(self):
        self.configure_push()
        self.env.update(ECR_REPOSITORY="iris/alb-log-collector",
                        DOCKERFILE=str(ROOT / "collectors/alb-access-logs/Dockerfile"),
                        DOCKER_BUILD_CONTEXT=str(ROOT / "collectors/alb-access-logs"))
        result = self.run_script("push")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.calls()[1]["args"]
        self.assertEqual(args[-1], self.env["DOCKER_BUILD_CONTEXT"])
        self.assertEqual(args[args.index("--file") + 1], self.env["DOCKERFILE"])
        self.assertEqual(self.outputs()["image_ref"],
                         "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/alb-log-collector@" + DIGEST)

    def test_account_mismatch_blocks_docker(self):
        self.configure_push()
        self.env["FAKE_ACCOUNT"] = "999999999999"
        self.assertNotEqual(self.run_script("push").returncode, 0)
        self.assertEqual([call["tool"] for call in self.calls()], ["aws"])

    def test_registry_mismatch_blocks_all_tool_calls(self):
        self.configure_push()
        self.env["ECR_REGISTRY"] = "999999999999.dkr.ecr.ap-northeast-2.amazonaws.com"
        self.assertNotEqual(self.run_script("push").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_invalid_digest_is_not_reported_as_a_successful_push(self):
        self.configure_push()
        self.env["FAKE_DIGEST"] = "not-a-digest"
        self.assertNotEqual(self.run_script("push").returncode, 0)
        self.assertNotIn("image_ref", self.outputs())
        self.assert_clean_metadata()

    def test_failed_docker_build_cleans_metadata(self):
        self.env["FAKE_DOCKER_FAILURE"] = "1"
        self.assertNotEqual(self.run_script("build").returncode, 0)
        self.assertEqual(self.outputs(), {})
        self.assert_clean_metadata()

    def test_invalid_input_never_calls_tools(self):
        for args in ((), ("destroy",), ("build", "extra")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)
                self.assertEqual(self.calls(), [])
        for name, value in (("GITHUB_SHA", "bad"), ("GITHUB_RUN_ATTEMPT", "0"),
                            ("DOCKER_PLATFORM", "linux/amd64,linux/arm64")):
            with self.subTest(name=name):
                original = self.env.get(name)
                self.env[name] = value
                self.assertNotEqual(self.run_script("build").returncode, 0)
                self.assertEqual(self.calls(), [])
                if original is None:
                    del self.env[name]
                else:
                    self.env[name] = original

    @unittest.skipUnless(os.environ.get("IRIS_ECR_DOCKER_TEST") == "1", "Opt in with IRIS_ECR_DOCKER_TEST=1; needs a Docker daemon.")
    def test_real_docker_fixture_build(self):
        env = dict(self.env)
        env["PATH"] = os.environ["PATH"]
        result = subprocess.run(["bash", str(SCRIPT), "build"], env=env, text=True,
                                capture_output=True, timeout=120)
        try:
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            subprocess.run(["docker", "image", "rm", f"iris-build-check:sha-{SHA}-12345-1"],
                           env=env, capture_output=True, timeout=20)


if __name__ == "__main__":
    unittest.main()
