#!/usr/bin/env python3
"""Select infrastructure checks and Terraform deployment from the event's Git diff."""
import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath


SPEC = importlib.util.spec_from_file_location("collector_ci", Path(__file__).with_name("collector-ci-changes.py"))
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
CHECKS = ("tf_check", "collector_check", "helm_check", "platform_check", "ops_check")
APPLY_HELPERS = {".terraform-version", "scripts/tf-ci.sh", "scripts/common.sh",
                 "scripts/check-foundation-plan.py"}
TF_CHECK_INPUTS = APPLY_HELPERS | {
    "Makefile", ".github/workflows/terraform-check.yml",
    "scripts/tf-stack.sh", "scripts/terraform-check.sh", "scripts/terraform-test.sh",
    "scripts/terraform-ci-changes.py", "scripts/collector-ci-changes.py",
    "scripts/tests/test-tf-ci.py", "scripts/tests/test-terraform-ci-changes.py",
}
HELM_INPUTS = {
    "scripts/check-helm.py", "scripts/helm-check.sh", "scripts/check-alb-traffic.py",
    "scripts/tests/test-alb-traffic.py",
}
PLATFORM_INPUTS = {
    "examples/github-actions/build-push-ecr.sh", "examples/github-actions/build-push-ecr.yml",
    "scripts/check-platform-ecr.py", "scripts/tests/test-platform-ecr.py",
    "scripts/tests/test-build-push-ecr.py", "terraform/config/platform-ecr-repositories.json",
}
OPS_INPUTS = {
    "scripts/eks-ops.py", "scripts/check-eks-ci-permissions.py",
    "scripts/tests/test-eks-ops.py", "scripts/tests/test-eks-ci-permissions.py",
    "scripts/bootstrap-cluster.sh", "scripts/eks-api-tunnel.sh", "scripts/eks-preflight.sh",
    "scripts/export-targets.sh", "scripts/smoke-test.sh",
}
RUNTIME_PREFIXES = ("terraform/bootstrap/aws/", "terraform/environments/aws/dev/",
                    "terraform/modules/", "terraform/config/")


def documentation(path):
    return path.lower().endswith(".md")


def apply_input(path):
    if path in APPLY_HELPERS:
        return True
    if not path.startswith(RUNTIME_PREFIXES) or documentation(path):
        return False
    file = PurePosixPath(path)
    # Include runtime assets such as buildspec.yml, policy JSON and .scaffold,
    # rather than limiting deployment to .tf files. Account remains opt-in/local.
    return not (any(part in {"docs", "tests"} for part in file.parts) or
                file.name.endswith((".example", ".tftest.hcl")) or file.name.startswith("test_"))


def select(paths):
    flags = {name: False for name in (*CHECKS, "tf_apply")}
    for path in paths:
        if documentation(path):
            continue
        contract = path.startswith("contracts/") and path.endswith(".json")
        collector = CI.test_input(path)
        helm = path.startswith(("helm/", "clusters/")) or path in HELM_INPUTS or contract
        platform = path in PLATFORM_INPUTS
        ops = path in OPS_INPUTS or contract
        tf = path.startswith("terraform/") or path in TF_CHECK_INPUTS
        known = tf or collector or helm or platform or ops
        if path.startswith("scripts/") and not known:
            # New helpers must get validation until their narrower category is added.
            tf = True
        flags["tf_check"] |= tf
        flags["tf_apply"] |= apply_input(path)
        flags["collector_check"] |= collector
        flags["helm_check"] |= helm or collector
        flags["platform_check"] |= platform
        flags["ops_check"] |= ops
    if flags["tf_check"]:
        flags.update({name: True for name in CHECKS})
    if flags["helm_check"]:
        flags["ops_check"] = True
    return flags


def classify(root, event, current_sha, payload, force_apply=False):
    CI.sha(current_sha)
    CI.git(root, "rev-parse", "--verify", current_sha + "^{commit}")
    if event == "pull_request":
        base = CI.sha(payload["pull_request"]["base"]["sha"])
        paths = CI.paths_between(root, base, current_sha)
    elif event == "push":
        base = CI.sha(payload["before"])
        paths = ([os.fsdecode(row.split(b"\t", 1)[1]) for row in
                  CI.git(root, "ls-tree", "-r", "-z", current_sha, "--").split(b"\0") if row]
                 if set(base) == {"0"} else CI.paths_between(root, base, current_sha))
    elif event == "workflow_dispatch":
        # Manual checks do not infer permission to deploy from the current tree.
        return {**{name: True for name in CHECKS}, "tf_apply": force_apply}
    else:
        raise ValueError("Unsupported infrastructure CI event")
    return select(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CI.ROOT)
    args = parser.parse_args()
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    flags = classify(args.root, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"],
                     payload, os.environ.get("FORCE_APPLY") == "true")
    CI.output(flags)


if __name__ == "__main__":
    main()
