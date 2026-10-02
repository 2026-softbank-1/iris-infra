#!/usr/bin/env python3
"""Render the pinned stack and check operational contracts without a cluster."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Prometheus CRDs contain the literal scalar '=' in OpenAPI enum lists.
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:value', lambda loader, node: loader.construct_scalar(node))
VERSIONS = json.loads((ROOT / 'helm/versions.json').read_text())
LOCK = json.loads((ROOT / 'helm/images.lock.json').read_text())
HELM = os.environ.get('HELM', 'helm')


def command(*args):
    return subprocess.check_output([HELM, *map(str, args)], text=True)


def render(chart, values=None, release='check', namespace='kube-system', parameters=()):
    args = ['--kube-version', VERSIONS['kubernetes']+'.0', '--namespace', namespace]
    if values: args += ['-f', values]
    args += list(parameters)
    command('lint', '--strict', chart, *args)
    docs = [x for x in yaml.safe_load_all(command('template', release, chart, '--include-crds', *args)) if x]
    # Some charts (LBC) wrap resources in a v1 List; Argo applies the items.
    docs = [item for x in docs for item in (x.get('items') or [] if x['kind']=='List' else [x])]
    assert docs, f'Empty chart: {chart}'
    return docs


def images(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'image' and isinstance(child, str): yield child
            yield from images(child)
    elif isinstance(value, list):
        for child in value: yield from images(child)


def check_images(docs):
    for ref in images(docs):
        assert re.fullmatch(r'.+@sha256:[a-f0-9]{64}', ref), f'Unpinned image: {ref}'
        name, digest = ref.rsplit('@', 1)
        assert LOCK['images'].get(name) == digest, f'Image differs from lock: {name}'


# Cluster-scoped kinds Argo must be allowed to create through the addon AppProjects.
CLUSTER_SCOPED = {'Namespace','StorageClass','ClusterRole','ClusterRoleBinding','MutatingWebhookConfiguration','ValidatingWebhookConfiguration','CustomResourceDefinition','APIService','IngressClass','IngressClassParams','PriorityClass','PersistentVolume','CSIDriver','RuntimeClass'}


def group_kind(doc):
    return (doc['apiVersion'].rpartition('/')[0], doc['kind'])


def main():
    assert command('version', '--short').startswith('v'+VERSIONS['helm']+'+'), 'Use pinned Helm version.'
    command('repo', 'add', 'iris-argocd', VERSIONS['charts']['argo-cd']['repo'])
    command('dependency', 'build', ROOT/'helm/bootstrap')
    bootstrap = render(ROOT/'helm/bootstrap', release='argocd', namespace='argocd')
    check_images(bootstrap)
    accounts = {d['metadata']['name'] for d in bootstrap if d['kind']=='ServiceAccount'}
    assert {'argocd-server','argocd-application-controller','argocd-applicationset-controller'} <= accounts
    assert not any(d['kind']=='Ingress' for d in bootstrap)
    with tempfile.TemporaryDirectory(prefix='iris-helm-') as temporary:
        directory = Path(temporary)
        targets = {p: {'name':f'iris-dev-{p}', 'region':'ap-northeast-2','vpc_id':'vpc-0123456789abcdef0','endpoint':f'https://{p}.eks.amazonaws.com'} for p in ('management','workload')}
        values = directory/'gitops.json'
        values.write_text(json.dumps({'revision':'a'*40,'targets':targets}))
        gitops = render(ROOT/'helm/gitops', values, namespace='argocd')
        assert sum(d['kind']=='Application' for d in gitops)==9
        assert sum(d['kind']=='AppProject' for d in gitops)==4
        platform_app = next(d for d in gitops if d['kind']=='Application' and d['metadata']['name']=='iris-platform')['spec']
        assert platform_app['destination']=={'server':targets['management']['endpoint'],'namespace':'iris-platform'} and platform_app['sources'][0]['helm']['ignoreMissingValueFiles']
        assert platform_app['sources'][0]['helm']['valueFiles'][-1]=='$gitops/platform/aws-dev-management/values.yaml' and platform_app['sources'][1]['ref']=='gitops'
        platform_project = next(d for d in gitops if d['kind']=='AppProject' and d['metadata']['name']=='iris-platform-project')['spec']
        digest = 'sha256:'+'a'*64
        digests = directory/'platform-digests.json'
        digests.write_text(json.dumps({c:{'digest':digest} for c in ('api','buildWorker','deployWorker')}))
        platform_values = ROOT/'clusters/aws-dev-management/values/platform.yaml'
        empty = [x for x in yaml.safe_load_all(command('template','p',ROOT/'helm/charts/iris-platform','-f',platform_values,'--kube-version',VERSIONS['kubernetes']+'.0')) if x]
        assert not any(d['kind'] in ('Deployment','Job','Ingress') for d in empty), 'Without digests no component may be deployed.'
        command('lint','--strict',ROOT/'helm/charts/iris-platform','-f',platform_values,'-f',digests,'--kube-version',VERSIONS['kubernetes']+'.0')
        platform = [x for x in yaml.safe_load_all(command('template','p',ROOT/'helm/charts/iris-platform','-f',platform_values,'-f',digests,'--namespace','iris-platform','--kube-version',VERSIONS['kubernetes']+'.0')) if x]
        deployments = {d['metadata']['name']:d['spec']['template']['spec'] for d in platform if d['kind']=='Deployment'}
        assert set(deployments)=={'iris-api','iris-build-worker','iris-deploy-worker'} and deployments['iris-build-worker']['serviceAccountName']=='build-worker'
        assert all(p['containers'][0]['image'].endswith('/iris/was@'+digest) and p['securityContext']['runAsNonRoot'] for p in deployments.values())
        api_ingress = next(d for d in platform if d['kind']=='Ingress')
        assert api_ingress['spec']['rules'][0]['host']=='api.likelion.uk' and api_ingress['metadata']['annotations']['alb.ingress.kubernetes.io/group.name']=='iris-platform-external'
        migration = next(d for d in platform if d['kind']=='Job')['metadata']['annotations']
        assert migration['argocd.argoproj.io/hook']=='Sync' and migration['argocd.argoproj.io/sync-wave']=='-1'
        policies = {d['metadata']['name'] for d in platform if d['kind']=='NetworkPolicy'}
        assert policies=={'iris-api-from-alb','iris-deploy-worker-to-argocd','iris-platform-to-database'}
        platform_kinds = {(d['apiVersion'].rpartition('/')[0], d['kind']) for d in platform}
        assert platform_kinds <= {(w['group'],w['kind']) for w in platform_project['namespaceResourceWhitelist']}, f'iris-platform-project must allow chart kinds: {platform_kinds}'
        appset = next(d for d in gitops if d['kind']=='ApplicationSet')['spec']
        chart_source, values_source = appset['template']['spec']['sources']
        assert appset['syncPolicy']['applicationsSync']=='create-update', 'Removed service directories must not delete running services.'
        assert chart_source['targetRevision']==json.loads((ROOT/'helm/gitops/values.yaml').read_text())['services']['chartRevision']=='iris-service-'+yaml.safe_load((ROOT/'helm/charts/iris-service/Chart.yaml').read_text())['version'], 'ApplicationSet must pin the current iris-service chart tag.'
        assert values_source['ref']=='values' and chart_source['helm']['valueFiles']==['$values/{{ .path.path }}/values.yaml']
        assert appset['template']['metadata']['name']=='svc-{{ index .path.segments 1 }}' and appset['template']['spec']['destination']=={'server':targets['workload']['endpoint'],'namespace':'svc-{{ index .path.segments 1 }}'}
        services = next(d for d in gitops if d['kind']=='AppProject' and d['metadata']['name']=='iris-svc-project')['spec']
        assert services['destinations']==[{'server':targets['workload']['endpoint'],'namespace':'svc-*'}] and services['clusterResourceWhitelist']==[{'group':'','kind':'Namespace'}]
        assert next(d for d in gitops if d['kind']=='ApplicationSet')['metadata']['name']=='iris-svc-appset' and appset['template']['spec']['project']=='iris-svc-project'
        assert [r['name'] for r in services['roles']]==['iris-deploy-reader']
        service_docs = render(ROOT/'helm/charts/iris-service', ROOT/'helm/charts/iris-service/ci/aws-values.yaml', namespace='svc-12')
        ingress = next(d for d in service_docs if d['kind']=='Ingress')
        assert ingress['metadata']['annotations']['alb.ingress.kubernetes.io/group.name']=='iris-service-external', 'All services share the external ALB group.'
        rendered_kinds = {(d['apiVersion'].rpartition('/')[0], d['kind']) for d in service_docs}
        assert rendered_kinds <= {(w['group'],w['kind']) for w in services['namespaceResourceWhitelist']}, f'iris-svc-project must allow chart kinds: {rendered_kinds}'
        allowed = {p['metadata']['name'].removeprefix('iris-addons-'): {(w['group'],w['kind']) for w in p['spec']['clusterResourceWhitelist']} for p in gitops if p['kind']=='AppProject'}
        bad = directory/'bad.json'; bad.write_text(json.dumps({'revision':'main','targets':targets}))
        failed = subprocess.run([HELM,'template','check',str(ROOT/'helm/gitops'),'-f',str(bad)],capture_output=True)
        assert failed.returncode, 'Mutable Git revision must fail schema validation.'
        bad.write_text(json.dumps({'revision':'a'*40,'targets':targets,'services':{'repoURL':'https://github.com/other/repo.git'}}))
        failed = subprocess.run([HELM,'template','check',str(ROOT/'helm/gitops'),'-f',str(bad)],capture_output=True)
        assert failed.returncode, 'Only the reviewed GitOps repository may feed user services.'
        charts = {}
        for name, pin in VERSIONS['charts'].items():
            if name=='argo-cd': continue
            command('pull',name,'--repo',pin['repo'],'--version',pin['version'],'--untar','--untardir',directory)
            charts[name] = directory/name
        for purpose in targets:
            base = ROOT/f'clusters/aws-dev-{purpose}/values'
            baseline = render(ROOT/'helm/charts/cluster-baseline',base/'baseline.yaml')
            sc = next(d for d in baseline if d['kind']=='StorageClass')
            assert sc['volumeBindingMode']=='WaitForFirstConsumer' and sc['parameters']=={'type':'gp3','encrypted':'true'}
            assert any(d['kind']=='NetworkPolicy' for d in baseline)
            anchor = next(d for d in baseline if d['kind']=='Ingress')
            notes = anchor['metadata']['annotations']
            group = {'management':'iris-platform-external','workload':'iris-service-external'}[purpose]
            assert notes['alb.ingress.kubernetes.io/group.name']==notes['alb.ingress.kubernetes.io/load-balancer-name']==group and notes['alb.ingress.kubernetes.io/scheme']=='internet-facing'
            assert notes['alb.ingress.kubernetes.io/certificate-arn'].startswith('arn:aws:acm:ap-northeast-2:') and anchor['spec']['defaultBackend']['service']['port']['name']=='use-annotation'
            if purpose=='workload':
                assert yaml.safe_load((ROOT/'helm/charts/iris-service/values.yaml').read_text())['route']['groupName']==group, 'Service Ingresses must join the workload anchor ALB group.'
            rendered = list(baseline)
            for name,chart in charts.items():
                params = ('--set','clusterName=iris-dev-'+purpose,'--set','region=ap-northeast-2','--set','vpcId=vpc-0123456789abcdef0') if name=='aws-load-balancer-controller' else ()
                docs = render(chart,base/(name+'.yaml'),release='monitoring' if name=='kube-prometheus-stack' else name,namespace='observability' if name=='kube-prometheus-stack' else 'kube-system',parameters=params)
                check_images(docs)
                rendered += docs
                assert not any(d['kind']=='Ingress' or (d['kind']=='Service' and d['spec'].get('type')=='LoadBalancer') for d in docs)
                if name in ('aws-load-balancer-controller','metrics-server'):
                    deployment = next(d for d in docs if d['kind']=='Deployment')
                    assert deployment['spec']['replicas']==2
                    assert deployment['spec']['template']['spec']['topologySpreadConstraints'][0]['whenUnsatisfiable']=='ScheduleAnyway'
                    if name=='metrics-server':
                        args=deployment['spec']['template']['spec']['containers'][0]['args']
                        assert '--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt' in args
                        assert '--kubelet-insecure-tls' not in args
                else:
                    prom=next(d for d in docs if d['kind']=='Prometheus')['spec']
                    assert prom['retention']=='3d' and prom['retentionSize']=='15GB'
                    assert prom['storage']['volumeClaimTemplate']['spec']['resources']['requests']['storage']=='20Gi'
                    assert any(d['kind']=='Service' and d['metadata']['name']=='monitoring-prometheus' for d in docs)
            missing = {group_kind(d) for d in rendered if d['kind'] in CLUSTER_SCOPED} - allowed[purpose]
            assert not missing, f'{purpose} AppProject must allow cluster-scoped kinds: {sorted(missing)}'
    # Preserve latest main's Deploy Worker contract fixtures; legacy examples
    # contain removed fields and are intentionally not inputs to iris-service.
    chart=ROOT/'helm/charts/iris-service'
    for values in sorted((chart/'ci').glob('*.yaml')):
        docs=render(chart,values,release='demo',namespace='iris-check')
        kinds={d['kind'] for d in docs}
        assert {'Deployment','Service','Ingress'} <= kinds
    command('lint','--strict',ROOT/'helm/charts/iris-platform')
    print('Pinned charts/images, GitOps schema, baseline, storage and replicas: passed. No runtime deployment tested.')


if __name__=='__main__':main()
