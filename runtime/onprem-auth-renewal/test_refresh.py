import base64
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import httpx

spec = importlib.util.spec_from_file_location('refresh', Path(__file__).with_name('refresh.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def token(subject):
    payload = base64.urlsafe_b64encode(json.dumps({'sub': subject}).encode()).decode().rstrip('=')
    return 'header.' + payload + '.DO_NOT_LOG_SIGNATURE'


class RefreshTests(unittest.TestCase):
    def exercise(self, mode, *, identity=None, wrong_subject=False, conflict=False):
        target, name = ('svc-28', 'iris-ecr-renewer') if mode == 'ecr' else ('iris-onprem-test', 'iris-argocd')
        old = token('wrong' if wrong_subject else f'system:serviceaccount:{target}:{name}')
        new = token(f'system:serviceaccount:{target}:{name}')
        namespace = 'iris-platform' if mode == 'ecr' else 'argocd'
        secret_name = 'renew-auth' if mode == 'ecr' else 'iris-onprem-01-cluster'
        original = {'bearerToken': old, 'tlsClientConfig': {'insecure': False, 'serverName': 'original'},
                    'unrelatedSetting': 'preserve'}
        data = {'server': module.encode(module.SERVER)}
        data.update({'token': module.encode(old)} if mode == 'ecr'
                    else {'config': module.encode(json.dumps(original))})
        local_patches, remote_calls = [], []

        def local_handler(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'metadata': {'resourceVersion': '7'}, 'data': data})
            payload = json.loads(request.content)
            local_patches.append(payload)
            return httpx.Response(409 if conflict else 200, json={})

        def remote_handler(request):
            self.assertEqual(request.extensions['sni_hostname'], 'kubernetes.default.svc')
            remote_calls.append(request)
            if request.method == 'POST':
                self.assertEqual(request.url.path, f'/api/v1/namespaces/{target}/serviceaccounts/{name}/token')
                return httpx.Response(201, json={'status': {'token': new,
                    'expirationTimestamp': (NOW + timedelta(hours=48)).isoformat()}})
            if request.method == 'GET':
                return httpx.Response(200, json={'metadata': {'resourceVersion': '11'},
                    'type': 'kubernetes.io/dockerconfigjson', 'data': {'.dockerconfigjson': module.encode(
                        json.dumps({'auths': {'other.example': {'auth': 'preserved-other-auth'}}}))}})
            self.assertEqual(request.url.path, '/api/v1/namespaces/svc-28/secrets/iris-ecr-pull')
            self.assertEqual(request.headers['content-type'], 'application/merge-patch+json')
            return httpx.Response(200, json={})

        class Ecr:
            def get_authorization_token(self, **kwargs):
                return {'authorizationData': [{'proxyEndpoint': 'https://187069338876.dkr.ecr.ap-northeast-2.amazonaws.com',
                    'authorizationToken': 'DO_NOT_LOG_AWS_TOKEN', 'expiresAt': NOW + timedelta(hours=12)}]}

        with httpx.Client(base_url='https://kubernetes.default.svc', transport=httpx.MockTransport(local_handler)) as local:
            with httpx.Client(transport=httpx.MockTransport(remote_handler)) as remote:
                result = module.run(mode, namespace, secret_name, local, remote,
                    identity=identity or {'Arn': 'arn:aws:sts::187069338876:assumed-role/iris-dev-onprem-ecr-svc-28/test'},
                    ecr=Ecr(), now=NOW)
        return result, local_patches, remote_calls, original

    def test_ecr_scoped_secret_and_no_credentials_in_result(self):
        result, patches, calls, _ = self.exercise('ecr')
        self.assertEqual(patches[0]['metadata']['resourceVersion'], '7')
        self.assertEqual(set(patches[0]['data']), {'token', 'expiresAt'})
        docker = json.loads(module.decode(json.loads(calls[-1].content)['data']['.dockerconfigjson']))
        self.assertEqual(docker['auths']['other.example']['auth'], 'preserved-other-auth')
        self.assertNotIn('DO_NOT_LOG', json.dumps(result))

    def test_argo_preserves_tls_and_other_config(self):
        result, patches, calls, original = self.exercise('argo')
        config = json.loads(module.decode(patches[0]['data']['config']))
        self.assertEqual(config['tlsClientConfig'], original['tlsClientConfig'])
        self.assertEqual(config['unrelatedSetting'], 'preserve')
        self.assertEqual(len(calls), 1)
        self.assertNotIn('DO_NOT_LOG', json.dumps(result))

    def test_wrong_aws_role_rejected(self):
        with self.assertRaises(ValueError):
            self.exercise('ecr', identity={'Arn': 'arn:aws:sts::187069338876:assumed-role/Administrator/test'})

    def test_wrong_onprem_identity_rejected(self):
        with self.assertRaises(ValueError):
            self.exercise('argo', wrong_subject=True)

    def test_resource_version_conflict_stops_rotation(self):
        with self.assertRaises(httpx.HTTPStatusError):
            self.exercise('argo', conflict=True)


if __name__ == '__main__':
    unittest.main()
