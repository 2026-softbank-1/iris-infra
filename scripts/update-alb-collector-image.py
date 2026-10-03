#!/usr/bin/env python3
"""Update only the collector digest/lock after a successful ECR publication."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re


SPEC = importlib.util.spec_from_file_location("collector_ci", Path(__file__).with_name("collector-ci-changes.py"))
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
DIGEST_LINE = re.compile(r'^  digest: "(?:sha256:[0-9a-f]{64})?"[^\n]*$', re.MULTILINE)
REPOSITORY = re.compile(r"[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/iris/alb-log-collector")


def validate_existing_branch(root, ref, repository):
    """Fail before create-pull-request can overwrite unrelated edits on its branch."""
    base = CI.git(root, "merge-base", "HEAD", ref).decode().strip()
    paths = set(CI.paths_between(root, base, ref))
    if not paths.issubset({CI.VALUES, CI.LOCK}):
        raise ValueError("Automation branch contains unrelated file changes")
    old_values = CI.git(root, "show", f"{base}:{CI.VALUES}").decode()
    values = CI.git(root, "show", f"{ref}:{CI.VALUES}").decode()
    if DIGEST_LINE.sub("", old_values) != DIGEST_LINE.sub("", values):
        raise ValueError("Automation branch changes collector settings outside its digest")
    old_lock = json.loads(CI.git(root, "show", f"{base}:{CI.LOCK}"))
    lock = json.loads(CI.git(root, "show", f"{ref}:{CI.LOCK}"))
    previous = CI.provenance(values)
    if lock["images"].get(repository) != previous[0]:
        raise ValueError("Automation branch digest and lock differ")
    old_lock["images"].pop(repository, None)
    lock["images"].pop(repository, None)
    if old_lock != lock:
        raise ValueError("Automation branch changes other image lock entries")
    # A PR updated with main can intentionally have stale provenance. The new
    # publication repairs that state; only the merge check must reject it.
    return previous


def update(root, repository, digest, source, run_id, attempt, existing_ref=None):
    if not REPOSITORY.fullmatch(repository) or repository.startswith("000000000000"):
        raise ValueError("Expected the dev collector ECR repository")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Invalid published digest")
    CI.sha(source)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", str(run_id)) or not re.fullmatch(r"[1-9][0-9]{0,9}", str(attempt)):
        raise ValueError("Invalid run ID/attempt")
    # Even a manual re-run must correspond to current main's runtime inputs.
    if CI.fingerprint(root, source) != CI.fingerprint(root, "HEAD"):
        return "stale-source"
    previous = validate_existing_branch(root, existing_ref, repository) if existing_ref else None
    values_path, lock_path = root / CI.VALUES, root / CI.LOCK
    text = values_path.read_text()
    if not re.search(r"^  repository: " + re.escape(repository) + r"$", text, re.MULTILINE):
        raise ValueError("Published repository does not match deployment values")
    lock = json.loads(lock_path.read_text())
    if lock.get("schema_version") != 1 or not isinstance(lock.get("images"), dict):
        raise ValueError("Unsupported image lock schema")
    # main may already contain a newer or identical publication too.
    if CI.PROVENANCE.search(text):
        main_previous = CI.provenance(text)
        if not previous or (main_previous[2], main_previous[3]) > (previous[2], previous[3]):
            previous = main_previous
    if previous and CI.fingerprint(root, previous[1]) == CI.fingerprint(root, source):
        if (int(run_id), int(attempt)) <= previous[2:]:
            return "older-run"
        if previous[0] == digest:
            return "unchanged"
    if lock["images"].get(repository) == digest and re.search(
            r'^  digest: "' + re.escape(digest) + '"(?: #.*)?$', text, re.MULTILINE):
        return "unchanged"
    line = f'  digest: "{digest}" # source: {source} run: {run_id} attempt: {attempt}'
    new_text, count = DIGEST_LINE.subn(line, text)
    if count != 1:
        raise ValueError("Expected exactly one collector image.digest scalar")
    lock["images"][repository] = digest
    # Perform every validation before either deployment input is written.
    values_path.write_text(new_text)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    return "updated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CI.ROOT)
    parser.add_argument("--existing-ref")
    args = parser.parse_args()
    result = update(args.root, os.environ["COLLECTOR_REPOSITORY"], os.environ["COLLECTOR_DIGEST"],
                    os.environ["COLLECTOR_SOURCE_SHA"], os.environ["COLLECTOR_RUN_ID"],
                    os.environ["COLLECTOR_RUN_ATTEMPT"], args.existing_ref)
    print(result)
    CI.output({"updated": result == "updated"})


if __name__ == "__main__":
    main()
