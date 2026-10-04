"""Exercise real reconciliation against an in-memory Kubernetes API, never AWS."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

import httpx

import all_services as generic
from refresh import encode, decode
from test_refresh import token

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


class Api:
    def __init__(self):
        self.namespaces = {name: {'metadata': {'name': name, 'uid': name + '-uid'}}
                           for name in ('svc-28', 'svc-33', 'default', 'svc-0', 'svc-33-extra')}
        self.secrets = {}
        self.accounts = {name: {'metadata': {'resourceVersion': '1'}, 'imagePullSecrets': [{'name': 'other'}]}
                         for name in self.namespaces}
        self.pods, self.calls, self.sessions = {}, [], []
        self.faults, self.changed_uid = {}, set()
        self.paginate = False
        self.creation_conflict = False
        self.bootstrap_conflict = False
        self.bootstrap = {'metadata': {'resourceVersion': '7'}, 'data': {
            'server': encode(generic.SERVER), 'token': encode(token(generic.SUBJECT))}}
        self.now = NOW
        self.rs = {'metadata': {'uid': 'rs-uid', 'ownerReferences': [
            {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'app', 'uid': 'deployment-uid', 'controller': True}]},
            'spec': {'template': {'spec': {}}}}
        self.deployment = {'metadata': {'uid': 'deployment-uid'}, 'spec': {'template': {'spec': {}}}}

    def local(self, request):
        if request.method == 'GET':
            return httpx.Response(200, json=self.bootstrap)
        payload = json.loads(request.content)
        assert payload['metadata']['resourceVersion'] == self.bootstrap['metadata']['resourceVersion']
        if self.bootstrap_conflict:
            return httpx.Response(409, json={'message': 'NEVER_PRINT_BOOTSTRAP'})
        self.bootstrap['data'].update(payload['data'])
        self.bootstrap['metadata']['resourceVersion'] = '8'
        return httpx.Response(200, json=self.bootstrap)

    def remote(self, request):
        self.calls.append(request)
        assert request.extensions['sni_hostname'] == 'kubernetes.default.svc'
        path, method = request.url.path, request.method
        if (method, path) in self.faults:
            return httpx.Response(self.faults[method, path], json={'message': 'NEVER_PRINT_HTTP_BODY'})
        if path.endswith('/iris-ecr-renewer/token'):
            return httpx.Response(201, json={'status': {'token': token(generic.SUBJECT),
                'expirationTimestamp': (self.now + timedelta(hours=24)).isoformat()}})
        if path == '/api/v1/namespaces':
            items = list(self.namespaces.values())
            if self.paginate:
                return httpx.Response(200, json={'items': items[1:] if request.url.params.get('continue') else items[:1],
                    'metadata': {'continue': '' if request.url.params.get('continue') else 'next'}})
            return httpx.Response(200, json={'items': items})
        if path.startswith('/api/v1/namespaces/'):
            parts = path.split('/')
            name = parts[4]
            if len(parts) == 5:
                value = copy.deepcopy(self.namespaces.get(name))
                if value and name in self.changed_uid:
                    value['metadata']['uid'] = 'replacement-uid'
                return httpx.Response(200 if value else 404, json=value or {})
            resource = parts[5]
            if resource == 'secrets':
                if method == 'GET':
                    return httpx.Response(200 if name in self.secrets else 404, json=self.secrets.get(name, {}))
                payload = json.loads(request.content)
                if method == 'POST':
                    if self.creation_conflict:
                        self.secrets[name] = {'metadata': {'resourceVersion': '12'}, 'type': 'kubernetes.io/dockerconfigjson',
                            'data': {'.dockerconfigjson': encode(json.dumps({'auths': {'other.example': {'auth': 'keep'}}}))}}
                        return httpx.Response(409, json={})
                    assert name not in self.secrets
                    payload['metadata']['resourceVersion'] = '11'
                    self.secrets[name] = payload
                else:
                    assert payload['metadata']['resourceVersion'] == self.secrets[name]['metadata']['resourceVersion']
                    self.secrets[name]['data'].update(payload['data'])
                    self.secrets[name]['metadata'].update(payload['metadata'])
                return httpx.Response(200, json=self.secrets[name])
            if resource == 'serviceaccounts':
                if name not in self.accounts:
                    return httpx.Response(404, json={})
                if method == 'PATCH':
                    payload = json.loads(request.content)
                    assert payload['metadata']['resourceVersion'] == self.accounts[name]['metadata']['resourceVersion']
                    self.accounts[name]['imagePullSecrets'] = payload['imagePullSecrets']
                return httpx.Response(200, json=self.accounts[name])
            if resource == 'pods':
                if method == 'GET':
                    return httpx.Response(200, json={'items': self.pods.get(name, [])})
                payload = json.loads(request.content)
                pod = next(p for p in self.pods[name] if p['metadata']['name'] == parts[6])
                assert payload['preconditions'] == {'uid': pod['metadata']['uid'],
                                                     'resourceVersion': pod['metadata']['resourceVersion']}
                self.pods[name].remove(pod)
                return httpx.Response(200, json={'kind': 'Status'})
        if '/replicasets/' in path:
            return httpx.Response(200, json=self.rs)
        if path.endswith('/deployments/app'):
            return httpx.Response(200, json=self.deployment)
        raise AssertionError(f'Unexpected API {method} {path}')

    def assume_role(self, **kwargs):
        assert len(kwargs['RoleSessionName']) <= 64
        self.sessions.append(kwargs)
        return {'Credentials': {'AccessKeyId': 'NEVER_PRINT_ACCESS_KEY', 'SecretAccessKey': 'NEVER_PRINT_SECRET_KEY',
            'SessionToken': 'NEVER_PRINT_SESSION_TOKEN', 'Expiration': self.now + timedelta(hours=1)}}

    def ecr_factory(self, credentials):
        assert credentials['SessionToken'] == 'NEVER_PRINT_SESSION_TOKEN'
        return self

    def get_authorization_token(self, **kwargs):
        assert kwargs == {'registryIds': [generic.ACCOUNT]}
        return {'authorizationData': [{'proxyEndpoint': 'https://' + generic.REGISTRY,
            'authorizationToken': 'NEVER_PRINT_ECR_TOKEN', 'expiresAt': self.now + timedelta(hours=12)}]}

    def run(self, identity=None):
        with httpx.Client(base_url='https://kubernetes.default.svc', transport=httpx.MockTransport(self.local)) as local:
            with httpx.Client(transport=httpx.MockTransport(self.remote)) as remote:
                return generic.run_all('iris-platform', 'iris-onprem-ecr-renew-auth', local, remote,
                    identity=identity or {'Arn': generic.ISSUER + 'job'}, sts=self,
                    ecr_factory=self.ecr_factory, now=self.now)


def failed_pod():
    return {'metadata': {'name': 'app-pod', 'uid': 'pod-uid', 'resourceVersion': '25', 'ownerReferences': [
        {'apiVersion': 'apps/v1', 'kind': 'ReplicaSet', 'name': 'app-rs', 'uid': 'rs-uid', 'controller': True}]},
        'spec': {'serviceAccountName': 'default', 'containers': [
            {'image': generic.REGISTRY + '/iris/services/33@sha256:123'}]},
        'status': {'phase': 'Pending', 'containerStatuses': [
            {'state': {'waiting': {'reason': 'ImagePullBackOff'}}}]}}


class AllServicesTests(unittest.TestCase):
    def test_new_and_existing_services_receive_scoped_credentials_and_refs(self):
        api = Api()
        api.paginate = True
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertEqual(set(api.secrets), {'svc-28', 'svc-33'})
        for name, secret in api.secrets.items():
            sid = name.removeprefix('svc-')
            annotations = secret['metadata']['annotations']
            self.assertEqual(annotations['iris.dev/ecr-service-id'], sid)
            self.assertLess(generic.timestamp(annotations['iris.dev/sts-token-expires-at']),
                            generic.timestamp(annotations['iris.dev/ecr-token-expires-at']))
            self.assertEqual(api.accounts[name]['imagePullSecrets'], [{'name': 'other'}, {'name': generic.SECRET}])
        self.assertEqual({json.loads(s['Policy'])['Statement'][1]['Resource'] for s in api.sessions},
            {f'arn:aws:ecr:{generic.REGION}:{generic.ACCOUNT}:repository/iris/services/{sid}' for sid in ('28', '33')})
        self.assertTrue(all(s['RoleArn'] == generic.PULL_ROLE and s['DurationSeconds'] == 3600 for s in api.sessions))
        self.assertNotIn('NEVER_PRINT', json.dumps(result))

    def test_next_new_namespace_is_discovered_and_valid_secrets_not_rewritten(self):
        api = Api()
        api.run()
        api.sessions.clear()
        api.namespaces['svc-44'] = {'metadata': {'name': 'svc-44', 'uid': '44'}}
        api.accounts['svc-44'] = {'metadata': {'resourceVersion': '1'}}
        self.assertTrue(api.run()['ok'])
        self.assertEqual(len(api.sessions), 1)
        self.assertEqual(json.loads(api.sessions[0]['Policy']), generic.pull_policy('44'))
        self.assertEqual(api.accounts['svc-33']['imagePullSecrets'].count({'name': generic.SECRET}), 1)

    def test_renewal_uses_sts_expiry_and_preserves_other_registry(self):
        api = Api()
        api.run()
        secret = api.secrets['svc-33']
        docker = json.loads(decode(secret['data']['.dockerconfigjson']))
        docker['auths']['other.example'] = {'auth': 'keep'}
        secret['data']['.dockerconfigjson'] = encode(json.dumps(docker))
        secret['metadata']['annotations']['iris.dev/sts-token-expires-at'] = (NOW + timedelta(minutes=14)).isoformat()
        api.sessions.clear()
        self.assertTrue(api.run()['ok'])
        self.assertEqual(len(api.sessions), 1)
        self.assertEqual(json.loads(decode(secret['data']['.dockerconfigjson']))['auths']['other.example'], {'auth': 'keep'})

    def test_long_numeric_namespace_keeps_full_repository_scope(self):
        api = Api()
        sid = '1' * 59
        name = 'svc-' + sid
        api.namespaces[name] = {'metadata': {'name': name, 'uid': 'long-id'}}
        api.accounts[name] = {'metadata': {'resourceVersion': '1'}}
        self.assertTrue(api.run()['ok'])
        self.assertEqual(json.loads(api.sessions[-1]['Policy']), generic.pull_policy(sid))
        self.assertIn(name, api.secrets)

    def test_missing_sa_is_deferred_without_pod_deletion(self):
        api = Api()
        del api.accounts['svc-33']
        api.pods['svc-33'] = [failed_pod()]
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertEqual(result['services'][1]['status'], 'deferred')
        self.assertIn('svc-33', api.secrets)
        self.assertEqual(len(api.pods['svc-33']), 1)

    def test_namespace_recreation_does_not_write(self):
        api = Api()
        api.changed_uid.add('svc-33')
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertNotIn('svc-33', api.secrets)
        self.assertEqual(result['services'][1]['status'], 'deferred')

    def test_terminating_namespace_is_skipped(self):
        api = Api()
        api.namespaces['svc-33']['metadata']['deletionTimestamp'] = NOW.isoformat()
        self.assertEqual([r['namespace'] for r in api.run()['services']], ['svc-28'])

    def test_create_conflict_defers_and_rereads_without_overwrite(self):
        api = Api()
        api.creation_conflict = True
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertTrue(all(r['status'] == 'deferred' for r in result['services']))
        self.assertTrue(any(r.method == 'GET' and r.url.path.endswith('/secrets/iris-ecr-pull') for r in api.calls))
        api.creation_conflict = False
        self.assertTrue(api.run()['ok'])
        self.assertEqual(json.loads(decode(api.secrets['svc-33']['data']['.dockerconfigjson']))['auths']['other.example'], {'auth': 'keep'})

    def test_one_namespace_error_does_not_stop_others_or_log_response(self):
        api = Api()
        api.faults['POST', '/api/v1/namespaces/svc-28/secrets'] = 403
        result = api.run()
        self.assertFalse(result['ok'])
        self.assertEqual(result['services'][0]['statusCode'], 403)
        self.assertEqual(result['services'][1]['status'], 'ready')
        self.assertNotIn('NEVER_PRINT', json.dumps(result))

    def test_secret_conflict_preserves_existing_refs_and_reports_error(self):
        api = Api()
        api.run()
        api.secrets['svc-33']['metadata']['annotations'] = {}
        api.faults['PATCH', '/api/v1/namespaces/svc-33/secrets/iris-ecr-pull'] = 409
        self.assertFalse(api.run()['ok'])
        self.assertEqual(api.accounts['svc-33']['imagePullSecrets'], [{'name': 'other'}, {'name': generic.SECRET}])

    def test_wrong_secret_type_and_immutable_secret_rejected(self):
        for field, value in (('type', 'Opaque'), ('immutable', True)):
            api = Api()
            api.run()
            api.secrets['svc-33'][field] = value
            api.sessions.clear()
            self.assertFalse(api.run()['ok'])
            self.assertEqual(api.sessions, [])

    def test_wrong_issuer_and_bootstrap_identity_stop_before_workload_write(self):
        api = Api()
        with self.assertRaises(ValueError):
            api.run({'Arn': 'arn:aws:sts::187069338876:assumed-role/Administrator/job'})
        self.assertEqual(api.calls, [])
        api.bootstrap['data']['token'] = encode(token('system:serviceaccount:svc-28:iris-ecr-renewer'))
        with self.assertRaises(ValueError):
            api.run()
        self.assertEqual(api.calls, [])

    def test_bootstrap_conflict_stops_before_issuing_ecr_credentials(self):
        api = Api()
        api.bootstrap_conflict = True
        with self.assertRaises(httpx.HTTPStatusError):
            api.run()
        self.assertEqual(api.sessions, [])

    def test_pending_owned_pod_replaced_after_sa_connection_with_preconditions(self):
        api = Api()
        api.pods['svc-33'] = [failed_pod()]
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertEqual(result['services'][1]['replacedPods'], 1)
        self.assertEqual(api.pods['svc-33'], [])
        methods = [(r.method, r.url.path) for r in api.calls]
        self.assertLess(methods.index(('PATCH', '/api/v1/namespaces/svc-33/serviceaccounts/default')),
                        methods.index(('DELETE', '/api/v1/namespaces/svc-33/pods/app-pod')))

    def test_running_or_already_referenced_pods_are_not_replaced(self):
        for change in ('running', 'partly-running', 'referenced', 'foreign-image'):
            api = Api()
            pod = failed_pod()
            if change == 'running':
                pod['status']['phase'] = 'Running'
            elif change == 'partly-running':
                pod['status']['containerStatuses'].append({'state': {'running': {}}})
            elif change == 'referenced':
                pod['spec']['imagePullSecrets'] = [{'name': generic.SECRET}]
            else:
                pod['spec']['containers'][0]['image'] = generic.REGISTRY + '/iris/services/333:latest'
            api.pods['svc-33'] = [pod]
            self.assertTrue(api.run()['ok'])
            self.assertEqual(len(api.pods['svc-33']), 1)

    def test_unsupported_or_explicit_controller_refs_are_reported_without_delete(self):
        for change in ('custom-sa', 'standalone', 'explicit-refs', 'old-rs-refs', 'other-owner'):
            api = Api()
            pod = failed_pod()
            if change == 'custom-sa':
                pod['spec']['serviceAccountName'] = 'custom'
            elif change == 'standalone':
                pod['metadata']['ownerReferences'] = []
            elif change == 'explicit-refs':
                api.deployment['spec']['template']['spec']['imagePullSecrets'] = [{'name': 'other'}]
            elif change == 'old-rs-refs':
                api.rs['spec']['template']['spec']['imagePullSecrets'] = [{'name': 'other'}]
            else:
                api.rs['metadata']['ownerReferences'][0]['name'] = 'other-app'
            api.pods['svc-33'] = [pod]
            result = api.run()
            self.assertFalse(result['ok'])
            self.assertEqual(result['services'][1]['errorType'], 'UnsupportedWorkload')
            self.assertEqual(len(api.pods['svc-33']), 1)

    def test_owner_uid_mismatch_is_deferred(self):
        api = Api()
        api.pods['svc-33'] = [failed_pod()]
        api.rs['metadata']['uid'] = 'different-rs'
        self.assertEqual(api.run()['services'][1]['status'], 'deferred')
        self.assertEqual(len(api.pods['svc-33']), 1)

    def test_deleted_pod_race_is_deferred_without_failing_other_namespaces(self):
        api = Api()
        api.pods['svc-33'] = [failed_pod()]
        api.faults['DELETE', '/api/v1/namespaces/svc-33/pods/app-pod'] = 404
        result = api.run()
        self.assertTrue(result['ok'])
        self.assertEqual(result['services'][1]['status'], 'deferred')


if __name__ == '__main__':
    unittest.main()
