#!/usr/bin/env python3
"""Render GCP and verify cross-cloud ownership without credentials or a cluster."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
HELM = os.environ.get('HELM', 'helm')
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:value', lambda loader, node: loader.construct_scalar(node))


def render(chart, values, directory, namespace='iris-system', success=True, expected_error=()):
    path = directory/'values.json'
    path.write_text(json.dumps(values))
    command = [HELM, 'template', 'argocd' if chart == 'bootstrap' else 'check', str(ROOT/'helm'/chart), '-f', str(path), '--namespace', namespace, '--kube-version', '1.35.0']
    result = subprocess.run(command, capture_output=True, text=True)
    if not success:
        assert result.returncode, f'Invalid input unexpectedly rendered: {chart}'
        assert all(message in result.stderr for message in expected_error), result.stderr
        return
    assert result.returncode == 0, result.stderr
    subprocess.run([HELM, 'lint', '--strict', str(ROOT/'helm'/chart), '-f', str(path), '--namespace', namespace, '--kube-version', '1.35.0'], check=True, capture_output=True)
    return {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(result.stdout) if d}


def main():
    spec = importlib.util.spec_from_file_location('gcp_ops', ROOT/'scripts/gcp-ops.py')
    ops = importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
    target = json.loads((ROOT/'contracts/gcp-target.example.json').read_text())
    ops.validate(target, target['project_id'])
    schema = json.loads((ROOT/'contracts/gcp-target.schema.json').read_text())
    assert set(schema['required']) == set(target) == set(schema['properties'])
    account = '123456789012'
    credentials = {'enabled': True, 'image': f'{account}.dkr.ecr.ap-northeast-2.amazonaws.com/iris/gcp-ecr-credentials@sha256:'+('a'*64), 'serviceAccountEmail': target['ecr_service_account'], 'serviceAccountId': target['ecr_service_account_id'], 'awsAccountId': account, 'roleArn': f'arn:aws:iam::{account}:role/iris-dev-gcp-ecr-pull', 'audience': target['ecr_audience'], 'kubeApiCidr': '10.60.15.1/32'}
    g = {'enabled': True, 'endpoint': target['endpoint'], 'baseDomain': target['base_domain'], 'deniedCidrs': target['denied_cidrs'], 'addressName': target['address_name'], 'certificateMap': target['certificate_map'], 'chartRevision': 'iris-service-0.10.0', 'credentials': credentials}
    targets = {p: {'name': f'iris-dev-{p}', 'region': 'ap-northeast-2', 'vpc_id': 'vpc-0123456789abcdef0', 'endpoint': f'https://{p}.eks.amazonaws.com'} for p in ('management', 'workload')}
    with tempfile.TemporaryDirectory(prefix='iris-gcp-render-') as temp:
        directory = Path(temp)
        baseline = render('gitops', {'revision': 'main', 'targets': targets}, directory, 'argocd')
        enabled = render('gitops', {'revision': 'main', 'targets': targets, 'gcp': g}, directory, 'argocd')
        assert all(enabled[key] == value for key, value in baseline.items()), 'GCP activation must preserve every existing AWS/onprem document.'
        appset = enabled['ApplicationSet', 'iris-svc-gcp-appset']['spec']
        app = appset['template']['spec']
        assert appset['generators'][0]['git']['directories'] == [{'path': 'services/*/gcp'}]
        assert appset['syncPolicy']['applicationsSync'] == 'sync'
        assert appset['template']['metadata']['name'] == 'gcp-svc-{{ index .path.segments 1 }}'
        assert appset['template']['metadata']['finalizers'] == ['resources-finalizer.argocd.argoproj.io']
        assert app['sources'][0]['targetRevision'] == 'iris-service-0.10.0'
        assert 'RespectIgnoreDifferences=true' in app['syncPolicy']['syncOptions']
        assert app['ignoreDifferences'] == [{'group': '', 'kind': 'Secret', 'name': 'iris-ecr-pull', 'jsonPointers': ['/data/.dockerconfigjson', '/metadata/annotations/iris.dev~1expires-at']}]
        labels = app['syncPolicy']['managedNamespaceMetadata']['labels']
        assert labels['iris.dev/target'] == 'gcp-dev-workload' and labels['iris.dev/registry-pull'] == 'gcp'
        addon = enabled['Application', 'iris-gcp-workload']['spec']
        assert addon['syncPolicy']['managedNamespaceMetadata']['labels'] == {'iris.dev/registry-pull': 'gcp', 'pod-security.kubernetes.io/enforce': 'baseline'}
        values = addon['source']['helm']['valuesObject']
        workload = render('charts/gcp-workload', values, directory)
        assert not any(k[0] in ('Ingress', 'StorageClass', 'Secret') for k in workload), 'Bootstrap owns the helper pull Secret.'
        gateway = workload['Gateway', 'iris-gcp-apps']
        assert gateway['metadata']['annotations']['networking.gke.io/certmap'] == 'iris-gcp-apps'
        assert gateway['spec']['gatewayClassName'] == 'gke-l7-global-external-managed'
        assert gateway['spec']['addresses'] == [{'type': 'NamedAddress', 'value': 'iris-gcp-apps'}]
        assert gateway['spec']['listeners'][1]['allowedRoutes']['namespaces']['selector']['matchLabels'] == {'iris.dev/target': 'gcp-dev-workload'}
        redirect = workload['HTTPRoute', 'http-redirect']['spec']['rules'][0]['filters'][0]['requestRedirect']
        assert redirect == {'scheme': 'https', 'statusCode': 301}
        redirect_spec = workload['HTTPRoute', 'http-redirect']['spec']
        assert redirect_spec['parentRefs'] == [{'group': 'gateway.networking.k8s.io', 'kind': 'Gateway', 'name': 'iris-gcp-apps', 'sectionName': 'http'}]
        assert redirect_spec['rules'][0]['matches'] == [{'path': {'type': 'PathPrefix', 'value': '/'}}]
        role = workload['Role', 'iris-ecr-pull']['rules'][0]
        assert role == {'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': ['iris-ecr-pull'], 'verbs': ['get', 'patch']}
        assert workload['ClusterRole', 'iris-gcp-ecr-namespaces']['rules'][0]['resources'] == ['namespaces']
        np = workload['NetworkPolicy', 'ecr-credentials']['spec']
        assert np['ingress'] == [] and np['egress'][2]['to'][0]['ipBlock']['cidr'] == credentials['kubeApiCidr']
        default_workload = render('charts/gcp-workload', {'gateway': values['gateway']}, directory)
        assert not any(k[0] in ('Deployment', 'ServiceAccount', 'Secret') for k in default_workload)
        raw = (ROOT/'helm/charts/iris-service/ci/aws-values.yaml').read_text()
        service = json.loads('\n'.join(line for line in raw.splitlines() if not line.startswith('#')))
        legacy = render('charts/iris-service', service, directory, 'svc-12')
        assert ('Ingress', 'app') in legacy and not any(k[0] in ('HTTPRoute', 'HealthCheckPolicy', 'Role', 'Secret', 'ResourceQuota') for k in legacy)
        assert ('Rollout', 'app') in legacy and ('Deployment', 'app') not in legacy
        assert 'imagePullSecrets' not in legacy['Rollout', 'app']['spec']['template']['spec']
        for key, value in app['sources'][0]['helm']['valuesObject'].items():
            service[key] = {**service.get(key, {}), **value}
        service['iris']['targetName'] = 'gcp'
        service['route']['host'] = 'my-app.gcp.likelion.uk'
        service_docs = render('charts/iris-service', service, directory, 'svc-12')
        assert not any(k[0] == 'Ingress' for k in service_docs)
        assert ('Rollout', 'app') not in service_docs
        deployment = service_docs['Deployment', 'app']
        assert deployment['apiVersion'] == 'apps/v1'
        assert deployment['spec']['strategy'] == {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}}
        assert deployment['spec']['progressDeadlineSeconds'] == service['health']['timeoutSeconds']
        assert 'progressDeadlineAbort' not in deployment['spec']
        pod = deployment['spec']['template']['spec']
        assert pod['terminationGracePeriodSeconds'] == 45
        assert pod['containers'][0]['lifecycle']['preStop'] == {'sleep': {'seconds': 15}}
        hc = service_docs['HealthCheckPolicy', 'app']['spec']
        assert hc['targetRef'] == {'group': '', 'kind': 'Service', 'name': 'app'}
        assert hc['default']['config']['httpHealthCheck'] == {'portSpecification': 'USE_FIXED_PORT', 'port': 8080, 'host': service['route']['host'], 'requestPath': '/health'}
        assert service_docs['Deployment', 'app']['spec']['template']['spec']['imagePullSecrets'] == [{'name': 'iris-ecr-pull'}]
        for names in (['iris-ecr-pull'], ['other-registry', 'iris-ecr-pull'], ['other-registry']):
            mixed = copy.deepcopy(service)
            mixed['imagePullSecrets'] = [{'name': name} for name in names]
            docs = render('charts/iris-service', mixed, directory, 'svc-12')
            assert docs['Deployment', 'app']['spec']['template']['spec']['imagePullSecrets'] == [{'name': name} for name in dict.fromkeys([*names, 'iris-ecr-pull'])]
        pull = service_docs['Secret', 'iris-ecr-pull']
        assert json.loads(base64.b64decode(pull['data']['.dockerconfigjson'])) == {'auths': {}}
        assert pull['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '-1'
        assert service_docs['Role', 'iris-ecr-pull']['rules'][0] == role
        assert ('ResourceQuota', 'application-budget') in service_docs and ('LimitRange', 'application-defaults') in service_docs
        # Upstream project metadata and aliases must survive the GCP Deployment branch.
        project_fixture = yaml.safe_load((ROOT/'helm/charts/iris-service/ci/aliases-values.yaml').read_text())
        project_service = copy.deepcopy(service)
        for key in ('projectId', 'hostAliases', 'containerPort', 'service'):
            project_service[key] = copy.deepcopy(project_fixture[key])
        project_docs = render('charts/iris-service', project_service, directory, 'svc-12')
        project_deployment = project_docs['Deployment', 'app']['spec']
        assert project_deployment['template']['metadata']['labels']['iris.io/project-id'] == project_fixture['projectId']
        assert 'iris.io/project-id' not in project_deployment['selector']['matchLabels']
        assert project_docs['NetworkPolicy', 'allow-project-egress']['spec']['egress'] == [{'to': [{'namespaceSelector': {}, 'podSelector': {'matchLabels': {'iris.io/project-id': project_fixture['projectId']}}}]}]
        assert [port['port'] for port in project_docs['Service', 'app']['spec']['ports']] == [80, project_fixture['containerPort']]
        for alias in project_fixture['hostAliases']:
            assert project_docs['Service', alias['name']]['spec'] == {'type': 'ExternalName', 'externalName': alias['target']}
        # First prove the database fixture is valid, then reject the complete GCP variant
        # for its unsupported workload kind, rather than a missing-host template error.
        database = yaml.safe_load((ROOT/'helm/charts/iris-service/ci/database-values.yaml').read_text())
        database_docs = render('charts/iris-service', database, directory, 'svc-13')
        assert ('StatefulSet', 'app') in database_docs
        assert not any(kind in ('Deployment', 'Rollout', 'Ingress', 'HTTPRoute') for kind, _ in database_docs)
        for key in ('route', 'registryPull', 'tenantBudget'):
            database[key] = copy.deepcopy(service[key])
        render('charts/iris-service', database, directory, 'svc-13', success=False,
               expected_error=('schema', "'/workload/kind'", "value must be 'app'"))
        for name in ('new', 'rollback'):
            changed = copy.deepcopy(service)
            changed['release']['id'] = 346
            changed['variables']['name'] = 'vars-r346'
            if name == 'rollback':changed['release']['id'] = 345;changed['variables']['name'] = 'vars-r345'
            docs = render('charts/iris-service', changed, directory, 'svc-12')
            assert docs['Secret', 'iris-ecr-pull']['metadata']['name'] == pull['metadata']['name']
        for change in ({'route': {'host': 'bad.other.test'}}, {'route': {'host': 'nested.app.gcp.likelion.uk'}}, {'variables': {'name': 'iris-ecr-pull'}}):
            bad = copy.deepcopy(service)
            for key, value in change.items():bad[key].update(value)
            render('charts/iris-service', bad, directory, 'svc-12', success=False)
        for strategy in ('CANARY', 'BLUE_GREEN'):
            bad = copy.deepcopy(service)
            bad['deploymentStrategy'] = strategy
            render('charts/iris-service', bad, directory, 'svc-12', success=False)
        bad = copy.deepcopy(values);bad['credentials']['image'] = 'fixture:latest'
        render('charts/gcp-workload', bad, directory, success=False)
        bootstrap = render('bootstrap', ops.argo_overlay(target), directory, 'argocd')
        for kind, name in [('StatefulSet', 'argocd-application-controller'), ('Deployment', 'argocd-server')]:
            pod = bootstrap[kind, name]['spec']['template']['spec']
            assert {v['name'] for v in pod['volumes']} >= {'gcp-identity', 'gcp-config'}
            container = next(c for c in pod['containers'] if c['name'] in ('application-controller', 'server'))
            assert {'name': 'GOOGLE_APPLICATION_CREDENTIALS', 'value': '/etc/gcp-identity/credentials.json'} in container['env']
            assert {v['name'] for v in container['volumeMounts']} >= {'gcp-identity', 'gcp-config'}
    print('GCP Gateway, pull RBAC, namespace isolation, Argo WIF and legacy render preservation: OK')


if __name__ == '__main__':
    main()
