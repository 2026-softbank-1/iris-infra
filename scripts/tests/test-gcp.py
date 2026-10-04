#!/usr/bin/env python3
"""Cloud-free failure/rotation/ownership tests using fictional identities only."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ops = module('gcp_ops', 'scripts/gcp-ops.py')
runtime = module('credentials', 'runtime/gcp-ecr-credentials/credentials.py')
eks = module('eks_ops', 'scripts/eks-ops.py')
ci = module('ci', 'scripts/terraform-ci-changes.py')
tf = module('gcp_tf', 'scripts/gcp-terraform.py')


def namespace(name, **labels):
    return {'metadata': {'name': name, 'labels': {runtime.LABEL: 'gcp', 'iris.dev/target': 'gcp-dev-workload', **labels}}}


def secret(expiry=0):
    return {'metadata': {'name': runtime.SECRET, 'resourceVersion': '1', 'labels': {runtime.LABEL: 'gcp'}, 'annotations': {runtime.EXPIRY: str(expiry)}}, 'type': 'kubernetes.io/dockerconfigjson', 'data': {'.dockerconfigjson': 'blank'}}


class FakeKube:
    def __init__(self):
        self.items = [namespace('iris-system'), namespace('svc-1')]
        self.secrets = {'iris-system': secret(), 'svc-1': secret()}
        self.reads, self.patches = [], []
    def namespaces(self):
        return iter(self.items)
    def secret(self, name):
        self.reads.append(name)
        return copy.deepcopy(self.secrets[name])
    def patch(self, name, previous, data, expiry):
        assert previous['metadata']['resourceVersion'] == self.secrets[name]['metadata']['resourceVersion']
        self.patches.append(name)
        self.secrets[name]['data']['.dockerconfigjson'] = data
        self.secrets[name]['metadata']['annotations'][runtime.EXPIRY] = str(expiry)


class FakeAWS:
    def __init__(self):
        self.calls, self.fail, self.expiry = 0, False, 45000
    def fetch(self):
        self.calls += 1
        if self.fail:
            raise ValueError('fixture-sensitive-error-must-not-be-logged')
        return 'fixture-token-'+str(self.calls), self.expiry


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.aws, self.kube = FakeAWS(), FakeKube()
        self.r = runtime.Reconciler(self.aws, self.kube, clock=lambda: self.now)
    def test_new_namespace_gets_cached_token_and_hourly_rotation(self):
        self.r.tick()
        self.assertTrue(self.r.ready())
        self.kube.items.append(namespace('svc-2'))
        self.kube.secrets['svc-2'] = secret()
        self.r.tick()
        self.assertEqual(self.aws.calls, 1)
        self.assertEqual(self.kube.secrets['svc-2']['data'], self.kube.secrets['svc-1']['data'])
        self.assertEqual(self.kube.patches, ['iris-system', 'svc-1', 'svc-2'])
        self.now += 3600
        self.aws.expiry += 3600
        self.r.tick()
        self.assertEqual(self.aws.calls, 2)
        self.assertEqual(self.kube.patches[-3:], ['iris-system', 'svc-1', 'svc-2'])
    def test_fetch_failure_preserves_valid_cache_for_new_namespace(self):
        self.r.tick()
        self.now += 3600
        self.aws.fail = True
        self.kube.items.append(namespace('svc-2'))
        self.kube.secrets['svc-2'] = secret()
        with patch('builtins.print') as log:
            self.r.tick()
        self.assertTrue(self.r.ready())
        self.assertEqual(self.kube.secrets['svc-2']['data'], self.kube.secrets['svc-1']['data'])
        self.assertEqual(log.call_args.args, ('credential_refresh_failed',))
        self.now = 50000
        with patch('builtins.print'):
            self.r.tick()
        self.assertFalse(self.r.ready())
    def test_namespace_and_secret_ownership_boundaries(self):
        terminating = namespace('svc-4')
        terminating['metadata']['deletionTimestamp'] = 'fixture'
        self.kube.items += [namespace('svc-3', **{'iris.dev/target': 'aws-dev-workload'}), namespace('svc-abc'), namespace('other'), terminating]
        self.kube.secrets['svc-1']['type'] = 'Opaque'
        with patch('builtins.print'):
            self.r.tick()
        self.assertEqual(self.kube.reads, ['iris-system', 'svc-1'])
        self.assertEqual(self.kube.patches, ['iris-system'])
    def test_patch_only_data_expiry_and_resource_version(self):
        k = object.__new__(runtime.Kubernetes)
        with patch.object(k, 'call') as call:
            k.patch('svc-1', secret(), 'new', 12345)
        path, method, body = call.call_args.args
        self.assertEqual(path, '/api/v1/namespaces/svc-1/secrets/iris-ecr-pull')
        self.assertEqual(method, 'PATCH')
        self.assertEqual(body, {'metadata': {'resourceVersion': '1', 'annotations': {runtime.EXPIRY: '12345'}}, 'data': {'.dockerconfigjson': 'new'}})
    def test_google_identity_claims_reject_wrong_audience_subject_and_expiry(self):
        claims = {'iss': 'https://accounts.google.com', 'sub': '123456789012345678901', 'azp': '123456789012345678901', 'aud': 'iris-gcp-ecr-pull', 'exp': 9000}
        def token(c):
            return 'fixture.'+base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip('=')+'.fixture'
        runtime.validate_claims(token(claims), claims['sub'], claims['aud'], 1000)
        for field, value in [('iss', 'other'), ('sub', 'other'), ('azp', 'other'), ('aud', 'other'), ('exp', 1000)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.validate_claims(token({**claims, field: value}), claims['sub'], claims['aud'], 1000)
    def test_fetch_exchanges_google_token_for_explicit_short_lived_credentials(self):
        from datetime import datetime, timezone
        account, subject = '123456789012', '123456789012345678901'
        aws = runtime.AWS(account, f'arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull', subject, 'iris-gcp-ecr-pull')
        claims = {'iss': 'https://accounts.google.com', 'sub': subject, 'azp': subject, 'aud': aws.audience, 'exp': 9000}
        token = 'fixture.'+base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')+'.fixture'
        response = Mock();response.read.return_value = token.encode();response.__enter__ = Mock(return_value=response);response.__exit__ = Mock(return_value=False)
        opener = Mock();opener.open.return_value = response
        sts, ecr = Mock(), Mock()
        sts.assume_role_with_web_identity.return_value = {'Credentials': {'AccessKeyId': 'fixture-id', 'SecretAccessKey': 'fixture-secret', 'SessionToken': 'fixture-session'}}
        ecr.get_authorization_token.return_value = {'authorizationData': [{'proxyEndpoint': 'https://'+aws.registry, 'authorizationToken': 'fixture-auth', 'expiresAt': datetime.fromtimestamp(45000, timezone.utc)}]}
        client = Mock(side_effect=[sts, ecr])
        fake = {'boto3': SimpleNamespace(client=client), 'botocore': SimpleNamespace(UNSIGNED='unsigned'), 'botocore.config': SimpleNamespace(Config=lambda **kwargs: kwargs)}
        with patch.dict('sys.modules', fake), patch.object(runtime.urllib.request, 'build_opener', return_value=opener), patch.object(runtime.time, 'time', return_value=1000):
            data, expiry = aws.fetch()
        self.assertEqual(expiry, 45000)
        self.assertEqual(json.loads(base64.b64decode(data)), {'auths': {aws.registry: {'auth': 'fixture-auth'}}})
        self.assertEqual(sts.assume_role_with_web_identity.call_args.kwargs['WebIdentityToken'], token)
        self.assertEqual(client.call_args_list[1].kwargs['aws_session_token'], 'fixture-session')
        self.assertEqual(ecr.get_authorization_token.call_args.kwargs, {'registryIds': [account]})


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.t = json.loads((ROOT/'contracts/gcp-target.example.json').read_text())
    def test_target_rejects_cross_project_unknown_credentials_wrong_endpoint(self):
        ops.validate(self.t, self.t['project_id'])
        for change in [{'project_id': 'other-project'}, {'ca_data': 'fixture'}, {'endpoint': 'https://aws.eks.amazonaws.com'}, {'ecr_service_account_id': '0'}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                ops.validate({**self.t, **change}, self.t['project_id'])
    def test_preflight_blocks_public_nodes_ip_api_and_wrong_identity(self):
        cluster = {'status': 'RUNNING', 'name': self.t['name'], 'network': self.t['network'], 'subnetwork': self.t['subnet'], 'privateClusterConfig': {'enablePrivateNodes': True}, 'controlPlaneEndpointsConfig': {'dnsEndpointConfig': {'endpoint': self.t['endpoint'][8:], 'allowExternalTraffic': True}, 'ipEndpointsConfig': {'enabled': False}}, 'datapathProvider': 'ADVANCED_DATAPATH', 'workloadIdentityConfig': {'workloadPool': self.t['project_id']+'.svc.id.goog'}, 'gatewayApiConfig': {'channel': 'CHANNEL_STANDARD'}}
        with patch.object(ops, 'cloud', side_effect=[cluster, {'uniqueId': self.t['ecr_service_account_id']}]):ops.preflight(self.t)
        for field in ('public_nodes', 'ip_api', 'wrong_sa'):
            bad = copy.deepcopy(cluster)
            sa = self.t['ecr_service_account_id']
            if field == 'public_nodes':bad['privateClusterConfig']['enablePrivateNodes'] = False
            if field == 'ip_api':bad['controlPlaneEndpointsConfig']['ipEndpointsConfig']['enabled'] = True
            if field == 'wrong_sa':sa = '999999999999999999999'
            with self.subTest(field=field), patch.object(ops, 'cloud', side_effect=[bad, {'uniqueId': sa}]), self.assertRaises(ValueError):ops.preflight(self.t)
    def test_missing_published_image_stops_before_kubernetes_writes(self):
        args = SimpleNamespace(aws_account_id='123456789012', image='123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials@sha256:'+('a'*64))
        with patch.object(ops, 'preflight'), patch.object(ops, 'run', side_effect=[json.dumps({'Account': args.aws_account_id}), json.dumps({'imageDetails': []})]), patch.object(ops, 'kubectl') as kube, patch.object(ops, 'apply') as apply, self.assertRaises(ValueError):ops.prepare(self.t, args)
        kube.assert_not_called();apply.assert_not_called()
    def test_dns_tls_and_projected_identity_config(self):
        auth = json.loads(ops.cluster_secret(self.t)['stringData']['config'])
        self.assertEqual(auth['tlsClientConfig'], {'insecure': False})
        self.assertEqual(auth['execProviderConfig']['args'], ['gcp'])
        config = ops.credential_config(self.t)
        overlay = ops.argo_overlay(self.t)['argo-cd']
        self.assertEqual(overlay['controller'], overlay['server'])
        token = overlay['controller']['volumes'][0]['projected']['sources'][0]['serviceAccountToken']
        self.assertEqual(token['audience'], config['audience'])
        self.assertEqual(token['expirationSeconds'], 3600)
    def test_rebootstrap_registered_gcp_requires_opt_in_before_any_write(self):
        with patch.dict(os.environ, {'GITOPS_GCP_ENABLED': '0'}), patch.object(eks, 'kubectl', return_value='secret/iris-gcp-workload-cluster'), patch.object(eks, 'apply') as apply, self.assertRaises(ValueError):
            eks.gcp_bootstrap_inputs({})
        apply.assert_not_called()
    def test_bootstrap_blocks_unpublished_digest_and_expired_secret(self):
        account = '123456789012'
        g = {'enabled': True, 'endpoint': self.t['endpoint'], 'baseDomain': self.t['base_domain'], 'deniedCidrs': self.t['denied_cidrs'], 'addressName': 'iris-gcp-apps', 'certificateMap': 'iris-gcp-apps', 'chartRevision': 'iris-service-0.10.0', 'credentials': {'enabled': True, 'awsAccountId': account, 'roleArn': f'arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull', 'serviceAccountEmail': self.t['ecr_service_account'], 'serviceAccountId': self.t['ecr_service_account_id'], 'audience': self.t['ecr_audience'], 'kubeApiCidr': '10.60.15.1/32', 'image': f'{account}.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials@sha256:'+('a'*64)}}
        s = secret(9000)
        s['data']['.dockerconfigjson'] = base64.b64encode(json.dumps({'auths': {f'{account}.dkr.ecr.ap-northeast-2.amazonaws.com': {'auth': 'fixture'}}}).encode()).decode()
        ops.bootstrap_gate(self.t, g, s, now=lambda: 1000)
        for change in ['expiry', 'image', 'role', 'cidr']:
            bad_g, bad_s = copy.deepcopy(g), copy.deepcopy(s)
            if change == 'expiry':bad_s['metadata']['annotations'][runtime.EXPIRY] = '1100'
            if change == 'image':bad_g['credentials']['image'] = bad_g['credentials']['image'].split('@')[0]+':latest'
            if change == 'role':bad_g['credentials']['roleArn'] += '-other'
            if change == 'cidr':bad_g['credentials']['kubeApiCidr'] = '0.0.0.0/0'
            with self.subTest(change=change), self.assertRaises(ValueError):
                ops.bootstrap_gate(self.t, bad_g, bad_s, now=lambda: 1000)
    def test_wrong_kubeconfig_stops_operation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'config';path.touch()
            wrong = {'current-context': 'aws-dev-management', 'clusters': [{'cluster': {'server': self.t['endpoint']}}]}
            with patch.object(ops, 'kube_path', return_value=path), patch.object(ops, 'run', return_value=json.dumps(wrong)) as run, self.assertRaises(ValueError):
                ops.kubectl(self.t, 'apply', '-f', '-')
            self.assertEqual(run.call_count, 1)
    def test_cross_cloud_stacks_never_trigger_auto_apply(self):
        for path in ['terraform/bootstrap/gcp/main.tf', 'terraform/environments/gcp/dev/workload/main.tf', 'terraform/environments/aws/dev/gcp-access/main.tf', 'runtime/gcp-ecr-credentials/credentials.py']:
            flags = ci.select([path])
            self.assertFalse(flags['tf_apply'], path)
            self.assertTrue(flags['tf_check'] or flags['ops_check'], path)
        self.assertTrue(ci.select(['scripts/common.sh'])['tf_apply'])
    def test_state_stage_preserves_local_state_and_real_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp);source = root/'source';source.mkdir()
            (source/'main.tf').write_text('resource "null_resource" "fixture" {}')
            (source/'backend.tf').write_text('terraform {\n backend "gcs" {}\n}\n')
            (source/'terraform.tfvars').write_text('project_id = "iris-fixture-project"')
            with patch.object(tf, 'STAGE', root/'stage'):
                tf.stage(source)
                self.assertIn('backend "local"', (tf.STAGE/'backend.tf').read_text())
                self.assertIn('backend "gcs"', (source/'backend.tf').read_text())
                self.assertEqual((tf.STAGE/'terraform.tfvars').stat().st_mode & 0o777, 0o600)
                with self.assertRaises(ValueError):tf.stage(source)


if __name__ == '__main__':
    unittest.main()
