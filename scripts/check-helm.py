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


def check_platform(directory, targets, bootstrap):
    chart=ROOT/'helm/charts/iris-platform'
    for fixture in sorted((chart/'ci').glob('*.yaml')):
        docs=render(chart, fixture, release='iris-platform', namespace='iris-platform')
        values=json.loads(fixture.read_text())
        assert not any(d['kind'] in {'Secret','PersistentVolumeClaim','StatefulSet','Namespace'} for d in docs)
        deployments={d['metadata']['labels']['app.kubernetes.io/component']:d for d in docs if d['kind']=='Deployment'}
        assert set(deployments)=={'api','build-worker','deploy-worker'} | ({'error-agent'} if values['errorAgent']['enabled'] else set())
        job=next(d for d in docs if d['kind']=='Job')
        notes=job['metadata']['annotations']
        assert notes['argocd.argoproj.io/hook']=='Sync' and notes['argocd.argoproj.io/sync-wave']=='-1'
        assert notes['argocd.argoproj.io/hook-delete-policy']=='BeforeHookCreation,HookSucceeded'
        assert job['spec']['activeDeadlineSeconds']==600 and job['spec']['backoffLimit']==1
        assert not job['spec'].get('ttlSecondsAfterFinished'), 'Failed migration must remain inspectable.'
        # Each WAS component is deployed separately with its own digest; migration follows the API.
        image_of={c:values['was']['image']['repository']+'@'+values[k]['digest'] for c,k in (('api','api'),('build-worker','buildWorker'),('deploy-worker','deployWorker'))}
        image_of['migration']=image_of['api']
        for component, doc in {**deployments,'migration':job}.items():
            pod=doc['spec']['template']['spec']; container=pod['containers'][0]
            assert pod['automountServiceAccountToken'] is False and pod['securityContext']['runAsNonRoot'] is True
            assert container['securityContext']['allowPrivilegeEscalation'] is False and container['securityContext']['capabilities']['drop']==['ALL']
            env={e['name']:e for e in container['env']}
            if component=='error-agent':
                assert container['command']==['python','-m','ai_error_check_agent.api','--host','0.0.0.0','--port','8001']
                assert set(env)=={'LLM_API_KEY','AGENT_API_KEY'} and not pod.get('initContainers')
                continue
            was=image_of[component]
            refs=[e['secretRef']['name'] for e in container.get('envFrom',[]) if 'secretRef' in e]
            assert refs==([values['was']['envSecret']] if values['was'].get('envSecret') else []), 'WAS containers take the whole .env Secret.'
            assert container['image']==was and pod['securityContext']['runAsUser']==1001
            assert env['PGSSLMODE']['value']=='verify-full' and env['PGSSLROOTCERT']['value']=='/etc/iris-rds/ca-bundle.pem'
            expected_secret=values['database']['secret']
            assert env['DATABASE_URL']['valueFrom']['secretKeyRef']=={'name':expected_secret,'key':values['database']['urlKey']}
            guard=pod['initContainers'][0]
            assert guard['image']==was and guard['env']==container['env'][:3]
            assert guard['volumeMounts']==[{'name':'rds-ca','mountPath':'/etc/iris-rds','readOnly':True}]
            assert 'make_url' in guard['args'][0] and 'sslmode' in guard['args'][0] and 'cafile=' in guard['args'][0]
            if component=='migration':
                assert container['command']==['alembic','upgrade','head']
                assert pod['serviceAccountName']=='iris-platform-migration' and pod['restartPolicy']=='Never'
            elif component=='api':
                assert pod['serviceAccountName']=='iris-platform-api' and set(env)=={'DATABASE_URL','PGSSLMODE','PGSSLROOTCERT'}
                assert container['ports']==[{'name':'http','containerPort':8000}]
                assert container['readinessProbe']['httpGet']=={'path':'/readyz','port':'http'}
                assert container['livenessProbe']['httpGet']=={'path':'/healthz','port':'http'}
            else:
                assert pod['serviceAccountName']==component and not any(k.endswith('Probe') for k in container), 'Workers have no HTTP health endpoints.'
                assert container['command']==['python','-m','app.workers.'+component.replace('-','_')]
                assert not any('AWS_ACCESS_KEY' in key or 'AWS_SECRET_ACCESS' in key for key in env)
                if component=='build-worker':
                    assert pod['terminationGracePeriodSeconds']>=120 and 'ARGOCD_TOKEN' not in env
                    assert all(env[key]['valueFrom']['secretKeyRef']['name']==values['buildWorker']['githubSecret'] for key in ('GITHUB_APP_ID','GITHUB_APP_PRIVATE_KEY','GITHUB_PUBLIC_INSTALLATION_ID'))
                else:
                    assert 'GITHUB_APP_PRIVATE_KEY' not in env
                    assert env['ARGOCD_TOKEN']['valueFrom']['secretKeyRef']['name']==values['deployWorker']['argocdSecret']
                    assert all(env[key]['valueFrom']['secretKeyRef']['name']==values['deployWorker']['githubSecret'] for key in ('GITOPS_APP_ID','GITOPS_APP_PRIVATE_KEY','GITOPS_INSTALLATION_ID'))
                    assert next(v for v in pod['volumes'] if v['name']=='argocd-ca')['configMap']['name']==values['deployWorker']['caConfigMap']
        prep=[d for d in docs if d['kind'] in {'ServiceAccount','ConfigMap','NetworkPolicy'}]
        assert all(d['metadata']['annotations']['argocd.argoproj.io/sync-wave']=='-2' for d in prep)
        ingress=next(d for d in docs if d['kind']=='Ingress'); notes=ingress['metadata']['annotations']
        assert notes['alb.ingress.kubernetes.io/group.name']=='iris-platform-external' and notes['alb.ingress.kubernetes.io/target-type']=='ip'
        assert notes['alb.ingress.kubernetes.io/healthcheck-path']=='/readyz' and notes['alb.ingress.kubernetes.io/success-codes']=='204'
        assert not any(key in notes for key in ('alb.ingress.kubernetes.io/certificate-arn','alb.ingress.kubernetes.io/scheme')), 'Shared settings belong to baseline anchor.'
        # listen-ports is per Ingress in LBC; it must match the anchor or HTTPS gets no rule.
        assert json.loads(notes['alb.ingress.kubernetes.io/listen-ports'])==[{'HTTP':80},{'HTTPS':443}]
        service=next(d for d in docs if d['kind']=='Service' and d['metadata']['name']=='iris-platform-api')['spec']
        assert service['type']=='ClusterIP' and service['ports']==[{'name':'http','port':8000,'targetPort':'http'}]
        policies={d['metadata']['name']:d['spec'] for d in docs if d['kind']=='NetworkPolicy'}
        alb=policies['iris-platform-api-alb'];rds=policies['iris-platform-rds'];argo=policies['iris-platform-deploy-argocd']
        assert alb['ingress'][0]['ports']==[{'protocol':'TCP','port':8000}] and alb['podSelector']['matchLabels']['app.kubernetes.io/component']=='api'
        assert [e['ipBlock']['cidr'] for e in alb['ingress'][0]['from']]==values['network']['albSubnetCidrs']
        assert rds['podSelector']['matchLabels']['iris.dev/database-client']=='true' and rds['egress'][0]['ports']==[{'protocol':'TCP','port':5432}]
        assert [e['ipBlock']['cidr'] for e in rds['egress'][0]['to']]==values['network']['rdsSubnetCidrs']
        assert argo['egress'][0]['ports']==[{'protocol':'TCP','port':8080}]
        server=next(d for d in bootstrap if d['kind']=='Deployment' and d['metadata']['name']=='argocd-server')
        assert 8080 in [p['containerPort'] for p in server['spec']['template']['spec']['containers'][0]['ports']]
        assert argo['egress'][0]['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/name']==server['spec']['template']['metadata']['labels']['app.kubernetes.io/name']
        total_cpu=sum(int(doc['spec']['template']['spec']['containers'][0]['resources']['limits']['cpu'].removesuffix('m')) for doc in [*deployments.values(),job])
        assert total_cpu<4000, 'Initial platform limits must leave quota headroom for migration/rollout.'
    import copy
    good=json.loads((chart/'ci/was-values.yaml').read_text())
    mutations=[lambda v:v.update(unknown=True),lambda v:v['api'].update(digest='latest'),lambda v:v['api'].update(host=''),lambda v:v['database'].update(secret=''),lambda v:v['buildWorker'].update(githubSecret=v['deployWorker']['githubSecret']),lambda v:v['network'].update(rdsSubnetCidrs=[]),lambda v:v['network'].update(albSubnetCidrs=['0.0.0.0/0']),lambda v:v['network'].update(rdsSubnetCidrs=['999.0.0.0/24']),lambda v:v['errorAgent'].update(enabled=True)]
    bad=directory/'bad-platform.json'
    for change in mutations:
        values=copy.deepcopy(good);change(values);bad.write_text(json.dumps(values))
        result=subprocess.run([HELM,'template','iris-platform',str(chart),'-f',str(bad),'--kube-version',VERSIONS['kubernetes']+'.0','--namespace','iris-platform'],capture_output=True)
        assert result.returncode, 'Invalid platform inputs must fail before deployment.'
    cluster=ROOT/'clusters/aws-dev-management/values/platform.yaml'
    assert not any(d['kind'] in {'Deployment','Job','Ingress'} for d in render(chart, cluster, release='iris-platform', namespace='iris-platform')), 'Without GitOps digests no component may be deployed.'
    digest='sha256:'+'a'*64
    only_api=directory/'was.yaml'; only_api.write_text(json.dumps({'api':{'digest':digest}}))
    agent=directory/'error-check-agent.yaml'; agent.write_text(json.dumps({'errorAgent':{'image':{'digest':digest}}}))
    command('lint','--strict',chart,'-f',cluster,'-f',only_api,'--kube-version',VERSIONS['kubernetes']+'.0','--namespace','iris-platform')
    docs=[x for x in yaml.safe_load_all(command('template','iris-platform',chart,'-f',cluster,'-f',only_api,'-f',agent,'--kube-version',VERSIONS['kubernetes']+'.0','--namespace','iris-platform')) if x]
    assert {d['metadata']['name'] for d in docs if d['kind'] in {'Deployment','Job','Ingress'}}=={'iris-platform-api','iris-platform-migration'}, 'Only components with a digest deploy; the agent also needs enabled.'
    enabled=directory/'gitops-platform.json';enabled.write_text(json.dumps({'revision':'a'*40,'targets':targets,'platform':{'enabled':True}}))
    docs=render(ROOT/'helm/gitops', enabled, namespace='argocd')
    assert sum(d['kind']=='Application' for d in docs)==11 and sum(d['kind']=='AppProject' for d in docs)==4
    app=next(d for d in docs if d['kind']=='Application' and d['metadata']['name']=='iris-platform')
    assert app['metadata']['finalizers']==['resources-finalizer.argocd.argoproj.io'] and app['spec']['syncPolicy']['automated']=={'prune':True,'selfHeal':True}
    assert app['spec']['destination']=={'server':targets['management']['endpoint'],'namespace':'iris-platform'}
    chart_source, gitops_source = app['spec']['sources']
    assert chart_source['targetRevision']=='a'*40 and chart_source['path']=='helm/charts/iris-platform' and chart_source['helm']['ignoreMissingValueFiles']
    repos=json.loads((ROOT/'terraform/config/platform-ecr-repositories.json').read_text())
    assert chart_source['helm']['valueFiles']==['../../../clusters/aws-dev-management/values/platform.yaml',*[f'$gitops/platform/aws-dev-management/{r}.yaml' for r in repos]]
    assert gitops_source=={'repoURL':'https://github.com/2026-softbank-1/iris-gitops-environments.git','targetRevision':'main','ref':'gitops'}
    project=next(d for d in docs if d['kind']=='AppProject' and d['metadata']['name']=='iris-platform-project')['spec']
    assert project['clusterResourceWhitelist']==[] and project['destinations']==[app['spec']['destination']] and gitops_source['repoURL'] in project['sourceRepos']
    allowed={(p['group'],p['kind']) for p in project['namespaceResourceWhitelist']}
    assert allowed=={('apps','Deployment'),('','Service'),('','ConfigMap'),('','ServiceAccount'),('batch','Job'),('networking.k8s.io','NetworkPolicy'),('networking.k8s.io','Ingress')}

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
        # Platform is opt-in at bootstrap (GITOPS_PLATFORM_ENABLED); check_platform covers it.
        assert sum(d['kind']=='Application' for d in gitops)==10
        assert sum(d['kind']=='AppProject' for d in gitops)==3
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
        check_platform(directory, targets, bootstrap)
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
            # Addons render where the GitOps Application exists; each needs its cluster values file.
            enabled = {d['metadata']['name'].removeprefix(f'iris-{purpose}-') for d in gitops if d['kind']=='Application' and d['metadata']['name'].startswith(f'iris-{purpose}-')} - {'baseline'}
            assert enabled == {name for name in charts if (base/(name+'.yaml')).exists()}, f'{purpose} addons and values files differ: {sorted(enabled)}'
            for name,chart in charts.items():
                if name not in enabled: continue
                params = ('--set','clusterName=iris-dev-'+purpose,'--set','region=ap-northeast-2','--set','vpcId=vpc-0123456789abcdef0') if name=='aws-load-balancer-controller' else ()
                namespace = 'kube-system' if name in ('aws-load-balancer-controller','metrics-server') else 'observability'
                docs = render(chart,base/(name+'.yaml'),release='monitoring' if name=='kube-prometheus-stack' else name,namespace=namespace,parameters=params)
                check_images(docs)
                rendered += docs
                balancers = [d for d in docs if d['kind']=='Service' and d['spec'].get('type')=='LoadBalancer']
                assert not any(d['kind']=='Ingress' for d in docs)
                if name=='opentelemetry-collector' and purpose=='management':
                    # The only addon load balancer: internal NLB for workload agents, behind basic auth.
                    [nlb] = balancers
                    assert nlb['metadata']['annotations']['service.beta.kubernetes.io/aws-load-balancer-scheme']=='internal'
                    assert nlb['spec']['loadBalancerSourceRanges']==['10.40.32.0/20','10.40.48.0/20'] and [p['port'] for p in nlb['spec']['ports']]==[4318]
                    relay = yaml.safe_load(next(d for d in docs if d['kind']=='ConfigMap')['data']['relay'])
                    assert list(relay['receivers'])==['otlp'] and list(relay['receivers']['otlp']['protocols'])==['http'], 'Gateway must accept only authenticated OTLP/HTTP.'
                    assert relay['receivers']['otlp']['protocols']['http']['auth']['authenticator']=='basicauth/server'
                    assert set(relay['service']['pipelines'])=={'logs','metrics'}
                else:
                    assert not balancers
                if name in ('aws-load-balancer-controller','metrics-server'):
                    deployment = next(d for d in docs if d['kind']=='Deployment')
                    assert deployment['spec']['replicas']==2
                    assert deployment['spec']['template']['spec']['topologySpreadConstraints'][0]['whenUnsatisfiable']=='ScheduleAnyway'
                    if name=='metrics-server':
                        args=deployment['spec']['template']['spec']['containers'][0]['args']
                        assert '--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt' in args
                        assert '--kubelet-insecure-tls' not in args
                elif name=='loki':
                    workloads = [d for d in docs if d['kind'] in ('Deployment','StatefulSet','DaemonSet')]
                    assert [(d['kind'],d['metadata']['name'],d['spec']['replicas']) for d in workloads]==[('StatefulSet','loki',1)], 'SingleBinary only: no gateway, caches or canary.'
                    assert workloads[0]['spec']['template']['spec']['serviceAccountName']=='loki', 'Pod Identity is bound to observability/loki.'
                    assert any(d['kind']=='Service' and d['metadata']['name']=='loki' and 3100 in [p['port'] for p in d['spec']['ports']] for d in docs)
                elif name=='kube-prometheus-stack':
                    prom=next(d for d in docs if d['kind']=='Prometheus')['spec']
                    # Management also keeps user service metrics written by the OTel gateway.
                    assert prom['retention']=={'management':'7d','workload':'3d'}[purpose] and prom['retentionSize']=='15GB'
                    assert prom.get('enableRemoteWriteReceiver', False)==(purpose=='management')
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
    print('Pinned charts/images, GitOps schema, baseline, platform TLS/migration/credentials, storage and replicas: passed. No runtime deployment tested.')


if __name__=='__main__':main()
