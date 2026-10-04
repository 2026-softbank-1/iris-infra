#!/usr/bin/env python3
"""Manual GCP state bootstrap/migration and explicit-project Terraform commands."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / '.generated/gcp-state-bootstrap'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def check_project():
    project = os.environ.get('GCP_PROJECT_ID', '')
    need(re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project), 'Set GCP_PROJECT_ID explicitly.')
    result = subprocess.run(['gcloud', 'projects', 'describe', project, '--format=json'], capture_output=True, text=True, check=True)
    metadata = json.loads(result.stdout)
    need(metadata['projectId'] == project and metadata['lifecycleState'] == 'ACTIVE', 'Project mismatch or inactive project.')
    return project


def stage(source):
    need(not STAGE.exists(), 'Local bootstrap already exists; preserve its state and use local-plan/local-apply/migrate.')
    STAGE.mkdir(parents=True, mode=0o700)
    for path in source.iterdir():
        if path.name != 'backend.tf' and (path.suffix == '.tf' or path.name in ('terraform.tfvars', '.terraform.lock.hcl')):
            shutil.copy2(path, STAGE/path.name)
    (STAGE/'backend.tf').write_text('terraform {\n  backend "local" {\n    path = "terraform.tfstate"\n  }\n}\n')
    os.chmod(STAGE/'terraform.tfvars', 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'plan', 'apply', 'local-init', 'local-plan', 'local-apply', 'migrate'))
    parser.add_argument('--stack', choices=('bootstrap/gcp', 'account/gcp', 'gcp/dev/workload'), default='bootstrap/gcp')
    args = parser.parse_args()
    project = check_project()
    source = ROOT/'terraform'/(args.stack if args.stack in ('bootstrap/gcp', 'account/gcp') else 'environments/gcp/dev/workload')
    local = args.action.startswith('local-') or args.action == 'migrate'
    need(not local or args.stack == 'bootstrap/gcp', 'Local state is only for first state-bucket creation.')
    need((source/'terraform.tfvars').is_file(), 'Copy terraform.tfvars.example and supply actual inputs.')
    action = args.action.removeprefix('local-')
    directory = STAGE if local else source
    if args.action == 'local-init':
        stage(source)
    if local:
        need((STAGE/'main.tf').is_file(), 'Run local-init first.')
    if args.action == 'migrate':
        need((STAGE/'terraform.tfstate').is_file(), 'Create the bucket with local-apply before migrating.')
        need((source/'backend.hcl').is_file(), 'Configure the new GCS bucket/prefix in backend.hcl.')
        shutil.copy2(source/'backend.tf', STAGE/'backend.tf')
        command = ['init', '-migrate-state', '-backend-config='+str(source/'backend.hcl')]
    elif action == 'init':
        command = ['init']
        if not local:
            need((source/'backend.hcl').is_file(), 'Configure backend.hcl before remote init.')
            command += ['-backend-config=backend.hcl']
    else:
        if local:
            need('backend "local"' in (STAGE/'backend.tf').read_text(), 'State is migrated; use the remote stack commands.')
            for path in source.glob('*.tf'):
                if path.name != 'backend.tf':
                    need((STAGE/path.name).read_bytes() == path.read_bytes(), 'Bootstrap configuration changed; review the staged configuration before proceeding.')
        command = [action, '-var=project_id='+project]
    # Interactive Terraform plan approval and migration confirmation are retained.
    subprocess.run(['terraform', '-chdir='+str(directory), *command], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('GCP Terraform stopped; check project, inputs and state prerequisites.') from None
