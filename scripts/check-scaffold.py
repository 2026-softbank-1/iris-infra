#!/usr/bin/env python3
"""Dependency-free checks for the repository skeleton, not deployment validation."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    roots = [
        "terraform/bootstrap/aws",
        "terraform/account/aws",
        "terraform/environments/aws/dev/foundation",
        "terraform/environments/aws/dev/management",
        "terraform/environments/aws/dev/workload",
    ]
    required = [
        "README.md", "Makefile", ".terraform-version",
        "docs/architecture.md", "docs/decisions/README.md",
        "docs/runbooks/bootstrap.md", "docs/runbooks/deploy-platform.md",
        "docs/runbooks/troubleshooting.md", "docs/runbooks/teardown.md",
        "local/README.md", "local/k3d.yaml.example",
        "docs/runbooks/eks-access.md", "contracts/target.schema.json",
        "helm/versions.json", "helm/images.lock.json", "helm/bootstrap/Chart.lock",
        "helm/gitops/values.schema.json",
        ".github/workflows/terraform-check.yml",
    ]
    for directory in roots:
        for name in (
            "versions.tf", "providers.tf", "backend.tf", "backend.hcl.example",
            "main.tf", "variables.tf", "outputs.tf", "terraform.tfvars.example", "README.md",
        ):
            required.append(f"{directory}/{name}")
    for name in ("main.tf", "variables.tf", "outputs.tf", "README.md"):
        required.append(f"terraform/modules/eks/{name}")
    for name in ("cluster-baseline", "iris-platform", "iris-service"):
        for filename in ("Chart.yaml", "values.yaml", "values.schema.json", "README.md"):
            required.append(f"helm/charts/{name}/{filename}")
        if not (ROOT / "helm/charts" / name / "templates").is_dir():
            raise SystemExit(f"Missing templates directory: {name}")
    for name in ("aws-dev-management", "aws-dev-workload", "local-workload"):
        required.append(f"clusters/{name}/cluster.yaml")
    for name in ("deployment", "target", "build", "namespace", "release"):
        required.append(f"contracts/{name}.md")
    for name in required:
        if not (ROOT / name).is_file():
            raise SystemExit(f"Missing file: {name}")
    for schema in [*(ROOT / "helm/charts").glob("*/values.schema.json"), ROOT/"helm/gitops/values.schema.json", ROOT/"contracts/target.schema.json"]:
        data = json.loads(schema.read_text())
        if data.get("type") != "object":
            raise SystemExit(f"Expected an object schema: {schema}")
    for script in (ROOT / "scripts").glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/check-platform-ecr.py")], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "examples/github-actions/build-push-ecr.sh")], check=True)
    print("Scaffold files, JSON schemas and shell syntax: OK")


if __name__ == "__main__":
    main()
