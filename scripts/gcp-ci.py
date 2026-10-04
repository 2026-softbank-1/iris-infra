#!/usr/bin/env python3
"""Fail-closed, secret-safe GCP workload CI. No bootstrap/account apply."""
import argparse
import collections
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = '2026-softbank-1/iris-infra'
WORKLOAD = ROOT/'terraform/environments/gcp/dev/workload'
PREFIX = 'gcp/dev/workload'
FIELDS = {
    'project_id': 'GCP_PROJECT_ID', 'project_number': 'GCP_PROJECT_NUMBER',
    'state_bucket': 'GCP_STATE_BUCKET', 'service_account': 'GCP_TERRAFORM_SERVICE_ACCOUNT',
    'workload_identity_provider': 'GCP_WORKLOAD_IDENTITY_PROVIDER',
    'management_oidc_issuer': 'GCP_MANAGEMENT_OIDC_ISSUER',
    'github_repository_id': 'GCP_GITHUB_REPOSITORY_ID', 'github_owner_id': 'GCP_GITHUB_OWNER_ID',
    'base_domain': 'GCP_BASE_DOMAIN',
}
DEFAULTS = {'region': 'asia-northeast3', 'zone': 'asia-northeast3-a',
            'state_prefix': PREFIX, 'base_domain': 'gcp.likelion.uk'}


class CIFailure(ValueError):
    """Only messages constructed from validated metadata or fixed text are safe."""


def need(condition, message):
    if not condition:
        raise CIFailure(message)


def output(values):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a') as stream:
            for key, value in values.items():
                need(re.fullmatch(r'[a-z_]+', key) and '\n' not in str(value), 'Unsafe CI output.')
                stream.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')


def summary(message):
    print(message)
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a') as stream:
            stream.write(message+'\n')


class Runner:
    """Capture raw CLI output; terminate the entire child group on cancellation."""
    def __init__(self):
        self.child = None

    def run(self, command, *, env=None, stdin=None, allowed=(0,)):
        try:
            self.child = subprocess.Popen(command, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          env=env, start_new_session=True)
            stdout, _ = self.child.communicate(stdin.encode() if isinstance(stdin, str) else stdin)
            need(self.child.returncode in allowed, 'Command failed; raw output suppressed. Check provider/project/permissions and rerun a fresh plan.')
            return stdout.decode()
        finally:
            if self.child and self.child.poll() is None:
                self.stop()
            self.child = None

    def stop(self):
        if self.child and self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGTERM)
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                self.child.wait()


RUN = Runner()


def cancelled(signum, _frame):
    RUN.stop()
    raise SystemExit(128+signum)


def config(data=None):
    values = dict(DEFAULTS)
    values.update(data if data is not None else {key: os.environ.get(env, '') for key, env in FIELDS.items()})
    if not values.get('base_domain'):
        values['base_domain'] = DEFAULTS['base_domain']
    rules = {
        'project_id': r'[a-z][a-z0-9-]{4,28}[a-z0-9]',
        'project_number': r'[1-9][0-9]{5,19}',
        'state_bucket': r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',
        'github_repository_id': r'[1-9][0-9]+', 'github_owner_id': r'[1-9][0-9]+',
        'management_oidc_issuer': r'https://oidc\.eks\.ap-northeast-2\.amazonaws\.com/id/[A-Za-z0-9]+',
        'base_domain': r'[a-z0-9-]+(\.[a-z0-9-]+)+',
    }
    for key, rule in rules.items():
        need(isinstance(values.get(key), str) and re.fullmatch(rule, values[key]), f'Missing or invalid configuration: {key}.')
    need(values.get('service_account') == f"iris-gcp-terraform@{values['project_id']}.iam.gserviceaccount.com", 'Unexpected Terraform service account.')
    need(values.get('workload_identity_provider') == f"projects/{values['project_number']}/locations/global/workloadIdentityPools/iris-github-ci/providers/github", 'Unexpected project or WIF provider.')
    need(all(values.get(key) == value for key, value in DEFAULTS.items() if key != 'base_domain'), 'Unexpected workload prefix, region or zone.')
    need(set(values) <= set(FIELDS) | set(DEFAULTS), 'Unknown configuration fields.')
    return values


def sha(value):
    need(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{40}', value), 'Invalid source SHA.')
    return value


def changes(paths, kind):
    prefixes = ('terraform/environments/gcp/dev/workload/', 'terraform/config/gcp/') if kind == 'terraform' else ('runtime/gcp-ecr-credentials/',)
    helpers = {'scripts/gcp-ci.py'}
    helpers |= ({'.terraform-version', '.github/workflows/gcp-terraform.yml', 'scripts/gcp-setup.py'} if kind == 'terraform' else {'.github/workflows/gcp-ecr-credentials.yml', 'scripts/gcp-image-ci.py'})
    return any(path in helpers or (path.startswith(prefixes) and not path.endswith(('.md', '.example', '.example.json', '.tftest.hcl')) and '/tests/' not in path) for path in paths)


def classify(kind):
    spec = importlib.util.spec_from_file_location('collector_ci', ROOT/'scripts/collector-ci-changes.py')
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    event = os.environ['GITHUB_EVENT_NAME']
    head = sha(os.environ['GITHUB_SHA'])
    payload = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    if event == 'workflow_dispatch':
        changed = False
    else:
        need(event in ('push', 'pull_request'), 'Unsupported event.')
        base = sha(payload['before'] if event == 'push' else payload['pull_request']['base']['sha'])
        paths = ([os.fsdecode(row.split(b'\t', 1)[1]) for row in ci.git(ROOT, 'ls-tree', '-r', '-z', head, '--').split(b'\0') if row]
                 if set(base) == {'0'} else ci.paths_between(ROOT, base, head))
        changed = changes(paths, kind)
    output({'changed': changed})
    flag = 'GCP_TERRAFORM_DEPLOY_ENABLED' if kind == 'terraform' else 'GCP_ECR_PUBLISH_ENABLED'
    if os.environ.get(flag) != 'true':
        summary(f'{flag} is disabled: cloud-free verification only; no automatic credentials or deployment.')


def gate(kind, action, runner=RUN):
    """Run before credentials and again immediately before any cloud writes."""
    need(os.environ.get('VERIFY_RESULT') == 'success', 'Verification did not succeed.')
    need(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY and os.environ.get('GITHUB_REF') == 'refs/heads/main', 'Only the expected repository main branch may authenticate.')
    event = os.environ.get('GITHUB_EVENT_NAME')
    need(event in ('push', 'workflow_dispatch'), 'Only push or manual main events may authenticate.')
    flag = 'GCP_TERRAFORM_DEPLOY_ENABLED' if kind == 'terraform' else 'GCP_ECR_PUBLISH_ENABLED'
    need(action in (('plan', 'apply') if kind == 'terraform' else ('publish',)), 'Unsupported action.')
    if action != 'plan':
        need(os.environ.get(flag) == 'true', 'Deployment/publishing is disabled.')
    if event == 'push':
        need(action != 'plan' and os.environ.get('RUNTIME_CHANGED') == 'true', 'Push requires enabled runtime changes.')
    else:
        need(os.environ.get('REQUESTED_ACTION') == action, 'Manual action mismatch.')
        if kind == 'image':
            need(os.environ.get('MANUAL_PUBLISH') == 'true', 'Manual publishing was not requested.')
    head = sha(os.environ.get('GITHUB_SHA'))
    need(runner.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).strip() == head, 'Checkout SHA mismatch.')
    latest = json.loads(runner.run(['gh', 'api', f'repos/{REPOSITORY}/commits/main']))
    need(sha(latest.get('sha')) == head, 'Source is stale; rerun from latest main.')


def check_ids(values):
    need(values['github_repository_id'] == os.environ.get('GITHUB_REPOSITORY_ID') and values['github_owner_id'] == os.environ.get('GITHUB_REPOSITORY_OWNER_ID'), 'Numeric repository/owner ID mismatch.')


def project(values, runner=RUN):
    metadata = json.loads(runner.run(['gcloud', 'projects', 'describe', values['project_id'], '--format=json']))
    need(metadata.get('projectId') == values['project_id'] and metadata.get('lifecycleState') == 'ACTIVE' and str(metadata.get('projectNumber')) == values['project_number'], 'Wrong or inactive GCP project.')


def preflight(values, runner=RUN):
    project(values, runner)
    identities = json.loads(runner.run(['gcloud', 'auth', 'list', '--filter=status:ACTIVE', '--format=json']))
    need(len(identities) == 1 and identities[0].get('account') == values['service_account'], 'Unexpected active GCP identity.')
    bucket = json.loads(runner.run(['gcloud', 'storage', 'buckets', 'describe', 'gs://'+values['state_bucket'], '--raw', '--format=json']))
    need(str(bucket.get('project_number', bucket.get('projectNumber'))) == values['project_number'], 'State bucket belongs to a different project.')
    adc = json.loads(Path(os.environ['GOOGLE_APPLICATION_CREDENTIALS']).read_text())
    expected = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+values['service_account']+':generateAccessToken'
    source = adc.get('credential_source', {})
    need(adc.get('type') == 'external_account' and adc.get('audience') == '//iam.googleapis.com/'+values['workload_identity_provider'] and adc.get('service_account_impersonation_url') == expected, 'ADC identity or audience mismatch.')
    request_url = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_URL', '')
    expected_url, actual_url = urlsplit(request_url), urlsplit(source.get('url', ''))
    expected_query = dict(parse_qsl(expected_url.query))
    expected_query['audience'] = 'https://iam.googleapis.com/'+values['workload_identity_provider']
    request_token = os.environ.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN', '')
    need(expected_url.scheme == 'https' and expected_url.netloc and not expected_url.fragment and
         (actual_url.scheme, actual_url.netloc, actual_url.path, actual_url.fragment) == (expected_url.scheme, expected_url.netloc, expected_url.path, '') and
         sorted(parse_qsl(actual_url.query)) == sorted(expected_query.items()) and request_token and
         source.get('headers', {}).get('Authorization') == 'Bearer '+request_token and
         source.get('format') == {'type': 'json', 'subject_token_field_name': 'value'}, 'ADC must use refreshable GitHub OIDC URL credentials for the exact provider, not a fixed token.')
    need(adc.get('token_url') == 'https://sts.googleapis.com/v1/token' and adc.get('subject_token_type') == 'urn:ietf:params:oauth:token-type:jwt', 'Unexpected STS endpoint/token type.')


def plan_summary(plan):
    need(plan.get('errored') is not True, 'Terraform plan is errored.')
    counts = collections.Counter()
    safe = []
    destructive = False
    for change in plan.get('resource_changes', []):
        address, actions = change.get('address', ''), change.get('change', {}).get('actions')
        need(re.fullmatch(r'[A-Za-z0-9_.\[\]"/-]+', address) and isinstance(actions, list) and actions and all(a in ('no-op', 'create', 'read', 'update', 'delete') for a in actions), 'Unsafe or unknown plan action.')
        counts.update(actions)
        destructive |= 'delete' in actions
        if actions != ['no-op']:
            safe.append(address+': '+', '.join(actions))
    summary('Terraform actions: '+json.dumps(dict(sorted(counts.items())))+'\n'+'\n'.join(safe))
    need(not destructive, 'Delete/replacement blocked. Review a separate operator plan; CI will not apply it.')


def terraform(values, action, runner=RUN):
    # Never use checkout-local backend, tfvars, cached providers, plans or state.
    with tempfile.TemporaryDirectory(prefix='iris-gcp-ci-', dir=os.environ.get('RUNNER_TEMP')) as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        for source in WORKLOAD.iterdir():
            if (source.suffix == '.tf' and not source.name.endswith(('_override.tf',)) and source.name != 'override.tf') or source.name == '.terraform.lock.hcl':
                shutil.copy2(source, directory/source.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('TF_VAR_', 'TF_CLI_ARGS', 'TF_LOG')) and k not in ('TF_DATA_DIR', 'TF_WORKSPACE', 'TF_CLI_CONFIG_FILE', 'GOOGLE_CREDENTIALS', 'GOOGLE_BACKEND_CREDENTIALS', 'GOOGLE_IMPERSONATE_SERVICE_ACCOUNT', 'GOOGLE_BACKEND_IMPERSONATE_SERVICE_ACCOUNT', 'GOOGLE_OAUTH_ACCESS_TOKEN', 'GOOGLE_BACKEND_ACCESS_TOKEN')}
        env.update(TF_IN_AUTOMATION='true', TF_INPUT='false', TF_DATA_DIR=str(directory/'.terraform'),
                   TF_VAR_project_id=values['project_id'], TF_VAR_management_oidc_issuer=values['management_oidc_issuer'], TF_VAR_base_domain=values['base_domain'])
        command = ['terraform', '-chdir='+str(directory)]
        runner.run(command+['init', '-input=false', '-lockfile=readonly', '-backend-config=bucket='+values['state_bucket'], '-backend-config=prefix='+PREFIX], env=env)
        plan = directory/'workload.tfplan'
        # Create with restrictive permissions before Terraform replaces its contents.
        plan.touch(mode=0o600)
        runner.run(command+['plan', '-input=false', '-lock-timeout=5m', '-out='+str(plan)], env=env)
        os.chmod(plan, 0o600)
        plan_summary(json.loads(runner.run(command+['show', '-json', str(plan)], env=env)))
        summary(f"Target: {values['project_id']} / asia-northeast3-a / {values['state_bucket']}/{PREFIX}")
        if action == 'apply':
            runner.run(command+['apply', '-input=false', '-lock-timeout=5m', str(plan)], env=env)
            summary('Saved workload plan applied. Live service/bootstrap verification remains required.')
        else:
            summary('Plan only. Saved plan removed; apply always creates a fresh plan.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('classify', 'prepare', 'run'))
    parser.add_argument('--action', choices=('plan', 'apply'), default='plan')
    args = parser.parse_args()
    if args.command == 'classify':
        classify('terraform')
        return
    gate('terraform', args.action)
    values = config()
    check_ids(values)
    if args.command == 'prepare':
        output({'provider': values['workload_identity_provider'], 'service_account': values['service_account'], 'project_id': values['project_id']})
    else:
        preflight(values)
        terraform(values, args.action)


if __name__ == '__main__':
    for event in (signal.SIGTERM, signal.SIGINT):
        signal.signal(event, cancelled)
    try:
        main()
    except CIFailure as error:
        raise SystemExit('GCP CI stopped: '+str(error)) from None
    except (ValueError, KeyError, OSError, json.JSONDecodeError):
        raise SystemExit('GCP CI stopped: invalid gate/configuration or command failure. Raw credentials, state and provider output are suppressed.') from None
