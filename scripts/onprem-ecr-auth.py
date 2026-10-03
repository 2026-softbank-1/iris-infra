#!/usr/bin/env python3
"""Refresh a pre-provisioned, service-scoped ECR pull Secret over SSH.

Defaults to Kubernetes server dry-run. No IAM roles or access keys are created.
Credentials are passed through process memory/stdin and are never printed.
"""
import argparse
import base64
import json
import shlex
import subprocess
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-id', required=True, type=int)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--region', default='ap-northeast-2')
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-user', required=True)
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--ssh-key', required=True)
    parser.add_argument('--vm-host')
    parser.add_argument('--vm-user')
    parser.add_argument('--vm-key', help='Path to the VM SSH key on the outer host')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.service_id < 1 or not args.account_id.isdigit() or len(args.account_id) != 12:
        parser.error('A positive service ID and explicit 12-digit AWS account are required.')
    if args.vm_host and not (args.vm_user and args.vm_key):
        parser.error('--vm-host requires --vm-user and --vm-key.')

    aws = ['aws', '--profile', args.profile, '--region', args.region, '--output', 'json']

    def call_aws(*parameters):
        return json.loads(subprocess.check_output(aws + list(parameters), text=True))

    identity = call_aws('sts', 'get-caller-identity')
    if identity['Account'] != args.account_id:
        raise SystemExit('AWS account mismatch; no credentials issued or remote changes made.')
    sid = args.service_id
    role_name = f'iris-dev-onprem-ecr-svc-{sid}'
    role_arn = f'arn:aws:iam::{args.account_id}:role/{role_name}'
    repository = f'iris/services/{sid}'
    repository_arn = f'arn:aws:ecr:{args.region}:{args.account_id}:repository/{repository}'
    expected = {
        'Version': '2012-10-17',
        'Statement': [
            {'Effect': 'Allow', 'Action': ['ecr:GetAuthorizationToken'], 'Resource': '*'},
            {'Effect': 'Allow', 'Action': ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer',
                                         'ecr:BatchCheckLayerAvailability'], 'Resource': repository_arn},
        ],
    }
    actual = call_aws('iam', 'get-role-policy', '--role-name', role_name,
                      '--policy-name', 'PullServiceImage')['PolicyDocument']
    if actual != expected:
        raise SystemExit('Pull role policy differs from the service-scoped contract; no remote change made.')

    credentials = call_aws('sts', 'assume-role', '--role-arn', role_arn,
                           '--role-session-name', f'iris-onprem-{sid}',
                           '--duration-seconds', '3600')['Credentials']
    # The AWS CLI needs the temporary credentials, but the on-prem server receives only its ECR token.
    import os
    env = os.environ.copy()
    env.update(AWS_ACCESS_KEY_ID=credentials['AccessKeyId'],
               AWS_SECRET_ACCESS_KEY=credentials['SecretAccessKey'],
               AWS_SESSION_TOKEN=credentials['SessionToken'])
    env.pop('AWS_PROFILE', None)
    authorization = json.loads(subprocess.check_output(
        ['aws', 'ecr', 'get-authorization-token', '--region', args.region, '--output', 'json'],
        env=env, text=True))['authorizationData'][0]
    registry = authorization['proxyEndpoint'].removeprefix('https://')
    docker_config = {'auths': {registry: {'auth': authorization['authorizationToken']}}}
    namespace = f'svc-{sid}'
    secret = {
        'apiVersion': 'v1', 'kind': 'Secret',
        'metadata': {'name': 'iris-ecr-pull', 'namespace': namespace},
        'type': 'kubernetes.io/dockerconfigjson',
        'data': {'.dockerconfigjson': base64.b64encode(json.dumps(docker_config).encode()).decode()},
    }
    outer = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', args.ssh_key,
             '-p', str(args.ssh_port), f'{args.ssh_user}@{args.ssh_host}']

    def remote(parameters, body=None):
        command = ['sudo', 'k3s', 'kubectl', '-n', namespace] + parameters
        if args.vm_host:
            inner = ['ssh', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-i', args.vm_key,
                     f'{args.vm_user}@{args.vm_host}']
            shell = shlex.join(inner) + ' ' + shlex.quote(shlex.join(command))
        else:
            shell = shlex.join(command)
        return subprocess.check_output(outer + [shell], input=body, text=True)

    account = json.loads(remote(['get', 'serviceaccount', 'default', '-o', 'json']))
    references = account.get('imagePullSecrets', [])
    if not any(item['name'] == 'iris-ecr-pull' for item in references):
        references.append({'name': 'iris-ecr-pull'})
    dry_run = [] if args.apply else ['--dry-run=server']
    print(remote(['apply', '-f', '-'] + dry_run, json.dumps(secret)), end='')
    print(remote(['patch', 'serviceaccount', 'default', '--type=merge', '--patch-file=/dev/stdin']
                 + dry_run, json.dumps({'imagePullSecrets': references})), end='')
    expiry = authorization['expiresAt']
    if isinstance(expiry, (float, int)):
        expiry = datetime.fromtimestamp(expiry).astimezone().isoformat()
    print(json.dumps({'applied': args.apply, 'namespace': namespace, 'roleArn': role_arn,
                      'secret': 'iris-ecr-pull', 'ecrTokenExpiresAt': expiry}))


if __name__ == '__main__':
    main()
