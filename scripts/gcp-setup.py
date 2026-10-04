#!/usr/bin/env python3
"""Offline readiness and explicitly requested read-only GCP/GitHub discovery."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re

SPEC = importlib.util.spec_from_file_location('gcp_ci', Path(__file__).with_name('gcp-ci.py'))
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


def write_config(path, data):
    target = (CI.ROOT/'.generated/gcp-ci.json').resolve()
    CI.need(path.resolve() == target and not path.is_symlink(), 'Only .generated/gcp-ci.json may be generated.')
    path.parent.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    os.chmod(path, 0o600)


def readiness(data):
    missing = [key for key in CI.FIELDS if key != 'base_domain' and not data.get(key)]
    if missing:
        print('Not ready. Fill: '+', '.join(missing))
        return False
    CI.config(data)
    print('Offline metadata ready. Project/billing/APIs/quota, real WIF/IAM, remote state and live deploy still require online verification.')
    return True


def online(values, runner=CI.RUN):
    CI.project(values, runner)
    billing = json.loads(runner.run(['gcloud', 'billing', 'projects', 'describe', values['project_id'], '--format=json']))
    CI.need(billing.get('billingEnabled') is True, 'Billing is not enabled.')
    enabled = json.loads(runner.run(['gcloud', 'services', 'list', '--enabled', '--project='+values['project_id'], '--format=json']))
    services = {row.get('config', {}).get('name') for row in enabled}
    required = {'serviceusage.googleapis.com', 'cloudresourcemanager.googleapis.com', 'iam.googleapis.com', 'iamcredentials.googleapis.com', 'sts.googleapis.com', 'storage.googleapis.com'}
    missing = sorted(required-services)
    print('Initial APIs: '+('ready' if not missing else 'missing '+', '.join(missing)))
    regions = (json.loads(runner.run(['gcloud', 'compute', 'regions', 'describe', values['region'], '--project='+values['project_id'], '--format=json']))
               if 'compute.googleapis.com' in services else {})
    if not regions:
        print('Compute API not enabled yet; regional quota inspection is pending workload API setup.')
    # Quota is observational, not a guarantee of GKE/Gateway capacity.
    for item in regions.get('quotas', []):
        if item.get('metric') in {'CPUS', 'E2_CPUS', 'IN_USE_ADDRESSES', 'SSD_TOTAL_GB', 'DISKS_TOTAL_GB'}:
            print(f"Quota {item['metric']}: limit={float(item['limit'])} usage={float(item['usage'])}")
    CI.need(not missing, 'Initial APIs are not ready; administrator setup is required.')
    print('Read-only checks complete. WIF claims/IAM, DNS/certificate and actual deployment remain separate checks.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('readiness', 'online', 'account-export', 'github-discover'))
    parser.add_argument('--config', type=Path, default=CI.ROOT/'terraform/config/gcp/ci.example.json')
    args = parser.parse_args()
    if args.command == 'github-discover':
        repository = json.loads(CI.RUN.run(['gh', 'api', f'repos/{CI.REPOSITORY}']))
        subject = json.loads(CI.RUN.run(['gh', 'api', f'repos/{CI.REPOSITORY}/actions/oidc/customization/sub']))
        print(json.dumps({'repository_id': int(repository['id']), 'owner_id': int(repository['owner']['id']), 'oidc_subject_configuration': subject}, indent=2))
        return
    data = json.loads(args.config.read_text())
    if args.command == 'account-export':
        need_project = os.environ.get('GCP_PROJECT_ID', '')
        CI.need(re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', need_project), 'Set expected GCP_PROJECT_ID.')
        values = json.loads(CI.RUN.run(['terraform', '-chdir='+str(CI.ROOT/'terraform/account/gcp'), 'output', '-json', 'ci']))
        CI.need(values.get('project_id') == need_project and set(values) <= set(CI.FIELDS) | set(CI.DEFAULTS), 'Unexpected account output.')
        data.update(values)
        write_config(CI.ROOT/'.generated/gcp-ci.json', data)
        print('Nonsecret account metadata written to .generated/gcp-ci.json. Add the management EKS issuer, then run readiness.')
    elif args.command == 'readiness':
        if not readiness(data):
            raise SystemExit(2)
    else:
        online(CI.config(data))


if __name__ == '__main__':
    try:
        main()
    except CI.CIFailure as error:
        raise SystemExit('Setup stopped: '+str(error)) from None
    except (ValueError, KeyError, OSError, json.JSONDecodeError):
        raise SystemExit('Setup stopped: invalid metadata or read-only query failed; raw cloud output suppressed.') from None
