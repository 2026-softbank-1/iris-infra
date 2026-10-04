"""Renew scoped on-prem API credentials and ECR image authentication.

Only the configured Secret is patched. No credential values are logged.
"""
import base64
import json
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

SERVER = 'https://iris-onprem-api.argocd.svc.cluster.local:6443'


def decode(value):
    return base64.b64decode(value).decode()


def encode(value):
    return base64.b64encode(value.encode()).decode()


def subject(token):
    part = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))['sub']


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def checked(response):
    response.raise_for_status()
    return response.json()


def run(mode, namespace, secret_name, local, remote, *, identity=None, ecr=None, now=None):
    now = now or datetime.now(timezone.utc)
    path = f'/api/v1/namespaces/{namespace}/secrets/{secret_name}'
    secret = checked(local.get(path))
    data = secret['data']
    if mode == 'ecr':
        target_namespace, account = 'svc-28', 'iris-ecr-renewer'
        server, old_token = decode(data['server']), decode(data['token'])
        expected_role = 'arn:aws:sts::187069338876:assumed-role/iris-dev-onprem-ecr-svc-28/'
        if not identity or not identity.get('Arn', '').startswith(expected_role):
            raise ValueError('Unexpected AWS role; image credentials were not issued.')
        duration, minimum = 86400, timedelta(hours=3)
    elif mode == 'argo':
        target_namespace, account = 'iris-onprem-test', 'iris-argocd'
        server = decode(data['server'])
        config = json.loads(decode(data['config']))
        old_token = config['bearerToken']
        duration, minimum = 172800, timedelta(hours=12)
    else:
        raise ValueError('Unknown renewal mode.')
    expected_subject = f'system:serviceaccount:{target_namespace}:{account}'
    if server != SERVER or subject(old_token) != expected_subject:
        raise ValueError('Unexpected on-prem server or ServiceAccount; no Secret patch made.')

    def onprem(method, url, token, **kwargs):
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = 'Bearer ' + token
        request = remote.build_request(method, SERVER + url,
                                       headers=headers,
                                       extensions={'sni_hostname': 'kubernetes.default.svc'}, **kwargs)
        return checked(remote.send(request))

    fresh = onprem('POST', f'/api/v1/namespaces/{target_namespace}/serviceaccounts/{account}/token',
                   old_token, json={'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
                                    'spec': {'expirationSeconds': duration}})['status']
    new_token, api_expiry = fresh['token'], fresh['expirationTimestamp']
    if subject(new_token) != expected_subject or timestamp(api_expiry) < now + minimum:
        raise ValueError('Unexpected issued token identity or lifetime; no Secret patch made.')
    annotations = {'iris.dev/last-token-renewal': now.isoformat(),
                   'iris.dev/api-token-expires-at': api_expiry,
                   'kubectl.kubernetes.io/last-applied-configuration': None}
    if mode == 'ecr':
        update = {'token': encode(new_token), 'expiresAt': encode(api_expiry)}
    else:
        config['bearerToken'] = new_token
        update = {'config': encode(json.dumps(config))}
    checked(local.patch(path, headers={'Content-Type': 'application/merge-patch+json'}, json={
        'metadata': {'resourceVersion': secret['metadata']['resourceVersion'],
                     'annotations': annotations}, 'data': update}))
    result = {'mode': mode, 'apiTokenExpiresAt': api_expiry, 'renewedAt': now.isoformat()}
    if mode == 'ecr':
        auth = ecr.get_authorization_token(registryIds=['187069338876'])['authorizationData'][0]
        registry = '187069338876.dkr.ecr.ap-northeast-2.amazonaws.com'
        if auth['proxyEndpoint'] != 'https://' + registry:
            raise ValueError('Unexpected registry; no image Secret patch made.')
        docker_path = '/api/v1/namespaces/svc-28/secrets/iris-ecr-pull'
        docker_secret = onprem('GET', docker_path, new_token)
        if docker_secret['type'] != 'kubernetes.io/dockerconfigjson':
            raise ValueError('Unexpected image Secret type.')
        docker = json.loads(decode(docker_secret['data']['.dockerconfigjson']))
        docker.setdefault('auths', {})[registry] = {'auth': auth['authorizationToken']}
        expiry = auth['expiresAt'].isoformat()
        onprem('PATCH', docker_path, new_token,
               headers={'Content-Type': 'application/merge-patch+json'},
               json={'metadata': {'resourceVersion': docker_secret['metadata']['resourceVersion'],
                                  'annotations': {'iris.dev/ecr-token-expires-at': expiry,
                                                  'iris.dev/last-token-renewal': now.isoformat(),
                                                  'kubectl.kubernetes.io/last-applied-configuration': None}},
                     'data': {'.dockerconfigjson': encode(json.dumps(docker))}})
        result['ecrTokenExpiresAt'] = expiry
    return result


def main():
    mode = os.environ['MODE']
    namespace, secret = os.environ['POD_NAMESPACE'], os.environ['AUTH_SECRET']
    local_token = Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
    local_ca = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
    remote_ca = ssl.create_default_context(cafile='/code/server-ca.crt')
    identity = ecr = None
    if mode in ('ecr', 'ecr-all'):
        import boto3
        identity = boto3.client('sts', region_name='ap-northeast-2').get_caller_identity()
        ecr = boto3.client('ecr', region_name='ap-northeast-2')
    with httpx.Client(base_url='https://kubernetes.default.svc', verify=local_ca, timeout=30,
                      headers={'Authorization': 'Bearer ' + local_token}) as local:
        with httpx.Client(verify=remote_ca, timeout=30) as remote:
            if mode == 'ecr-all':
                from all_services import run_all

                def ecr_factory(credentials):
                    return boto3.client('ecr', region_name='ap-northeast-2',
                        aws_access_key_id=credentials['AccessKeyId'],
                        aws_secret_access_key=credentials['SecretAccessKey'],
                        aws_session_token=credentials['SessionToken'])

                result = run_all(namespace, secret, local, remote, identity=identity,
                                 sts=boto3.client('sts', region_name='ap-northeast-2'), ecr_factory=ecr_factory)
            else:
                result = run(mode, namespace, secret, local, remote, identity=identity, ecr=ecr)
            print(json.dumps(result))
            if not result.get('ok', True):
                raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HTTP errors may contain confidential response bodies. Print only type and status.
        error = {'ok': False, 'errorType': type(exc).__name__}
        if isinstance(exc, httpx.HTTPStatusError):
            error['statusCode'] = exc.response.status_code
        if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
            error['endpoint'] = str(exc.request.url.copy_with(query=None))
        print(json.dumps(error))
        raise SystemExit(1)
