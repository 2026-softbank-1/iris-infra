#!/usr/bin/env python3
"""Validate the non-secret inventory shared by foundation and account."""
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "terraform/config/platform-ecr-repositories.json"
SUFFIX = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


def validate_inventory(value):
    if not isinstance(value, list) or not value:
        raise ValueError("Platform ECR inventory must be a non-empty JSON array.")
    if any(not isinstance(name, str) or not SUFFIX.fullmatch(name) for name in value):
        raise ValueError("Repository suffixes must be lowercase identifiers without slashes.")
    if len(value) != len(set(value)):
        raise ValueError("Platform ECR inventory contains duplicate repository suffixes.")
    if any(len(name) > 251 for name in value):
        raise ValueError("Repository suffix is too long for the iris/ prefix.")
    return value


def main():
    try:
        names = validate_inventory(json.loads(INVENTORY.read_text()))
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    print("Platform ECR inventory: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
