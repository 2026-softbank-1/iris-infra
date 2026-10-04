#!/usr/bin/env python3
"""Cloud-free regression tests of GCP CI trust gates and failure recovery."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CI, IMAGE, SETUP, AWS_CI = map(load, ('gcp-ci', 'gcp-image-ci', 'gcp-setup', 'terraform-ci-changes'))
SHA = 'a'*40
DIGEST = 'sha256:'+'b'*64
VALUES = dict(CI.DEFAULTS, project_id='iris-fixture-project', project_number='123456789012',
              state_bucket='iris-fixture-state', service_account='iris-gcp-terraform@iris-fixture-project.iam.gserviceaccount.com',
              workload_identity_provider='projects/123456789012/locations/global/workloadIdentityPools/iris-github-ci/providers/github',
              github_repository_id='123456789', github_owner_id='987654321',
              management_oidc_issuer='https://oidc.eks.ap-northeast-2.amazonaws.com/id/FIXTURE')
ENV = dict(GITHUB_REPOSITORY=CI.REPOSITORY, GITHUB_REF='refs/heads/main', GITHUB_SHA=SHA,
           GITHUB_EVENT_NAME='push', VERIFY_RESULT='success', RUNTIME_CHANGED='true',
           GCP_TERRAFORM_DEPLOY_ENABLED='true', GCP_ECR_PUBLISH_ENABLED='true',
           REQUESTED_ACTION='apply', GITHUB_REPOSITORY_ID=VALUES['github_repository_id'],
           GITHUB_REPOSITORY_OWNER_ID=VALUES['github_owner_id'])


class FakeRunner:
    def __init__(self, fail=None, destructive=False):
        self.calls = []
        self.fail, self.destructive = fail, destructive
        self.directory = None

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0] == 'git':
            return SHA+'\n'
        if command[0] == 'gh':
            return json.dumps({'sha': SHA})
        if command[0] == 'terraform':
            action = command[2]
            self.directory = Path(command[1].removeprefix('-chdir='))
            if action == self.fail:
                raise ValueError('fake command failure')
            if action == 'init':
                assert self.directory.stat().st_mode & 0o777 == 0o700
                assert not list(self.directory.glob('*.tfvars'))
                assert not (self.directory/'backend.hcl').exists()
            if action == 'plan':
                path = Path(next(x.removeprefix('-out=') for x in command if x.startswith('-out=')))
                assert path.stat().st_mode & 0o777 == 0o600
                path.write_text('secret-plan-fixture')
            if action == 'show':
                return json.dumps({'resource_changes': [{'address': 'google_container_cluster.workload', 'change': {'actions': ['delete', 'create'] if self.destructive else ['create'], 'after': {'password': 'never-log-this-value'}}}], 'output_changes': {'secret': {'after': 'never-log-this-output'}}})
            return 'never-log-this-provider-output'
        raise AssertionError(command)


def guard_expression(workflow, job):
    content = (ROOT/'.github/workflows'/workflow).read_text().split(f'  {job}:\n', 1)[1]
    return re.search(r'    if: >-\n(.*?)    runs-on:', content, re.S)[1].strip()


def evaluate(expression, *, event='push', ref='refs/heads/main', verify='success', flag=True, changed=True, action='plan', publish=False):
    for key, value in {'needs.verify.result': verify, 'github.ref': ref, 'github.event_name': event,
                       'vars.GCP_TERRAFORM_DEPLOY_ENABLED': 'true' if flag else '',
                       'vars.GCP_ECR_PUBLISH_ENABLED': 'true' if flag else '',
                       'needs.verify.outputs.changed': 'true' if changed else 'false',
                       'inputs.action': action, 'inputs.publish': publish}.items():
        expression = expression.replace(key, repr(value))
    return eval(' '.join(expression.split()).replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}})


class GateTest(unittest.TestCase):
    def test_workflow_gates_pr_flags_manual_and_failed_verification(self):
        tf = guard_expression('gcp-terraform.yml', 'workload')
        image = guard_expression('gcp-ecr-credentials.yml', 'publish')
        cases = [({}, True, True), ({'flag': False}, False, False), ({'changed': False}, False, False),
                 ({'event': 'pull_request', 'ref': 'refs/pull/1/merge'}, False, False),
                 ({'ref': 'refs/heads/topic'}, False, False),
                 ({'event': 'workflow_dispatch', 'changed': False, 'flag': False}, True, False),
                 ({'event': 'workflow_dispatch', 'action': 'apply', 'flag': False}, False, False),
                 ({'event': 'workflow_dispatch', 'action': 'apply', 'publish': True}, True, True)]
        for options, tf_result, image_result in cases:
            with self.subTest(options=options):
                self.assertEqual(evaluate(tf, **options), tf_result)
                self.assertEqual(evaluate(image, **options), image_result)
        for status in ('failure', 'cancelled', 'skipped'):
            self.assertFalse(evaluate(tf, verify=status))
            self.assertFalse(evaluate(image, verify=status))
        text = (ROOT/'.github/workflows/gcp-ecr-credentials.yml').read_text()
        verify, publisher = re.split(r'^  publish:\n', text, maxsplit=1, flags=re.M)
        self.assertIn('docker build --platform linux/amd64', verify)
        self.assertNotIn('id-token: write', verify)
        self.assertIn('needs: verify', publisher)
        self.assertIsNone(re.search(r'^    environment:', publisher, re.M))

    def test_cli_gates_fail_before_authentication(self):
        for invalid in ({'VERIFY_RESULT': 'failure'}, {'GITHUB_REF': 'refs/heads/topic'},
                        {'GITHUB_EVENT_NAME': 'pull_request'}, {'GCP_TERRAFORM_DEPLOY_ENABLED': 'false'},
                        {'RUNTIME_CHANGED': 'false'}, {'GITHUB_REPOSITORY': 'attacker/fork'}):
            with patch.dict(os.environ, ENV | invalid, clear=True):
                runner = FakeRunner()
                with self.assertRaises(ValueError):
                    CI.gate('terraform', 'apply', runner)
                self.assertEqual(runner.calls, [])
        with patch.dict(os.environ, ENV, clear=True):
            runner = FakeRunner()
            CI.gate('terraform', 'apply', runner)
            self.assertEqual([call[0][0] for call in runner.calls], ['git', 'gh'])
            for response in (SHA.replace('a', 'b'), '--help'):
                runner.run = lambda *args, **kwargs: json.dumps({'sha': response}) if args[0][0] == 'gh' else SHA
                with self.assertRaises(ValueError):
                    CI.gate('terraform', 'apply', runner)
        manual = ENV | {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'REQUESTED_ACTION': 'plan', 'GCP_TERRAFORM_DEPLOY_ENABLED': 'false'}
        with patch.dict(os.environ, manual, clear=True):
            CI.gate('terraform', 'plan', FakeRunner())
        with patch.dict(os.environ, ENV | {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'REQUESTED_ACTION': 'publish'}, clear=True):
            with self.assertRaises(ValueError):
                CI.gate('image', 'publish', FakeRunner())

    def test_config_id_project_sa_wif_and_prefix_fail_closed(self):
        self.assertEqual(CI.config(VALUES), VALUES)
        for field, value in [('project_id', ''), ('project_number', ''), ('state_prefix', 'gcp/account'),
                             ('region', 'us-central1'), ('service_account', 'other@iris-fixture-project.iam.gserviceaccount.com'),
                             ('workload_identity_provider', VALUES['workload_identity_provider'].replace('123456789012', '999999999999'))]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                CI.config(VALUES | {field: value})
        with patch.dict(os.environ, ENV | {'GITHUB_REPOSITORY_ID': '999'}, clear=True), self.assertRaises(ValueError):
            CI.check_ids(VALUES)

    def test_classification_excludes_account_bootstrap_docs_from_apply(self):
        ignored = ['terraform/account/gcp/main.tf', 'terraform/bootstrap/gcp/main.tf',
                   'scripts/tests/test-gcp-ci.py', 'terraform/config/gcp/ci.example.json',
                   'terraform/environments/gcp/dev/workload/README.md',
                   'terraform/environments/gcp/dev/workload/tests/workload.tftest.hcl']
        self.assertFalse(CI.changes(ignored, 'terraform'))
        self.assertFalse(CI.changes(['scripts/tests/test-gcp-ci.py', 'runtime/gcp-ecr-credentials/tests/test_credentials.py'], 'image'))
        for path in ['terraform/config/gcp/ci.example.json', 'terraform/account/gcp/main.tf',
                     '.github/workflows/gcp-terraform.yml', 'scripts/gcp-ci.py',
                     'scripts/gcp-image-ci.py', 'terraform/environments/aws/dev/gcp-access/github-publisher.tf']:
            self.assertFalse(AWS_CI.select([path])['tf_apply'], path)
        self.assertTrue(CI.changes(['terraform/environments/gcp/dev/workload/main.tf'], 'terraform'))
        self.assertTrue(CI.changes(['runtime/gcp-ecr-credentials/Dockerfile'], 'image'))


class TerraformTest(unittest.TestCase):
    def test_failed_real_cli_output_is_redacted(self):
        runner = CI.Runner()
        with self.assertRaises(CI.CIFailure) as error:
            runner.run([sys.executable, '-c', "import sys; print('fixture-secret-stdout'); print('fixture-secret-stderr',file=sys.stderr); sys.exit(1)"])
        self.assertNotIn('fixture-secret', str(error.exception))
        self.assertIsNone(runner.child)

    def test_exact_saved_plan_private_files_no_raw_values_or_artifacts(self):
        runner = FakeRunner()
        log = io.StringIO()
        with patch.dict(os.environ, {'TF_VAR_project_id': 'wrong', 'TF_CLI_ARGS': '-destroy', 'TF_LOG': 'TRACE'}, clear=True), contextlib.redirect_stdout(log):
            CI.terraform(VALUES, 'apply', runner)
        commands = [c for c, _ in runner.calls]
        self.assertEqual([c[2] for c in commands], ['init', 'plan', 'show', 'apply'])
        plan = next(x.removeprefix('-out=') for x in commands[1] if x.startswith('-out='))
        self.assertEqual(commands[-1][-1], plan)
        self.assertFalse(runner.directory.exists())
        for _, options in runner.calls:
            self.assertEqual(options['env']['TF_VAR_project_id'], VALUES['project_id'])
            self.assertNotIn('TF_CLI_ARGS', options['env'])
            self.assertNotIn('TF_LOG', options['env'])
        self.assertNotIn('never-log', log.getvalue())
        self.assertIn('google_container_cluster.workload: create', log.getvalue())

    def test_destructive_plan_and_each_failure_cleanup_and_no_apply(self):
        for fail, destructive in [('init', False), ('plan', False), ('show', False), (None, True), ('apply', False)]:
            runner = FakeRunner(fail=fail, destructive=destructive)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                CI.terraform(VALUES, 'apply', runner)
            self.assertFalse(runner.directory.exists())
            if fail != 'apply':
                self.assertNotIn('apply', [call[0][2] for call in runner.calls])
            # Remote partial state is never removed, force-unlocked or destroyed.
            self.assertFalse(any('destroy' in call[0] or 'force-unlock' in call[0] for call in runner.calls))

    def test_manual_plan_removes_plan_without_apply(self):
        runner = FakeRunner()
        with contextlib.redirect_stdout(io.StringIO()):
            CI.terraform(VALUES, 'plan', runner)
        self.assertNotIn('apply', [call[0][2] for call in runner.calls])
        self.assertFalse(runner.directory.exists())

    def test_sigterm_terminates_cli_and_removes_saved_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli = root/'terraform'
            cli.write_text('#!'+sys.executable+'\n'+'''import json,os,pathlib,sys,time
marker=pathlib.Path(os.environ['MARKER'])
action=sys.argv[2]
if action=='plan':
    pathlib.Path(next(v[5:] for v in sys.argv if v.startswith('-out='))).write_text('secret')
if action=='show': print(json.dumps({'resource_changes':[]}))
if action=='apply':
    marker.write_text(json.dumps({'pid':os.getpid(),'dir':sys.argv[1][8:]}))
    time.sleep(120)
''')
            cli.chmod(0o700)
            script = f"import runpy,signal; c=runpy.run_path({str(ROOT/'scripts/gcp-ci.py')!r}); signal.signal(signal.SIGTERM,c['cancelled']); c['terraform']({VALUES!r},'apply')"
            process = subprocess.Popen([sys.executable, '-c', script], env=dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(root), MARKER=str(root/'marker')), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic()+10
                while not (root/'marker').exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((root/'marker').exists(), process.poll())
                marker = json.loads((root/'marker').read_text())
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 143, stderr.decode())
                self.assertFalse(Path(marker['dir']).exists())
                with self.assertRaises(ProcessLookupError):
                    os.kill(marker['pid'], 0)
                self.assertNotIn(b'secret', stdout+stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


class PreflightTest(unittest.TestCase):
    def test_active_identity_bucket_and_refreshable_adc(self):
        request = 'https://pipelines.actions.githubusercontent.com/token?api-version=2'
        adc = {'type': 'external_account', 'audience': '//iam.googleapis.com/'+VALUES['workload_identity_provider'], 'token_url': 'https://sts.googleapis.com/v1/token', 'subject_token_type': 'urn:ietf:params:oauth:token-type:jwt',
               'service_account_impersonation_url': 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+VALUES['service_account']+':generateAccessToken',
               'credential_source': {'url': request+'&'+urlencode({'audience': 'https://iam.googleapis.com/'+VALUES['workload_identity_provider']}), 'headers': {'Authorization': 'Bearer fixture-token'}, 'format': {'type': 'json', 'subject_token_field_name': 'value'}}}
        responses = [{'projectId': VALUES['project_id'], 'projectNumber': VALUES['project_number'], 'lifecycleState': 'ACTIVE'},
                     [{'account': VALUES['service_account']}], {'project_number': VALUES['project_number']}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'adc.json'
            path.write_text(json.dumps(adc))
            env = {'GOOGLE_APPLICATION_CREDENTIALS': str(path), 'ACTIONS_ID_TOKEN_REQUEST_URL': request, 'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'fixture-token'}
            class Runner:
                def __init__(self, data): self.data = iter(data)
                def run(self, *args): return json.dumps(next(self.data))
            with patch.dict(os.environ, env, clear=True):
                CI.preflight(VALUES, Runner(responses))
                bad = [responses[0] | {'projectNumber': '999'}, [{'account': 'wrong'}], {'project_number': '999'}]
                for index in range(3):
                    data = responses.copy()
                    data[index] = bad[index]
                    with self.assertRaises(ValueError): CI.preflight(VALUES, Runner(data))
                for source in ({'file': '/fixed-token'}, adc['credential_source'] | {'headers': {'Authorization': 'Bearer wrong'}}):
                    path.write_text(json.dumps(adc | {'credential_source': source}))
                    with self.assertRaises(ValueError): CI.preflight(VALUES, Runner(responses))

    def test_offline_readiness_does_not_execute_cloud_commands(self):
        with patch.object(SETUP.CI.RUN, 'run', side_effect=AssertionError('Cloud called')), contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertFalse(SETUP.readiness(json.loads((ROOT/'terraform/config/gcp/ci.example.json').read_text())))
            self.assertTrue(SETUP.readiness(VALUES))
        self.assertIn('project_id', log.getvalue())

    def test_online_setup_is_read_only_and_checks_billing_apis_quota(self):
        required = ['serviceusage.googleapis.com', 'cloudresourcemanager.googleapis.com', 'iam.googleapis.com', 'iamcredentials.googleapis.com', 'sts.googleapis.com', 'storage.googleapis.com', 'compute.googleapis.com']
        class Runner:
            def __init__(self, billing=True): self.calls, self.billing = [], billing
            def run(self, command):
                self.calls.append(command)
                if command[1:3] == ['projects', 'describe']:
                    return json.dumps({'projectId': VALUES['project_id'], 'projectNumber': VALUES['project_number'], 'lifecycleState': 'ACTIVE'})
                if command[1:3] == ['billing', 'projects']: return json.dumps({'billingEnabled': self.billing})
                if command[1:3] == ['services', 'list']: return json.dumps([{'config': {'name': name}} for name in required])
                if command[1:3] == ['compute', 'regions']: return json.dumps({'quotas': [{'metric': 'E2_CPUS', 'limit': 8, 'usage': 0}]})
                raise AssertionError(command)
        runner = Runner()
        with contextlib.redirect_stdout(io.StringIO()) as log:
            SETUP.online(VALUES, runner)
        self.assertIn('Quota E2_CPUS', log.getvalue())
        self.assertTrue(all(command[0] == 'gcloud' and ('describe' in command or 'list' in command) for command in runner.calls))
        with self.assertRaises(ValueError): SETUP.online(VALUES, Runner(billing=False))

    def test_generated_metadata_is_private_and_destination_is_fixed(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(SETUP.CI, 'ROOT', Path(temporary)):
            destination = Path(temporary)/'.generated/gcp-ci.json'
            SETUP.write_config(destination, VALUES)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(destination.read_text()), VALUES)
            with self.assertRaises(ValueError): SETUP.write_config(Path(temporary)/'tracked.json', VALUES)


class ImageTest(unittest.TestCase):
    def runner(self, existing=False, bad_revision=False, mutable=False):
        repository = '123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials'
        class Runner:
            calls = None
            def __init__(self): self.calls = []
            def run(self, command, **kwargs):
                self.calls.append((command, kwargs))
                if command[:3] == ['aws', 'sts', 'get-caller-identity']:
                    return json.dumps({'Account': '123456789012', 'Arn': 'arn:aws:sts::123456789012:assumed-role/iris-dev-gcp-credentials-publisher/fixture'})
                if command[:3] == ['aws', 'ecr', 'describe-repositories']:
                    return json.dumps({'repositories': [{'repositoryUri': repository, 'registryId': '123456789012', 'imageTagMutability': 'MUTABLE' if mutable else 'IMMUTABLE'}]})
                if command[:3] == ['aws', 'ecr', 'list-images']:
                    return json.dumps({'imageIds': [{'imageTag': 'sha-'+SHA, 'imageDigest': DIGEST}] if existing else []})
                if command[:3] == ['aws', 'ecr', 'get-login-password']: return 'secret-ecr-password'
                if command[:3] == ['aws', 'ecr', 'describe-images']:
                    return json.dumps({'imageDetails': [{'imageTags': ['sha-'+SHA], 'imageDigest': DIGEST}]})
                if command[:3] == ['docker', 'image', 'inspect']: return 'wrong' if bad_revision else SHA
                if command[0] == 'docker': return 'never-log-docker-output'
                raise AssertionError(command)
        return Runner()

    def env(self):
        return {'AWS_ACCOUNT_ID': '123456789012', 'AWS_REGION': 'ap-northeast-2', 'GITHUB_SHA': SHA,
                'GCP_ECR_PUBLISH_ROLE_ARN': 'arn:aws:iam::123456789012:role/iris-dev-gcp-credentials-publisher',
                'GCP_ECR_REPOSITORY': '123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials'}

    def test_publish_and_duplicate_sha_reuse_never_overwrite(self):
        for existing in (False, True):
            runner = self.runner(existing)
            with patch.dict(os.environ, self.env(), clear=True), contextlib.redirect_stdout(io.StringIO()) as log:
                IMAGE.publish(runner)
            commands = [call[0] for call in runner.calls]
            self.assertEqual(any(c[:2] == ['docker', 'push'] for c in commands), not existing)
            self.assertFalse(any('delete' in ' '.join(c) for c in commands))
            self.assertNotIn('secret-ecr-password', log.getvalue())
            self.assertNotIn('never-log', log.getvalue())
            self.assertIn(DIGEST, log.getvalue())
            self.assertEqual(next(k['stdin'] for c, k in runner.calls if c[:2] == ['docker', 'login']), 'secret-ecr-password')

    def test_bad_existing_revision_mutable_or_wrong_repository_block_publish(self):
        with patch.dict(os.environ, self.env(), clear=True):
            for runner in (self.runner(existing=True, bad_revision=True), self.runner(mutable=True)):
                with self.assertRaises(ValueError): IMAGE.publish(runner)
                self.assertFalse(any(call[0][:2] == ['docker', 'push'] for call in runner.calls))
        with patch.dict(os.environ, self.env() | {'GCP_ECR_REPOSITORY': 'wrong'}, clear=True), self.assertRaises(ValueError):
            IMAGE.config()


if __name__ == '__main__':
    unittest.main()
