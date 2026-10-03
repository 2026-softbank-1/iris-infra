#!/usr/bin/env python3
"""Classify Git changes and verify the source of automated collector image PRs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BOT_BRANCH = "automation/alb-log-collector-image"
VALUES = "clusters/aws-dev-management/values/alb-log-collector.yaml"
LOCK = "helm/images.lock.json"
BUILD_INPUTS = {
    ".github/workflows/alb-log-collector.yml",
    "scripts/collector-ci-changes.py",
    "scripts/update-alb-collector-image.py",
    "examples/github-actions/build-push-ecr.sh",
}
PROVENANCE = re.compile(
    r'^  digest: "(sha256:[0-9a-f]{64})" # source: ([0-9a-f]{40}(?:[0-9a-f]{24})?)'
    r' run: ([1-9][0-9]{0,19}) attempt: ([1-9][0-9]{0,9})$', re.MULTILINE
)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
        raise ValueError("Invalid Git SHA")
    return value


def image_input(path):
    if path in BUILD_INPUTS:
        return True
    if not path.startswith("collectors/alb-access-logs/"):
        return False
    name = Path(path).name
    return not (name.endswith(".md") or name.startswith("test_") or name == "integration.py")


def test_input(path):
    return (image_input(path) or path.startswith("collectors/alb-access-logs/") and
            not path.endswith(".md") or
            path == "helm/charts/iris-alb-log-collector/files/traffic.yaml" or
            path in {"scripts/tests/test-collector-ci.py", "scripts/tests/test-build-push-ecr.py"})


def paths_between(root, base, head):
    # Disable rename detection so both the deleted and added path are classified.
    return [os.fsdecode(p) for p in git(root, "diff", "--no-renames", "--name-only", "-z",
                                       base, head, "--").split(b"\0") if p]


def fingerprint(root, revision):
    rows = []
    for row in git(root, "ls-tree", "-r", "-z", revision, "--").split(b"\0"):
        if row and image_input(os.fsdecode(row.split(b"\t", 1)[1])):
            rows.append(row)
    return hashlib.sha256(b"\0".join(sorted(rows))).hexdigest()


def classify(root, event, current_sha, payload, force=False):
    sha(current_sha)
    if event == "pull_request":
        base = sha(payload["pull_request"]["base"]["sha"])
        paths = paths_between(root, base, current_sha)
    elif event == "push":
        base = sha(payload["before"])
        paths = ([os.fsdecode(row.split(b"\t", 1)[1]) for row in
                  git(root, "ls-tree", "-r", "-z", current_sha, "--").split(b"\0") if row]
                 if set(base) == {"0"} else paths_between(root, base, current_sha))
    elif event == "workflow_dispatch":
        # A manual run is the initial publication/recovery path, with explicit opt-in.
        paths = []
    else:
        raise ValueError("Unsupported collector CI event")
    image_changed = any(map(image_input, paths)) or event == "workflow_dispatch" and force
    return {"image_changed": image_changed, "test_changed": image_changed or any(map(test_input, paths))}


def provenance(text):
    matches = list(PROVENANCE.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected one collector digest with source/run provenance")
    digest, source, run, attempt = matches[0].groups()
    return digest, source, int(run), int(attempt)


def check_provenance(root, revision="HEAD"):
    text = git(root, "show", f"{revision}:{VALUES}").decode()
    digest, source, _, _ = provenance(text)
    sha(source)
    if fingerprint(root, source) != fingerprint(root, revision):
        raise ValueError("Collector digest was built from different inputs; wait for a new image PR")
    values_repository = re.search(r"^  repository: ([^\s]+)$", text, re.MULTILINE)
    lock = json.loads(git(root, "show", f"{revision}:{LOCK}"))
    if not values_repository or lock["images"].get(values_repository[1]) != digest:
        raise ValueError("Collector digest and image lock differ")


def output(values):
    lines = [f"{key}={str(value).lower()}" for key, value in values.items()]
    print("\n".join(lines))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["changes", "check-provenance"])
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == "check-provenance":
        check_provenance(args.root)
    else:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        output(classify(args.root, os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"],
                        payload, os.environ.get("FORCE_REBUILD") == "true"))


if __name__ == "__main__":
    main()
