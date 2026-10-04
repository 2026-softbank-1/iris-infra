"""Reconcile per-service pull credentials on the legacy on-prem cluster.

AWS credentials stay in management EKS. Only repository-scoped ECR passwords
are sent to the workload cluster. No HTTP response bodies enter job results.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from refresh import SERVER, checked, decode, encode, subject, timestamp

ACCOUNT = '187069338876'
REGION = 'ap-northeast-2'
REGISTRY = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com'
ISSUER = f'arn:aws:sts::{ACCOUNT}:assumed-role/iris-dev-onprem-ecr-renewer/'
PULL_ROLE = f'arn:aws:iam::{ACCOUNT}:role/iris-dev-onprem-ecr-renewal-pull'
SUBJECT = 'system:serviceaccount:iris-system:iris-ecr-renewer'
SECRET = 'iris-ecr-pull'
NAMESPACE = re.compile(r'svc-([1-9][0-9]*)\Z')
MARGIN = timedelta(minutes=15)


class Deferred(Exception):
    """Namespace/SA provisioning or deletion is still in progress."""


class UnsupportedWorkload(ValueError):
    """A failed workload needs an operator to connect its pull credentials."""


def utc(value):
    return timestamp(value) if isinstance(value, str) else value.astimezone(timezone.utc)


def pull_policy(service_id):
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['ecr:GetAuthorizationToken'], 'Resource': '*'},
        {'Effect': 'Allow', 'Action': ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
                                     'ecr:BatchCheckLayerAvailability'],
         'Resource': f'arn:aws:ecr:{REGION}:{ACCOUNT}:repository/iris/services/{service_id}'},
    ]}


def issue(service_id, sts, ecr_factory):
    credentials = sts.assume_role(RoleArn=PULL_ROLE, RoleSessionName=f'iris-onprem-svc-{service_id}'[:64],
                                  DurationSeconds=3600, Policy=json.dumps(pull_policy(service_id)))['Credentials']
    auth = ecr_factory(credentials).get_authorization_token(registryIds=[ACCOUNT])['authorizationData'][0]
    if auth['proxyEndpoint'] != 'https://' + REGISTRY or not auth.get('authorizationToken'):
        raise ValueError('Unexpected registry response')
    # Role chaining can expire before the nominal ECR expiration. Be conservative.
    return auth['authorizationToken'], utc(auth['expiresAt']), utc(credentials['Expiration'])


class Reconciler:
    def __init__(self, remote, token, sts, ecr_factory, now):
        self.remote, self.token = remote, token
        self.sts, self.ecr_factory, self.now = sts, ecr_factory, now

    def request(self, method, path, *, optional=False, **kwargs):
        request = self.remote.build_request(method, SERVER + path,
            headers={'Authorization': 'Bearer ' + self.token},
            extensions={'sni_hostname': 'kubernetes.default.svc'}, **kwargs)
        response = self.remote.send(request)
        if optional and response.status_code == 404:
            return None
        return checked(response)

    def patch(self, path, value):
        request = self.remote.build_request('PATCH', SERVER + path,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/merge-patch+json'},
            extensions={'sni_hostname': 'kubernetes.default.svc'}, json=value)
        return checked(self.remote.send(request))

    def pages(self, path):
        continuation = ''
        seen = set()
        while True:
            value = self.request('GET', path, params={'limit': 200, 'continue': continuation})
            yield from value['items']
            continuation = value.get('metadata', {}).get('continue', '')
            if not continuation:
                return
            if continuation in seen:
                raise ValueError('Repeated pagination cursor')
            seen.add(continuation)

    def alive(self, namespace):
        current = self.request('GET', '/api/v1/namespaces/' + namespace['metadata']['name'], optional=True)
        if (not current or current['metadata']['uid'] != namespace['metadata']['uid']
                or current['metadata'].get('deletionTimestamp')):
            raise Deferred()

    def secret(self, namespace, service_id):
        name = namespace['metadata']['name']
        path = f'/api/v1/namespaces/{name}/secrets/{SECRET}'
        current = self.request('GET', path, optional=True)
        docker = {'auths': {}}
        if current:
            if current['type'] != 'kubernetes.io/dockerconfigjson' or current.get('immutable'):
                raise ValueError('Pull Secret type or mutability differs')
            docker = json.loads(decode(current['data']['.dockerconfigjson']))
            if not isinstance(docker.get('auths'), dict):
                raise ValueError('Invalid Docker configuration')
            annotations = current['metadata'].get('annotations', {})
            if (annotations.get('iris.dev/ecr-pull-role') == PULL_ROLE
                    and annotations.get('iris.dev/ecr-service-id') == service_id):
                try:
                    expiry = min(timestamp(annotations['iris.dev/ecr-token-expires-at']),
                                 timestamp(annotations['iris.dev/sts-token-expires-at']))
                    credential = docker['auths'].get(REGISTRY, {}).get('auth')
                    if credential and expiry > self.now + MARGIN:
                        self.alive(namespace)
                        return False
                except (KeyError, ValueError, TypeError):
                    pass
        auth, ecr_expiry, sts_expiry = issue(service_id, self.sts, self.ecr_factory)
        if min(ecr_expiry, sts_expiry) <= self.now + MARGIN:
            raise ValueError('Issued image credential has insufficient lifetime')
        docker['auths'][REGISTRY] = {'auth': auth}
        metadata = {'name': SECRET, 'namespace': name, 'annotations': {
            'iris.dev/ecr-token-expires-at': ecr_expiry.isoformat(),
            'iris.dev/sts-token-expires-at': sts_expiry.isoformat(),
            'iris.dev/ecr-pull-role': PULL_ROLE, 'iris.dev/ecr-service-id': service_id,
            'iris.dev/last-token-renewal': self.now.isoformat(),
            'kubectl.kubernetes.io/last-applied-configuration': None,
        }}
        data = {'.dockerconfigjson': encode(json.dumps(docker))}
        self.alive(namespace)
        if current:
            metadata['resourceVersion'] = current['metadata']['resourceVersion']
            self.patch(path, {'metadata': metadata, 'data': data})
        else:
            metadata['annotations'].pop('kubectl.kubernetes.io/last-applied-configuration')
            try:
                self.request('POST', f'/api/v1/namespaces/{name}/secrets', json={
                    'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata,
                    'type': 'kubernetes.io/dockerconfigjson', 'data': data})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
                # A concurrent creator wins; reconcile its version on the next run.
                self.request('GET', path)
                raise Deferred() from None
        return True

    def account(self, namespace):
        path = f'/api/v1/namespaces/{namespace["metadata"]["name"]}/serviceaccounts/default'
        current = self.request('GET', path, optional=True)
        if not current or current['metadata'].get('deletionTimestamp'):
            raise Deferred()
        references = current.get('imagePullSecrets', [])
        if any(item.get('name') == SECRET for item in references):
            return
        self.alive(namespace)
        self.patch(path, {'metadata': {'resourceVersion': current['metadata']['resourceVersion']},
                          'imagePullSecrets': [*references, {'name': SECRET}]})

    def recover(self, namespace, service_id):
        name = namespace['metadata']['name']
        image = f'{REGISTRY}/iris/services/{service_id}'
        deleted, unsupported = 0, False
        for pod in self.pages(f'/api/v1/namespaces/{name}/pods'):
            spec, meta, status = pod['spec'], pod['metadata'], pod.get('status', {})
            if status.get('phase') != 'Pending' or meta.get('deletionTimestamp'):
                continue
            containers = status.get('containerStatuses', []) + status.get('initContainerStatuses', [])
            if any('running' in item.get('state', {}) for item in containers):
                continue
            failed = any(item.get('state', {}).get('waiting', {}).get('reason')
                         in ('ImagePullBackOff', 'ErrImagePull')
                         for item in containers)
            if not failed:
                continue
            images = [container['image'] for container in spec.get('containers', [])]
            if not any(value.startswith(image + '@sha256:') or value.startswith(image + ':') for value in images):
                continue
            if any(ref.get('name') == SECRET for ref in spec.get('imagePullSecrets', [])):
                continue  # kubelet retries using the updated Secret without replacement
            if spec.get('serviceAccountName', 'default') != 'default':
                unsupported = True
                continue
            owner = next((ref for ref in meta.get('ownerReferences', []) if ref.get('controller')), None)
            if not owner or owner['kind'] != 'ReplicaSet' or owner.get('apiVersion') != 'apps/v1':
                unsupported = True
                continue
            rs = self.request('GET', f'/apis/apps/v1/namespaces/{name}/replicasets/{owner["name"]}', optional=True)
            if not rs or rs['metadata']['uid'] != owner['uid']:
                raise Deferred()
            deployment_owner = next((ref for ref in rs['metadata'].get('ownerReferences', [])
                                     if ref.get('controller')), None)
            if (not deployment_owner or deployment_owner.get('kind') != 'Deployment'
                    or deployment_owner.get('apiVersion') != 'apps/v1' or deployment_owner.get('name') != 'app'):
                unsupported = True
                continue
            deployment = self.request('GET', f'/apis/apps/v1/namespaces/{name}/deployments/app', optional=True)
            if not deployment or deployment['metadata']['uid'] != deployment_owner['uid']:
                raise Deferred()
            template = deployment['spec']['template']['spec']
            replica_template = rs['spec']['template']['spec']
            if any(t.get('imagePullSecrets') or t.get('serviceAccountName', 'default') != 'default'
                   for t in (template, replica_template)):
                unsupported = True
                continue
            if deployment['metadata'].get('deletionTimestamp') or rs['metadata'].get('deletionTimestamp'):
                raise Deferred()
            self.alive(namespace)
            self.request('DELETE', f'/api/v1/namespaces/{name}/pods/{meta["name"]}', json={
                'apiVersion': 'v1', 'kind': 'DeleteOptions',
                'preconditions': {'uid': meta['uid'], 'resourceVersion': meta['resourceVersion']}})
            deleted += 1
        if unsupported:
            raise UnsupportedWorkload('Failed workload requires an explicit pull Secret reference')
        return deleted


def run_all(namespace, secret_name, local, remote, *, identity, sts, ecr_factory, now=None):
    now = now or datetime.now(timezone.utc)
    if not identity.get('Arn', '').startswith(ISSUER):
        raise ValueError('Unexpected AWS issuer role')
    path = f'/api/v1/namespaces/{namespace}/secrets/{secret_name}'
    bootstrap = checked(local.get(path))
    old_token = decode(bootstrap['data']['token'])
    if decode(bootstrap['data']['server']) != SERVER or subject(old_token) != SUBJECT:
        raise ValueError('Unexpected on-prem identity')
    worker = Reconciler(remote, old_token, sts, ecr_factory, now)
    fresh = worker.request('POST', '/api/v1/namespaces/iris-system/serviceaccounts/iris-ecr-renewer/token',
        json={'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
              'spec': {'expirationSeconds': 86400}})['status']
    if subject(fresh['token']) != SUBJECT or timestamp(fresh['expirationTimestamp']) < now + timedelta(hours=3):
        raise ValueError('Unexpected issued on-prem token')
    checked(local.patch(path, headers={'Content-Type': 'application/merge-patch+json'}, json={
        'metadata': {'resourceVersion': bootstrap['metadata']['resourceVersion'], 'annotations': {
            'iris.dev/api-token-expires-at': fresh['expirationTimestamp'],
            'iris.dev/last-token-renewal': now.isoformat(),
            'kubectl.kubernetes.io/last-applied-configuration': None}},
        'data': {'token': encode(fresh['token']), 'expiresAt': encode(fresh['expirationTimestamp'])}}))
    worker.token = fresh['token']
    result = {'mode': 'ecr-all', 'ok': True, 'apiTokenExpiresAt': fresh['expirationTimestamp'], 'services': []}
    for ns in worker.pages('/api/v1/namespaces'):
        name = ns['metadata']['name']
        match = NAMESPACE.fullmatch(name)
        if not match or ns['metadata'].get('deletionTimestamp'):
            continue
        record = {'namespace': name}
        try:
            changed = worker.secret(ns, match[1])
            worker.account(ns)
            record.update(status='ready', refreshed=changed, replacedPods=worker.recover(ns, match[1]))
        except Deferred:
            record['status'] = 'deferred'
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                record['status'] = 'deferred'
                result['services'].append(record)
                continue
            result['ok'] = False
            record.update(status='error', errorType=type(exc).__name__)
            if isinstance(exc, httpx.HTTPStatusError):
                record['statusCode'] = exc.response.status_code
        result['services'].append(record)
    return result
