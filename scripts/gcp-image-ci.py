#!/usr/bin/env python3
"""Publish one immutable GCP credential helper image; never write GitOps."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal

SPEC = importlib.util.spec_from_file_location('gcp_ci', Path(__file__).with_name('gcp-ci.py'))
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)
REPO = 'iris/gcp-ecr-credentials'


def config():
    account = os.environ.get('AWS_ACCOUNT_ID', '')
    CI.need(re.fullmatch(r'[0-9]{12}', account) and account != '000000000000', 'Invalid AWS account.')
    CI.need(os.environ.get('AWS_REGION') == 'ap-northeast-2', 'Unexpected AWS region.')
    role = f'arn:aws:iam::{account}:role/iris-dev-gcp-credentials-publisher'
    CI.need(os.environ.get('GCP_ECR_PUBLISH_ROLE_ARN') == role, 'Unexpected publisher role.')
    repository = f'{account}.dkr.ecr.ap-northeast-2.amazonaws.com/{REPO}'
    CI.need(os.environ.get('GCP_ECR_REPOSITORY') == repository, 'Unexpected image repository.')
    return account, role, repository


def publish(runner=CI.RUN):
    account, _, repository = config()
    identity = json.loads(runner.run(['aws', 'sts', 'get-caller-identity', '--output', 'json']))
    CI.need(identity.get('Account') == account and re.fullmatch(f'arn:aws:sts::{account}:assumed-role/iris-dev-gcp-credentials-publisher/[A-Za-z0-9+=,.@_-]+', identity.get('Arn', '')), 'Unexpected AWS identity.')
    aws = ['aws', 'ecr']
    options = ['--registry-id', account, '--region', 'ap-northeast-2', '--output', 'json']
    metadata = json.loads(runner.run(aws+['describe-repositories', '--repository-names', REPO, *options]))['repositories']
    CI.need(len(metadata) == 1 and metadata[0].get('repositoryUri') == repository and metadata[0].get('imageTagMutability') == 'IMMUTABLE' and metadata[0].get('registryId') == account, 'Repository mismatch or mutable tags.')
    head = CI.sha(os.environ.get('GITHUB_SHA'))
    tag = 'sha-'+head
    images = json.loads(runner.run(aws+['list-images', '--repository-name', REPO, '--filter', 'tagStatus=TAGGED', *options]))['imageIds']
    found = [i for i in images if i.get('imageTag') == tag]
    CI.need(len(found) <= 1, 'Ambiguous source tag.')
    password = runner.run(aws+['get-login-password', '--region', 'ap-northeast-2']).strip()
    CI.need(password and '\n' not in password, 'Invalid registry login.')
    runner.run(['docker', 'login', '--username', 'AWS', '--password-stdin', repository.split('/')[0]], stdin=password)
    try:
        if found:
            digest = found[0].get('imageDigest', '')
            CI.need(re.fullmatch(r'sha256:[a-f0-9]{64}', digest), 'Invalid existing digest.')
            runner.run(['docker', 'pull', '--platform', 'linux/amd64', repository+'@'+digest])
            revision = runner.run(['docker', 'image', 'inspect', '--format', '{{ index .Config.Labels "org.opencontainers.image.revision" }}', repository+'@'+digest]).strip()
            CI.need(revision == head, 'Existing immutable tag revision mismatch; never overwrite it.')
            CI.summary('Reused immutable source SHA image after revision verification.')
        else:
            target = repository+':'+tag
            runner.run(['docker', 'build', '--platform', 'linux/amd64', '--label', 'org.opencontainers.image.revision='+head, '--tag', target, str(CI.ROOT/'runtime/gcp-ecr-credentials')])
            runner.run(['docker', 'push', target])
            result = json.loads(runner.run(aws+['describe-images', '--repository-name', REPO, '--image-ids', 'imageTag='+tag, *options]))['imageDetails']
            CI.need(len(result) == 1 and tag in result[0].get('imageTags', []), 'Published image tag mismatch.')
            digest = result[0].get('imageDigest', '')
            CI.need(re.fullmatch(r'sha256:[a-f0-9]{64}', digest), 'Invalid published digest.')
        image = repository+'@'+digest
        CI.output({'image': image})
        CI.summary('Credential helper image: `'+image+'`\nUse this digest with make gcp-bootstrap GCP_ECR_IMAGE=... . Chart publication and GitOps registration remain explicit operator steps.')
    finally:
        runner.run(['docker', 'logout', repository.split('/')[0]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('classify', 'prepare', 'publish'))
    args = parser.parse_args()
    if args.command == 'classify':
        CI.classify('image')
    else:
        CI.gate('image', 'publish')
        config()
        if args.command == 'publish':
            publish()


if __name__ == '__main__':
    for event in (signal.SIGTERM, signal.SIGINT):
        signal.signal(event, CI.cancelled)
    try:
        main()
    except CI.CIFailure as error:
        raise SystemExit('GCP image CI stopped: '+str(error)) from None
    except (ValueError, KeyError, OSError, json.JSONDecodeError):
        raise SystemExit('GCP image CI stopped: gate, identity, immutable repository or command verification failed. Raw output suppressed.') from None
