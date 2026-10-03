#!/usr/bin/env python3
"""Render the pinned stack and check operational contracts without a cluster."""
import copy
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


# The last Deployment-based iris-service chart; on-prem services stay on it (no Argo Rollouts there).
ONPREM_CHART_REVISION = 'iris-service-0.6.0'

# Cluster-scoped kinds Argo must be allowed to create through the addon AppProjects.
CLUSTER_SCOPED = {'Namespace','StorageClass','ClusterRole','ClusterRoleBinding','MutatingWebhookConfiguration','ValidatingWebhookConfiguration','CustomResourceDefinition','APIService','IngressClass','IngressClassParams','PriorityClass','PersistentVolume','CSIDriver','RuntimeClass'}


def group_kind(doc):
    return (doc['apiVersion'].rpartition('/')[0], doc['kind'])


def check_onprem_gateway(directory, targets, baseline):
    chart = ROOT/'helm/charts/iris-onprem-gateway'
    root_values = directory/'gateway-root.json'
    indexed = lambda docs: {(d['kind'], d['metadata']['name']): d for d in docs}
    baseline = indexed(baseline)
    # The gateway can be tested before on-prem workload deployment is enabled.
    for gateway_enabled in (False, True):
        for services_enabled in (False, True):
            values = {'revision':'main', 'targets':targets,
                      'onpremGateway':{'enabled':gateway_enabled},
                      'services':{'onprem':{'enabled':services_enabled, 'server':'https://onprem.example:6443',
                                            'ingressClassName':'traefik', 'egressDeniedCidrs':['192.168.0.0/16']}}}
            root_values.write_text(json.dumps(values))
            docs = indexed(render(ROOT/'helm/gitops', root_values, namespace='argocd'))
            enabled = gateway_enabled or services_enabled
            assert (('Application', 'iris-onprem-gateway') in docs) == enabled
            assert (('ApplicationSet', 'iris-svc-onprem-appset') in docs) == services_enabled
            assert docs['ApplicationSet','iris-svc-appset'] == baseline['ApplicationSet','iris-svc-appset']
            assert docs['AppProject','iris-addons-workload'] == baseline['AppProject','iris-addons-workload']
            management = docs['AppProject','iris-addons-management']['spec']
            for ns in ('onprem-gateway', 'tailscale'):
                assert ({'server':targets['management']['endpoint'], 'namespace':ns} in management['destinations']) == enabled
            assert ({'group':'tailscale.com', 'kind':'ProxyClass'} in management['clusterResourceWhitelist']) == enabled
            for key, doc in docs.items():
                if doc['kind']=='AppProject' and key[1]!='iris-addons-management':
                    assert {'group':'tailscale.com','kind':'ProxyClass'} not in doc['spec']['clusterResourceWhitelist']
            if enabled:
                spec = docs['Application','iris-onprem-gateway']['spec']
                assert spec['project']=='iris-addons-management'
                assert spec['source']['targetRevision']=='main' and spec['source']['helm']['releaseName']=='onprem-gateway'
                assert spec['destination']=={'server':targets['management']['endpoint'],'namespace':'onprem-gateway'}
                assert spec['syncPolicy']['automated']=={'prune':False,'selfHeal':True}
                assert spec['syncPolicy']['managedNamespaceMetadata']['labels']=={'elbv2.k8s.aws/pod-readiness-gate-inject':'enabled'}
                assert set(spec['syncPolicy']['syncOptions'])=={'CreateNamespace=true','ServerSideApply=true','RespectIgnoreDifferences=true'}
                assert spec['ignoreDifferences']==[{'group':'','kind':'Service','name':'iris-onprem-apps','jsonPointers':['/spec/externalName']}]
            if not services_enabled:
                # Normalize only gateway-owned additions; all existing AWS documents must be identical.
                expected = copy.deepcopy(docs)
                expected.pop(('Application','iris-onprem-gateway'), None)
                project = expected['AppProject','iris-addons-management']['spec']
                project['destinations'] = [d for d in project['destinations'] if d['namespace'] not in ('tailscale','onprem-gateway')]
                project['clusterResourceWhitelist'] = [w for w in project['clusterResourceWhitelist'] if w['kind']!='ProxyClass']
                for doc in expected.values():
                    sources = doc.get('spec',{}).get('sources') or [doc.get('spec',{}).get('source',{})]
                    for source in sources:
                        if source.get('repoURL')==values.get('repoURL', json.loads((ROOT/'helm/gitops/values.yaml').read_text())['repoURL']):
                            source['targetRevision']='a'*40
                assert expected==baseline, 'Gateway-only activation must preserve all existing AWS documents.'

    fixtures = [ROOT/'clusters/aws-dev-management/values/onprem-gateway.yaml', *sorted((chart/'ci').glob('*.yaml'))]
    for fixture in fixtures:
        values = yaml.safe_load(fixture.read_text())
        public = values['publicIngress']['enabled']
        docs = render(chart, fixture, namespace='onprem-gateway')
        check_images(docs)
        resources = indexed(docs)
        assert (('Ingress','onprem-gateway') in resources) == public
        np = resources['NetworkPolicy','onprem-gateway']['spec']
        assert np['podSelector']=={'matchLabels':{'app.kubernetes.io/name':'onprem-gateway'}}
        assert np['policyTypes']==['Ingress','Egress']
        assert np['egress']==[
            {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},
                    'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}],
             'ports':[{'protocol':'UDP','port':53},{'protocol':'TCP','port':53}]},
            {'to':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'tailscale'}},
                    'podSelector':{'matchLabels':{'iris.dev/proxy':'onprem-http'}}}],
             'ports':[{'protocol':'TCP','port':80}]}]
        if public:
            ingress = resources['Ingress','onprem-gateway']
            notes = ingress['metadata']['annotations']
            assert notes['alb.ingress.kubernetes.io/group.name']=='iris-platform-external'
            assert notes['alb.ingress.kubernetes.io/group.order']=='1000'
            assert notes['alb.ingress.kubernetes.io/target-type']=='ip'
            assert notes['alb.ingress.kubernetes.io/healthcheck-path']=='/healthz'
            assert notes['alb.ingress.kubernetes.io/ssl-redirect']=='443'
            assert json.loads(notes['alb.ingress.kubernetes.io/listen-ports'])==[{'HTTP':80},{'HTTPS':443}]
            assert notes['alb.ingress.kubernetes.io/certificate-arn']==values['certificateArn']
            assert ingress['spec']['ingressClassName']=='alb' and ingress['spec']['rules'][0]['host']=='*.internal.likelion.uk'
            assert np['ingress']==[{'from':[{'ipBlock':{'cidr':cidr}} for cidr in ('10.40.240.0/24','10.40.241.0/24')],
                                   'ports':[{'protocol':'TCP','port':8080}]}]
        else:
            assert np['ingress']==[]
        proxy_np = resources['NetworkPolicy','iris-onprem-http']
        assert proxy_np['metadata']['namespace']=='tailscale'
        assert proxy_np['spec']=={'podSelector':{'matchLabels':{'iris.dev/proxy':'onprem-http'}}, 'policyTypes':['Ingress'],
            'ingress':[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'onprem-gateway'}},
                                'podSelector':{'matchLabels':{'app.kubernetes.io/name':'onprem-gateway'}}}],
                        'ports':[{'protocol':'TCP','port':80}]}]}
        # Per-server HTTP egress Services use the same ProxyClass, so the gateway egress rule above and this
        # ingress rule already cover them without listing servers.
        api_np = resources['NetworkPolicy','iris-onprem-api']
        assert api_np['metadata']['namespace']=='tailscale'
        assert api_np['spec']=={'podSelector':{'matchLabels':{'iris.dev/proxy':'onprem-api'}}, 'policyTypes':['Ingress'],
            'ingress':[{'from':[{'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'argocd'}}}],
                        'ports':[{'protocol':'TCP','port':6443}]}]}
        api_proxy = resources['ProxyClass','iris-onprem-api']
        assert 'namespace' not in api_proxy['metadata'] and api_proxy['metadata']['annotations']['argocd.argoproj.io/sync-wave']=='-1'
        assert api_proxy['spec']['statefulSet']['pod']['labels']=={'iris.dev/proxy':'onprem-api'}
        proxy = resources['ProxyClass','iris-onprem-http']
        assert 'namespace' not in proxy['metadata'] and proxy['metadata']['annotations']['argocd.argoproj.io/sync-wave']=='-1'
        assert proxy['spec']['statefulSet']['pod']=={'labels':{'iris.dev/proxy':'onprem-http'},
            'tailscaleContainer':{'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}}}}
        service = resources['Service','iris-onprem-apps']
        assert service['spec']=={'type':'ExternalName','externalName':'placeholder','ports':[{'name':'http','port':80,'protocol':'TCP'}]}
        assert service['metadata']['annotations']=={'tailscale.com/proxy-class':'iris-onprem-http',
            'tailscale.com/hostname':'iris-mgmt-onprem-http','tailscale.com/tailnet-fqdn':'iris-onprem-01.tailb046e8.ts.net',
            'tailscale.com/tags':'tag:iris-onprem-apps'}
        pod = resources['Deployment','onprem-gateway']['spec']['template']
        spec = pod['spec']; container = spec['containers'][0]
        assert spec['automountServiceAccountToken'] is False
        assert spec['securityContext']=={'runAsNonRoot':True,'runAsUser':101,'runAsGroup':101,'fsGroup':101,
                                        'fsGroupChangePolicy':'OnRootMismatch','seccompProfile':{'type':'RuntimeDefault'}}
        assert container['securityContext']=={'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}}
        assert container['command']==['nginx'] and container['args']==['-c','/etc/nginx/nginx.conf','-g','daemon off;']
        assert next(v for v in spec['volumes'] if v['name']=='tmp')['emptyDir']=={'sizeLimit':'64Mi'}
        conf = resources['ConfigMap','onprem-gateway']['data']['nginx.conf']
        assert 'listen 8080 default_server;' in conf and 'server_name *.internal.likelion.uk;' in conf
        assert 'proxy_set_header Host $host;' in conf and 'proxy_set_header X-Forwarded-Proto $forwarded_proto;' in conf
        # Legacy hosts keep the fixed upstream; a host ending in -{serverKey} goes to that server's egress Service.
        assert 'default http://iris-onprem-apps.onprem-gateway.svc.cluster.local:80;' in conf
        assert r'"~^[a-z0-9-]+-(?<server_key>[a-z][a-z0-9]{7})\.internal\.likelion\.uk$" http://iris-onprem-apps-$server_key.onprem-gateway.svc.cluster.local:80;' in conf
        assert 'proxy_pass $onprem_upstream;' in conf
        assert 'valid=30s ipv6=off;' in conf and 'proxy_connect_timeout 5s;' in conf
        assert 'proxy_send_timeout 60s;' in conf and 'proxy_read_timeout 60s;' in conf
        assert not re.search(r'proxy_pass\s+[^;]*\$(host|http_host)',conf), 'Fixed upstream prevents an open proxy.'
        changed = indexed(render(chart, fixture, namespace='onprem-gateway', parameters=('--set','host=*.other.test')))
        assert changed['Deployment','onprem-gateway']['spec']['template']['metadata']['annotations']['checksum/config'] != pod['metadata']['annotations']['checksum/config']

    bad_values = directory/'bad-gateway.json'
    mutations = [
        {'publicIngress':{'enabled':True}},
        {'host':'*.internal.likelion.uk; return 200;'}, {'host':'*.bad..test'}, {'host':'example.test'},
        {'host':'*.'+'a'*64+'.test'}, {'host':'*.'+'.'.join(['a'*63]*4)},
        {'upstream':{'port':6443}}, {'upstream':{'tailnetFqdn':'bad;host'}},
        {'albSourceCidrs':['0.0.0.0/0']}, {'albSourceCidrs':['999.1.1.1/24']},
        {'albSourceCidrs':['10.0.0.1/33']}, {'publicIngress':{'enabled':'false'}},
        {'certificateArn':'arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000'},
        {'publicIngress':{'enabled':True},'certificateArn':yaml.safe_load((chart/'ci/public-values.yaml').read_text())['certificateArn'],'albSourceCidrs':[]},
    ]
    for override in mutations:
        bad_values.write_text(json.dumps({'publicIngress':{'enabled':False}, **override}))
        result = subprocess.run([HELM,'template','check',str(chart),'-f',str(bad_values)],capture_output=True)
        assert result.returncode, f'Invalid gateway values must fail: {override}'
    policy = json.loads((ROOT/'clusters/aws-dev-management/onprem/tailnet-policy-additions.json').read_text())
    assert policy['tagOwners']=={'tag:iris-onprem-apps':['tag:iris-operator'],'tag:iris-onprem-api':['tag:iris-operator']}
    # Each egress proxy reaches only its port; on-prem servers (tag:iris-onprem) are destinations only.
    assert policy['grants']==[{'src':['tag:iris-onprem-apps'],'dst':['tag:iris-onprem'],'ip':['tcp:80']},
                              {'src':['tag:iris-onprem-api'],'dst':['tag:iris-onprem'],'ip':['tcp:6443']}]
    assert not any('tag:iris-onprem' in grant['src'] for grant in policy['grants'])
    tests = {t['src']: t for t in policy['tests']}
    assert tests['tag:iris-onprem-apps']=={'src':'tag:iris-onprem-apps','accept':['tag:iris-onprem:80'],'deny':['tag:iris-onprem:6443']}
    assert tests['tag:iris-onprem-api']['accept']==['tag:iris-onprem:6443'] and 'tag:iris-onprem:80' in tests['tag:iris-onprem-api']['deny']
    assert 'accept' not in tests['tag:iris-onprem'] and {'tag:iris-onprem:80','tag:iris-onprem:6443','tag:iris-operator:443'} <= set(tests['tag:iris-onprem']['deny'])
    # The chart's egress Services must use exactly the tags this fragment grants.
    proxy_tags = set()
    for chart, fixture in (('iris-onprem-gateway', ROOT/'clusters/aws-dev-management/values/onprem-gateway.yaml'), ('iris-onprem-server', ROOT/'helm/charts/iris-onprem-server/ci/server-values.yaml')):
        rendered = yaml.safe_load_all(command('template', 'check', ROOT/'helm/charts'/chart, '-f', fixture, '--namespace', 'onprem-gateway'))
        proxy_tags |= {d['metadata']['annotations']['tailscale.com/tags'] for d in rendered if d and d['kind']=='Service' and 'tailscale.com/tags' in d['metadata'].get('annotations', {})}
    assert proxy_tags=={src for grant in policy['grants'] for src in grant['src']}
    # These are fragment assertions, not evaluation of the live/merged Tailscale policy.


def semver_of(revision):
    return tuple(map(int, revision.removeprefix('iris-service-').split('.')))


def check_onprem_servers(directory, targets, baseline):
    """User-registered on-prem servers: GitOps flag, per-server chart and probe chart (no cluster)."""
    indexed = lambda docs: {(d['kind'], d['metadata']['name']): d for d in docs}
    base = indexed(baseline)
    management = targets['management']['endpoint']
    defaults = json.loads((ROOT/'helm/gitops/values.yaml').read_text())
    assert defaults['onpremServers']['enabled'] is False, 'Enable only after the runbook prerequisites (docs/runbooks/onprem-server-registration.md).'
    for name in ('iris-onprem-servers', 'iris-onprem-probe'):
        assert ('AppProject', name) not in base
    for name in ('iris-onprem-servers', 'iris-svc-onprem-servers-appset'):
        assert ('ApplicationSet', name) not in base
    values = directory/'gitops-onprem-servers.json'
    # Enabling keeps the default pin only if it already supports imagePullSecrets.
    values.write_text(json.dumps({'revision':'a'*40, 'targets':targets, 'services':{'onprem':{'enabled':False}},
                                  'onpremGateway':{'enabled':False}, 'onpremServers':{'enabled':True}}))
    if semver_of(defaults['onpremServers']['chartRevision']) < (0,8,0):
        failed = subprocess.run([HELM,'template','check',str(ROOT/'helm/gitops'),'-f',str(values),'--kube-version',VERSIONS['kubernetes']+'.0'],capture_output=True,text=True)
        assert failed.returncode and 'below iris-service-0.8.0' in failed.stderr, 'Enabling servers with a chart that rejects imagePullSecrets must fail.'
    server_revision = 'iris-service-0.8.0'
    values.write_text(json.dumps({'revision':'a'*40, 'targets':targets, 'services':{'onprem':{'enabled':False}},
                                  'onpremGateway':{'enabled':False}, 'onpremServers':{'enabled':True,'chartRevision':server_revision}}))
    docs = indexed(render(ROOT/'helm/gitops', values, namespace='argocd'))
    # Existing AWS documents stay as they are; only the service project gains the onprem-* destination.
    for key in (('ApplicationSet','iris-svc-appset'), ('AppProject','iris-addons-workload')):
        assert docs[key]==base[key]
    assert ('ApplicationSet','iris-svc-onprem-appset') not in docs, 'The legacy on-prem ApplicationSet keeps its own flag.'
    services = docs['AppProject','iris-svc-project']['spec']
    assert services['destinations']==[{'server':targets['workload']['endpoint'],'namespace':'svc-*'},{'name':'onprem-*','namespace':'svc-*'}]
    assert ('Application','iris-onprem-gateway') in docs, 'Servers route through the gateway, so the flag enables it.'
    addons = docs['AppProject','iris-addons-management']['spec']
    assert {'server':management,'namespace':'tailscale'} in addons['destinations'] and {'group':'tailscale.com','kind':'ProxyClass'} in addons['clusterResourceWhitelist']
    project_repos = {k[1]: d['spec']['sourceRepos'] for k, d in docs.items() if k[0]=='AppProject'}

    servers_project = docs['AppProject','iris-onprem-servers']['spec']
    assert servers_project['destinations']==[{'server':management,'namespace':'argocd'},{'server':management,'namespace':'onprem-gateway'}]
    assert servers_project['clusterResourceWhitelist']==[]
    allowed = {(w['group'],w['kind']) for w in servers_project['namespaceResourceWhitelist']}
    assert allowed=={('bitnami.com','SealedSecret'),('','Service'),('argoproj.io','Application')}
    probe_project = docs['AppProject','iris-onprem-probe']['spec']
    assert probe_project['destinations']==[{'name':'onprem-*','namespace':'iris-system'}] and probe_project['clusterResourceWhitelist']==[]
    assert probe_project['namespaceResourceWhitelist']==[{'group':'','kind':'ConfigMap'}]
    assert probe_project['roles']==[{'name':'iris-deploy-reader','policies':['p, proj:iris-onprem-probe:iris-deploy-reader, applications, get, iris-onprem-probe/*, allow']}], 'Deploy Worker only reads probe status.'

    server_set = docs['ApplicationSet','iris-onprem-servers']['spec']
    assert server_set['generators']==[{'git':{'repoURL':defaults['services']['repoURL'],'revision':'main','files':[{'path':'platform/onprem-servers/*/values.yaml'}]}}]
    assert server_set['syncPolicy']['applicationsSync']=='sync'
    template = server_set['template']
    assert template['metadata']=={'name':'iris-onprem-server-{{ .path.basename }}','finalizers':['resources-finalizer.argocd.argoproj.io']}
    spec = template['spec']
    assert spec['project']=='iris-onprem-servers' and spec['destination']=={'server':management,'namespace':'argocd'}
    chart_source, values_source = spec['sources']
    assert chart_source['path']=='helm/charts/iris-onprem-server' and chart_source['targetRevision']=='a'*40, 'Infra chart follows the reviewed infra revision.'
    assert chart_source['helm']['valueFiles']==['$values/{{ .path.path }}/values.yaml'] and values_source['ref']=='values'
    assert chart_source['helm']['valuesObject']['gatewayNamespace']=='onprem-gateway', 'valuesObject outranks the data file.'
    assert spec['ignoreDifferences']==[{'group':'','kind':'Service','jsonPointers':['/spec/externalName']}]
    assert spec['syncPolicy']=={'automated':{'prune':True,'selfHeal':True},'syncOptions':['RespectIgnoreDifferences=true']}
    for source in spec['sources']:
        assert source['repoURL'] in project_repos[spec['project']]

    svc_set = docs['ApplicationSet','iris-svc-onprem-servers-appset']['spec']
    assert svc_set['generators'][0]['git']['directories']==[{'path':'services/*/onprem-*'}]
    svc = svc_set['template']
    assert svc['metadata']['name']=='svc-{{ index .path.segments 1 }}', 'Deploy Worker observes svc-{id} on every target.'
    assert svc['spec']['project']=='iris-svc-project'
    assert svc['spec']['destination']=={'name':'{{ index .path.segments 2 }}','namespace':'svc-{{ index .path.segments 1 }}'}
    svc_chart = svc['spec']['sources'][0]
    assert svc_chart['targetRevision']==server_revision
    assert svc_chart['helm']['valuesObject']=={'route':{'className':'traefik'},'networkPolicy':{'egressDeniedCidrs':defaults['onpremServers']['egressDeniedCidrs']}}
    chart_version = yaml.safe_load((ROOT/'helm/charts/iris-service/Chart.yaml').read_text())['version']
    assert semver_of(defaults['onpremServers']['chartRevision'])<=semver_of(chart_version), 'Pin the current chart tag or an earlier released one.'
    # Argo directory globs use path.Match: '*' never crosses '/', and 'onprem' and 'onprem-*' do not overlap.
    import fnmatch
    for path, legacy, server in (('services/12/onprem',True,False),('services/12/onprem-k3x9q2ma',False,True),('services/12/prod',False,False)):
        assert fnmatch.fnmatchcase(path,'services/*/onprem')==legacy and fnmatch.fnmatchcase(path,'services/*/onprem-*')==server

    # Render the server chart the way the ApplicationSet does: the data file plus its valuesObject.
    chart = ROOT/'helm/charts/iris-onprem-server'
    data = yaml.safe_load((chart/'ci/server-values.yaml').read_text())
    key = data['server']['key']
    app_values = json.loads(json.dumps(chart_source['helm']['valuesObject']).replace('{{ .path.basename }}', key))
    combined = directory/'onprem-server.json'
    combined.write_text(json.dumps({**{k: data[k] for k in ('server','cluster')}, **app_values}))
    for fixture in (chart/'ci/server-values.yaml', combined):
        rendered = render(chart, fixture, release='onprem-server-'+key, namespace='argocd')
        check_images(rendered)
        resources = indexed(rendered)
        assert set(resources)=={('SealedSecret','cluster-onprem-'+key),('Service','iris-onprem-api-'+key),('Service','iris-onprem-apps-'+key),('Application','iris-onprem-probe-'+key)}
        assert {group_kind(d) for d in rendered} <= allowed
        destinations = {(d['server'], d['namespace']) for d in servers_project['destinations']}
        assert all((management, d['metadata']['namespace']) in destinations for d in rendered), 'Every resource names a namespace its project allows.'
        sealed = resources['SealedSecret','cluster-onprem-'+key]
        assert sealed['metadata']['namespace']=='argocd' and sealed['metadata']['annotations']['argocd.argoproj.io/sync-wave']=='-1'
        assert sealed['spec']['encryptedData']=={'config':data['cluster']['encryptedConfig']}, 'Only the sealed config is secret.'
        secret = sealed['spec']['template']
        assert secret['metadata']['name']=='cluster-onprem-'+key and secret['metadata']['namespace']=='argocd', 'Strict scope Deploy Worker sealed for.'
        assert secret['metadata']['labels']['argocd.argoproj.io/secret-type']=='cluster' and secret['type']=='Opaque'
        assert secret['data']=={'name':'onprem-'+key,'server':f'https://iris-onprem-api-{key}.argocd.svc.cluster.local:6443'}
        api, apps = resources['Service','iris-onprem-api-'+key], resources['Service','iris-onprem-apps-'+key]
        assert api['metadata']['namespace']=='argocd' and apps['metadata']['namespace']=='onprem-gateway'
        for service, proxy_class, hostname, tag, port in ((api,'iris-onprem-api','iris-mgmt-onprem-api-'+key,'tag:iris-onprem-api',6443),
                                                          (apps,'iris-onprem-http','iris-mgmt-onprem-http-'+key,'tag:iris-onprem-apps',80)):
            notes = service['metadata']['annotations']
            assert {k: v for k, v in notes.items() if k.startswith('tailscale.com/')}=={'tailscale.com/proxy-class':proxy_class,
                'tailscale.com/hostname':hostname,'tailscale.com/tailnet-fqdn':data['server']['tailnetFqdn'],'tailscale.com/tags':tag}
            assert service['spec']['type']=='ExternalName' and service['spec']['externalName']=='placeholder' and service['spec']['ports'][0]['port']==port
        probe = resources['Application','iris-onprem-probe-'+key]
        assert probe['metadata']['namespace']=='argocd' and 'finalizers' not in probe['metadata'], 'Deleting a server must not wait on its cluster.'
        assert probe['spec']['project']=='iris-onprem-probe' and probe['spec']['destination']=={'name':'onprem-'+key,'namespace':'iris-system'}
        assert probe['spec']['source']['path']=='helm/charts/iris-onprem-probe' and probe['spec']['source']['repoURL'] in project_repos['iris-onprem-probe']
        assert probe['spec']['syncPolicy']['automated']=={'prune':True,'selfHeal':True}
        assert 'syncOptions' not in probe['spec']['syncPolicy'], 'install.sh creates iris-system and grants only ConfigMaps there; no CreateNamespace.'
    assert probe['spec']['source']['targetRevision']=='a'*40, 'Probe chart follows the same reviewed infra revision.'
    probe_docs = render(ROOT/'helm/charts/iris-onprem-probe', ROOT/'helm/charts/iris-onprem-probe/ci/probe-values.yaml', release='iris-onprem-probe', namespace='iris-system')
    assert [(d['kind'], d['metadata']['name'], d['data']) for d in probe_docs]==[('ConfigMap','iris-onprem-probe',{'serverKey':key})]
    assert {group_kind(d) for d in probe_docs} <= {(w['group'],w['kind']) for w in probe_project['namespaceResourceWhitelist']}

    good = json.loads(combined.read_text())
    mutations = {
        'directory differs from key': lambda v: v.update(directoryKey='zzzzzzzz'),
        'cluster name differs': lambda v: v['server'].update(clusterName='onprem-zzzzzzzz'),
        'fqdn of another server': lambda v: v['server'].update(tailnetFqdn='iris-zzzzzzzz.tailb046e8.ts.net'),
        'fqdn outside tailnet': lambda v: v['server'].update(tailnetFqdn='iris-k3x9q2ma.example.com'),
        'key starts with digit': lambda v: (v['server'].update(key='1k3x9q2m', clusterName='onprem-1k3x9q2m', tailnetFqdn='iris-1k3x9q2m.tailb046e8.ts.net'), v.update(directoryKey='1k3x9q2m')),
        'api port': lambda v: v['server'].update(apiPort=443),
        'apps port': lambda v: v['server'].update(appsPort=8080),
        'plaintext config': lambda v: v['cluster'].update(encryptedConfig='{"bearerToken": "x"}'),
        'unknown field': lambda v: v['cluster'].update(server='https://example.com'),
        'other probe project': lambda v: v['probe'].update(project='default'),
        'unreviewed revision': lambda v: v['probe'].update(targetRevision='feature/x'),
        'missing directory key': lambda v: v.pop('directoryKey'),
        'apps Service moved': lambda v: v.update(gatewayNamespace='argocd'),
    }
    bad = directory/'bad-onprem-server.json'
    for label, change in mutations.items():
        candidate = copy.deepcopy(good); change(candidate); bad.write_text(json.dumps(candidate))
        result = subprocess.run([HELM,'template','check',str(chart),'-f',str(bad),'--namespace','argocd'],capture_output=True)
        assert result.returncode, f'Invalid server data must fail before Argo applies it: {label}'
    for bad_probe in ({'serverKey':'UPPER123'}, {'serverKey':'k3x9q2ma','extra':1}):
        bad.write_text(json.dumps(bad_probe))
        assert subprocess.run([HELM,'template','check',str(ROOT/'helm/charts/iris-onprem-probe'),'-f',str(bad)],capture_output=True).returncode


def canary_replicas(replicas, weight, max_surge=1):
    """Port of approximateWeightedCanaryStableReplicaCounts (argo-rollouts v1.10.0
    utils/replicaset/canary.go) for basic canary with maxWeight 100. Returns (canary, stable)."""
    if replicas == 0: return 0, 0
    tied = lambda total: (total*weight/100) % 1 == 0.5
    ceil, floor = -(-replicas*weight//100), replicas*weight//100
    zero_allowed = weight in (0, 100) or (replicas == 1 and max_surge == 0)
    options = []
    if ceil < replicas or zero_allowed: options.append((ceil, replicas))
    if not tied(replicas) and (floor or zero_allowed): options.append((floor, replicas))
    if max_surge > 0:
        options.append((ceil, replicas+1))
        if not tied(replicas+1) and (floor or zero_allowed): options.append((floor, replicas+1))
    canary, total = min(options, key=lambda o: abs(o[0]*100/o[1]-weight))  # first minimum wins, as upstream
    return canary, total-canary


def check_service_strategies(directory):
    chart = ROOT/'helm/charts/iris-service'
    good = json.loads('\n'.join(l for l in (chart/'ci/aws-values.yaml').read_text().splitlines() if not l.startswith('#')))
    assert yaml.safe_load((chart/'values.yaml').read_text())['deploymentStrategy']=='ROLLING'
    rolling = {'canary': {'maxSurge': 1, 'maxUnavailable': 0}}
    values = directory/'strategy.json'
    max_replicas = json.loads((chart/'values.schema.json').read_text())['properties']['replicas']['maximum']
    for strategy in (None, 'ROLLING', 'CANARY', 'BLUE_GREEN'):
        for replicas in range(0, max_replicas+1):
            data = copy.deepcopy(good); data['replicas'] = replicas
            if strategy: data['deploymentStrategy'] = strategy  # None: Worker without the feature flag omits the key
            values.write_text(json.dumps(data))
            docs = render(chart, values, release='demo', namespace='svc-12')
            [rollout] = [d for d in docs if d['kind']=='Rollout']
            spec = rollout['spec']
            assert spec['replicas']==replicas and spec['progressDeadlineSeconds']==good['health']['timeoutSeconds'] and spec['progressDeadlineAbort'] is True
            # Pod 종료 대기: SIGTERM waits for ALB deregistration; the app keeps its default 30s after the sleep.
            pod = spec['template']['spec']
            assert pod['containers'][0]['lifecycle']=={'preStop': {'sleep': {'seconds': 15}}} and pod['terminationGracePeriodSeconds']==45, 'Old Pods must outlive ALB deregistration without needing a shell.'
            # Canary and blue-green need two Pods; below that the chart renders the rolling update.
            effective = strategy if strategy in ('CANARY','BLUE_GREEN') and replicas >= 2 else 'ROLLING'
            assert rollout['metadata']['annotations']['iris/deployment-strategy']==effective
            # Only blue-green TargetGroups get the faster health check; per-Ingress TG annotations (LBC MergeBehavior N/A).
            notes = next(d for d in docs if d['kind']=='Ingress')['metadata']['annotations']
            fast = {k: notes.get(k) for k in ('alb.ingress.kubernetes.io/healthcheck-interval-seconds','alb.ingress.kubernetes.io/healthcheck-timeout-seconds','alb.ingress.kubernetes.io/healthy-threshold-count')}
            assert fast==({'alb.ingress.kubernetes.io/healthcheck-interval-seconds':'5','alb.ingress.kubernetes.io/healthcheck-timeout-seconds':'4','alb.ingress.kubernetes.io/healthy-threshold-count':'2'} if effective=='BLUE_GREEN' else dict.fromkeys(fast)), f'{strategy}/{replicas}: blue-green-only ALB health check annotations'
            if effective=='ROLLING':
                assert spec['strategy']==rolling, 'ROLLING keeps the 0.6.0 RollingUpdate (maxSurge 1, maxUnavailable 0).'
            elif effective=='CANARY':
                canary = spec['strategy']['canary']
                assert {k: v for k, v in canary.items() if k!='steps'}==rolling['canary'] and 'trafficRouting' not in canary
                [weight_step, pause_step] = canary['steps']
                assert pause_step=={'pause': {'duration': '60s'}}
                assert canary_replicas(replicas, weight_step['setWeight'])[0]==1, f'Canary must start exactly one new Pod at {replicas} replicas.'
            else:
                assert spec['strategy']=={'blueGreen': {'activeService': 'app', 'autoPromotionEnabled': True, 'autoPromotionSeconds': 30, 'scaleDownDelaySeconds': 30}}
                services = [d['metadata']['name'] for d in docs if d['kind']=='Service']
                ingress = next(d for d in docs if d['kind']=='Ingress')
                assert services==['app'] and ingress['spec']['rules'][0]['http']['paths'][0]['backend']['service']['name']=='app', 'activeService is the Service the Ingress routes to; there is no preview Service.'
    for bad in ('rolling', 'RECREATE', ''):
        data = copy.deepcopy(good); data['deploymentStrategy'] = bad; values.write_text(json.dumps(data))
        result = subprocess.run([HELM,'template','demo',str(chart),'-f',str(values),'--kube-version',VERSIONS['kubernetes']+'.0'],capture_output=True)
        assert result.returncode, f'Unknown deploymentStrategy must fail: {bad!r}'


def check_platform(directory, targets, bootstrap):
    chart=ROOT/'helm/charts/iris-platform'
    for fixture in sorted((chart/'ci').glob('*.yaml')):
        docs=render(chart, fixture, release='iris-platform', namespace='iris-platform')
        values=json.loads(fixture.read_text())
        assert not any(d['kind'] in {'Secret','PersistentVolumeClaim','StatefulSet','Namespace'} for d in docs)
        deployments={d['metadata']['labels']['app.kubernetes.io/component']:d for d in docs if d['kind']=='Deployment'}
        assert set(deployments)=={'api','build-worker','deploy-worker'} | ({'error-agent'} if values['errorAgent']['enabled'] else set())
        # The API reads CloudWatch build logs only when a log group is set; otherwise it gets neither key.
        api_config=next(d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='iris-platform-api')['data']
        if values['api'].get('buildLogGroup'):
            assert api_config=={'LOG_LEVEL':values['was']['logLevel'],'AWS_REGION':values['awsRegion'],'BUILD_LOG_GROUP':values['api']['buildLogGroup']}
        else:
            assert set(api_config)=={'LOG_LEVEL'}, 'An empty build log group must not become an empty BUILD_LOG_GROUP.'
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
                assert pod['terminationGracePeriodSeconds']==values['api'].get('terminationGracePeriodSeconds',30)
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
                    assert env['ARGOCD_PROBE_TOKEN']['valueFrom']['secretKeyRef']=={'name':values['deployWorker']['argocdSecret'],'key':'ARGOCD_PROBE_TOKEN','optional':True}, 'Probe reader token is optional and Deploy Worker only.'
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
        obs=policies['iris-platform-api-observability']
        assert obs['podSelector']['matchLabels']['app.kubernetes.io/component']=='api'
        assert [(r['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/name'],r['ports']) for r in obs['egress']]==[('loki',[{'protocol':'TCP','port':3100}]),('prometheus',[{'protocol':'TCP','port':9090}])], 'API reads only Loki and Prometheus in observability.'
        server=next(d for d in bootstrap if d['kind']=='Deployment' and d['metadata']['name']=='argocd-server')
        assert 8080 in [p['containerPort'] for p in server['spec']['template']['spec']['containers'][0]['ports']]
        assert argo['egress'][0]['to'][0]['podSelector']['matchLabels']['app.kubernetes.io/name']==server['spec']['template']['metadata']['labels']['app.kubernetes.io/name']
        total_cpu=sum(int(doc['spec']['template']['spec']['containers'][0]['resources']['limits']['cpu'].removesuffix('m')) for doc in [*deployments.values(),job])
        assert total_cpu<4000, 'Initial platform limits must leave quota headroom for migration/rollout.'
    import copy
    good=json.loads((chart/'ci/was-values.yaml').read_text())
    mutations=[lambda v:v.update(unknown=True),lambda v:v['api'].update(digest='latest'),lambda v:v['api'].update(host=''),lambda v:v['api'].update(buildLogGroup='/aws/codebuild/not a group'),lambda v:v['database'].update(secret=''),lambda v:v['buildWorker'].update(githubSecret=v['deployWorker']['githubSecret']),lambda v:v['network'].update(rdsSubnetCidrs=[]),lambda v:v['network'].update(albSubnetCidrs=['0.0.0.0/0']),lambda v:v['network'].update(rdsSubnetCidrs=['999.0.0.0/24']),lambda v:v['errorAgent'].update(enabled=True)]
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
    assert {d['metadata']['name'] for d in docs if d['kind'] in {'Deployment','Job','Ingress'}}=={'iris-platform-api','iris-platform-migration','iris-platform-error-agent'}, 'Only components with a digest deploy.'
    enabled=directory/'gitops-platform.json';enabled.write_text(json.dumps({'revision':'a'*40,'targets':targets,'platform':{'enabled':True},'services':{'onprem':{'enabled':False}},'onpremGateway':{'enabled':False}}))
    docs=render(ROOT/'helm/gitops', enabled, namespace='argocd')
    assert sum(d['kind']=='Application' for d in docs)==15 and sum(d['kind']=='AppProject' for d in docs)==4
    app=next(d for d in docs if d['kind']=='Application' and d['metadata']['name']=='iris-platform')
    assert app['metadata']['finalizers']==['resources-finalizer.argocd.argoproj.io'] and app['spec']['syncPolicy']['automated']=={'prune':True,'selfHeal':True}
    assert app['spec']['destination']=={'server':targets['management']['endpoint'],'namespace':'iris-platform'}
    chart_source, gitops_source = app['spec']['sources']
    assert chart_source['targetRevision']=='a'*40 and chart_source['path']=='helm/charts/iris-platform' and chart_source['helm']['ignoreMissingValueFiles']
    repos=[r for r in json.loads((ROOT/'terraform/config/platform-ecr-repositories.json').read_text()) if r != 'alb-log-collector']
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
        # Isolate the existing AWS baseline; check_onprem_gateway exercises both gateway states.
        values.write_text(json.dumps({'revision':'a'*40,'targets':targets,'services':{'onprem':{'enabled':False}},'onpremGateway':{'enabled':False}}))
        gitops = render(ROOT/'helm/gitops', values, namespace='argocd')
        # Platform is opt-in at bootstrap (GITOPS_PLATFORM_ENABLED); check_platform covers it.
        assert sum(d['kind']=='Application' for d in gitops)==14
        for app in (d for d in gitops if d['kind']=='Application' and d['metadata']['name'].endswith('-aws-load-balancer-controller')):
            # Re-sync must not rotate the LBC webhook certificate under running controllers.
            ignored = {(i['kind'],i['name']) for i in app['spec']['ignoreDifferences']}
            assert ignored=={('Secret','aws-load-balancer-tls'),('MutatingWebhookConfiguration','aws-load-balancer-webhook'),('ValidatingWebhookConfiguration','aws-load-balancer-webhook')}
            assert 'RespectIgnoreDifferences=true' in app['spec']['syncPolicy']['syncOptions']
        assert sum(d['kind']=='AppProject' for d in gitops)==3
        # Argo refuses an Application whose chart repository its AppProject does not list (InvalidSpecError).
        project_repos = {d['metadata']['name']: d['spec']['sourceRepos'] for d in gitops if d['kind']=='AppProject'}
        for app in (d for d in gitops if d['kind']=='Application'):
            for source in app['spec'].get('sources') or [app['spec']['source']]:
                assert source['repoURL'] in project_repos[app['spec']['project']], f"{app['metadata']['name']}: {source['repoURL']} is not in AppProject {app['spec']['project']} sourceRepos"
        appset = next(d for d in gitops if d['kind']=='ApplicationSet')['spec']
        chart_source, values_source = appset['template']['spec']['sources']
        assert appset['syncPolicy']['applicationsSync']=='sync', 'A removed service directory must delete its Application (Deploy Worker REMOVE).'
        assert appset['template']['metadata']['finalizers']==['resources-finalizer.argocd.argoproj.io'], 'Deleting a service Application must also delete its workload.'
        service_pins = json.loads((ROOT/'helm/gitops/values.yaml').read_text())['services']
        chart_version = yaml.safe_load((ROOT/'helm/charts/iris-service/Chart.yaml').read_text())['version']
        assert chart_source['targetRevision']==service_pins['chartRevision']=='iris-service-'+chart_version, 'The AWS ApplicationSet must pin the current iris-service chart tag.'
        # on-prem has no Argo Rollouts controller; it stays on the Deployment-based chart (docs/runbooks/argo-rollouts.md).
        assert service_pins['onprem']['chartRevision']==ONPREM_CHART_REVISION, 'on-prem must stay on the Deployment-based iris-service 0.6.0 until it runs Argo Rollouts.'
        assert values_source['ref']=='values' and chart_source['helm']['valueFiles']==['$values/{{ .path.path }}/values.yaml']
        assert appset['template']['metadata']['name']=='svc-{{ index .path.segments 1 }}' and appset['template']['spec']['destination']=={'server':targets['workload']['endpoint'],'namespace':'svc-{{ index .path.segments 1 }}'}
        services = next(d for d in gitops if d['kind']=='AppProject' and d['metadata']['name']=='iris-svc-project')['spec']
        assert {('apps', 'ReplicaSet'), ('', 'Pod')} <= {(w['group'], w['kind']) for w in services['namespaceResourceWhitelist']}, 'Argo resource trees need ReplicaSet and Pod permission for workload children and logs.'
        assert services['destinations']==[{'server':targets['workload']['endpoint'],'namespace':'svc-*'}] and services['clusterResourceWhitelist']==[{'group':'','kind':'Namespace'}]
        assert next(d for d in gitops if d['kind']=='ApplicationSet')['metadata']['name']=='iris-svc-appset' and appset['template']['spec']['project']=='iris-svc-project'
        assert [r['name'] for r in services['roles']]==['iris-deploy-reader']
        service_docs = render(ROOT/'helm/charts/iris-service', ROOT/'helm/charts/iris-service/ci/aws-values.yaml', namespace='svc-12')
        ingress = next(d for d in service_docs if d['kind']=='Ingress')
        assert not any(d['kind']=='Deployment' for d in service_docs), 'Since 0.7.0 the app runs only as a Rollout.'
        rollout = next(d for d in service_docs if d['kind']=='Rollout')
        assert rollout['apiVersion']=='argoproj.io/v1alpha1' and rollout['metadata']['name']=='app'
        assert rollout['spec']['template']['metadata']['labels']['iris/release-id']=='345' and 'iris/release-id' not in rollout['spec']['selector']['matchLabels'], 'Pods carry the release label for logs/metrics; the immutable selector must not.'
        container = rollout['spec']['template']['spec']['containers'][0]
        check_service_strategies(directory)
        env = {e['name']: e['value'] for e in container['env']}
        assert (env['IRIS_SERVICE_NAME'],env['IRIS_TARGET_NAME'],env['IRIS_DEPLOYMENT_ID'])==('my-app','aws','6789012'), 'Platform identity reaches the app; large ids must not print as 1e+06.'
        sealed = next(d for d in service_docs if d['kind']=='SealedSecret')
        assert sealed['metadata']['name']=='vars-r345' and sealed['spec']['template']['metadata']['name']=='vars-r345'
        assert set(sealed['spec']['encryptedData'])=={'DATABASE_URL','SESSION_SECRET'} and 'namespace' not in sealed['metadata'], 'Argo applies it into svc-{id}, the scope Deploy Worker sealed for.'
        assert sealed['metadata']['annotations']['argocd.argoproj.io/sync-wave']=='-1', 'The Secret must exist before the Deployment starts.'
        assert container['envFrom']==[{'secretRef':{'name':'vars-r345'}}], 'User variables come only through envFrom so the env above wins.'
        good = json.loads('\n'.join(l for l in (ROOT/'helm/charts/iris-service/ci/aws-values.yaml').read_text().splitlines() if not l.startswith('#')))
        mutations = {
            'PORT variable': lambda v: v['variables']['encryptedData'].update(PORT='AAAA'),
            'IRIS_ variable': lambda v: v['variables']['encryptedData'].update(IRIS_X='AAAA'),
            'invalid variable name': lambda v: v['variables']['encryptedData'].update({'A-B':'AAAA'}),
            'empty variables': lambda v: v['variables'].update(encryptedData={}),
            'plaintext value': lambda v: v['variables']['encryptedData'].update(A='not base64!'),
            'invalid secret name': lambda v: v['variables'].update(name='Vars_R1'),
            'missing secret name': lambda v: v['variables'].pop('name'),
            'plaintext field': lambda v: v['variables'].update(plain={'A':'b'}),
            'unknown identity field': lambda v: v['iris'].update(x=1),
            'invalid service name': lambda v: v['iris'].update(serviceName='My_App'),
            'invalid pull secret name': lambda v: v.update(imagePullSecrets=[{'name':'Iris_Ecr'}]),
            'empty pull secrets': lambda v: v.update(imagePullSecrets=[]),
            'pull secret extra field': lambda v: v.update(imagePullSecrets=[{'name':'iris-ecr-pull','namespace':'x'}]),
        }
        bad_service = directory/'bad-service.json'
        for label, change in mutations.items():
            values = copy.deepcopy(good); change(values); bad_service.write_text(json.dumps(values))
            result = subprocess.run([HELM,'template','demo',str(ROOT/'helm/charts/iris-service'),'-f',str(bad_service),'--kube-version',VERSIONS['kubernetes']+'.0'],capture_output=True)
            assert result.returncode, f'Invalid user variables must fail before deployment: {label}'
        assert 'imagePullSecrets' not in rollout['spec']['template']['spec'], 'AWS nodes pull from ECR with their own role.'
        pulled = render(ROOT/'helm/charts/iris-service', ROOT/'helm/charts/iris-service/ci/onprem-server-values.yaml', namespace='svc-12')
        assert next(d for d in pulled if d['kind']=='Rollout')['spec']['template']['spec']['imagePullSecrets']==[{'name':'iris-ecr-pull'}], 'User-registered servers pull with the namespace Secret their CronJob refreshes.'
        egress = next(d for d in service_docs if d['kind']=='NetworkPolicy' and d['metadata']['name']=='restrict-egress')['spec']
        assert egress['podSelector']=={} and egress['policyTypes']==['Egress'] and {'cidr':'0.0.0.0/0','except':['10.40.0.0/16','169.254.0.0/16']} in [t.get('ipBlock') for r in egress['egress'] for t in r['to']], 'User pods must not reach VPC (collector NLB, nodes) or link-local addresses.'
        assert ingress['metadata']['annotations']['alb.ingress.kubernetes.io/group.name']=='iris-service-external', 'All services share the external ALB group.'
        rendered_kinds = {(d['apiVersion'].rpartition('/')[0], d['kind']) for d in service_docs}
        assert rendered_kinds <= {(w['group'],w['kind']) for w in services['namespaceResourceWhitelist']}, f'iris-svc-project must allow chart kinds: {rendered_kinds}'
        allowed = {p['metadata']['name'].removeprefix('iris-addons-'): {(w['group'],w['kind']) for w in p['spec']['clusterResourceWhitelist']} for p in gitops if p['kind']=='AppProject'}
        tracking = directory/'tracking.json'
        tracking_values = {'revision':'a'*40,'targets':targets,'platform':{'enabled':True},'services':{'onprem':{'enabled':False}},'albTraffic':{'enabled':True},'onpremGateway':{'enabled':False}}
        tracking.write_text(json.dumps(tracking_values))
        pinned_apps = {d['metadata']['name']:d for d in render(ROOT/'helm/gitops', tracking, namespace='argocd') if d['kind']=='Application'}
        tracking_values['revision'] = 'main'
        tracking.write_text(json.dumps(tracking_values))
        tracking_apps = {d['metadata']['name']:d for d in render(ROOT/'helm/gitops', tracking, namespace='argocd') if d['kind']=='Application'}
        assert tracking_apps.keys()==pinned_apps.keys() and len(tracking_apps)==16
        assert {'iris-platform','iris-management-alb-log-collector'} <= tracking_apps.keys()
        infra_repo = json.loads((ROOT/'helm/gitops/values.yaml').read_text())['repoURL']
        for name, app in tracking_apps.items():
            sources = app['spec'].get('sources') or [app['spec']['source']]
            infra_sources = [s for s in sources if s['repoURL']==infra_repo]
            assert infra_sources and all(s['targetRevision']=='main' for s in infra_sources), f'{name} must track main for infrastructure sources.'
            expected = copy.deepcopy(pinned_apps[name])
            for source in expected['spec'].get('sources') or [expected['spec']['source']]:
                if source['repoURL']==infra_repo:
                    source['targetRevision'] = 'main'
            assert app==expected, f'{name}: main tracking must preserve other sources and settings.'
        bad = directory/'bad.json'; bad.write_text(json.dumps({'revision':'feature/unreviewed','targets':targets}))
        failed = subprocess.run([HELM,'template','check',str(ROOT/'helm/gitops'),'-f',str(bad)],capture_output=True)
        assert failed.returncode, 'Only main or an immutable Git SHA may pass revision validation.'
        bad.write_text(json.dumps({'revision':'a'*40,'targets':targets,'services':{'repoURL':'https://github.com/other/repo.git'}}))
        failed = subprocess.run([HELM,'template','check',str(ROOT/'helm/gitops'),'-f',str(bad)],capture_output=True)
        assert failed.returncode, 'Only the reviewed GitOps repository may feed user services.'
        onprem = directory/'onprem.json'
        onprem.write_text(json.dumps({'revision':'a'*40,'targets':targets,'services':{'onprem':{'enabled':True,'server':'https://onprem.example:6443','ingressClassName':'traefik','egressDeniedCidrs':['192.168.0.0/16']}}}))
        docs = render(ROOT/'helm/gitops', onprem, namespace='argocd')
        sets = {d['metadata']['name']: d['spec'] for d in docs if d['kind']=='ApplicationSet'}
        onprem_set = sets['iris-svc-onprem-appset']
        assert onprem_set['generators'][0]['git']['directories']==[{'path':'services/*/onprem'}]
        assert onprem_set['template']['metadata']['name']=='svc-{{ index .path.segments 1 }}', 'Deploy Worker observes svc-{id} on every target.'
        assert onprem_set['template']['spec']['destination']['server']=='https://onprem.example:6443'
        assert onprem_set['template']['spec']['sources'][0]['targetRevision']==ONPREM_CHART_REVISION, 'on-prem renders its own chart pin, not services.chartRevision.'
        assert onprem_set['template']['spec']['sources'][0]['helm']['valuesObject']=={'route':{'className':'traefik'},'networkPolicy':{'egressDeniedCidrs':['192.168.0.0/16']}}
        assert 'elbv2.k8s.aws/pod-readiness-gate-inject' not in onprem_set['template']['spec']['syncPolicy']['managedNamespaceMetadata']['labels']
        project = next(d['spec'] for d in docs if d['kind']=='AppProject' and d['metadata']['name']=='iris-svc-project')
        assert {'server':'https://onprem.example:6443','namespace':'svc-*'} in project['destinations']
        assert 'iris-svc-onprem-appset' not in {d['metadata']['name'] for d in gitops if d['kind']=='ApplicationSet'}, 'AWS-only fixture disables the on-prem ApplicationSet.'
        check_onprem_gateway(directory, targets, gitops)
        check_onprem_servers(directory, targets, gitops)
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
                namespace = 'kube-system' if name in ('aws-load-balancer-controller','metrics-server','sealed-secrets','argo-rollouts') else 'observability'
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
                if name=='opentelemetry-collector' and purpose=='workload':
                    # Agent: no listening ports (user Pods share the node), only svc-* logs, sends to the gateway NLB.
                    agent = next(d for d in docs if d['kind']=='DaemonSet')['spec']['template']['spec']['containers'][0]
                    assert not agent.get('ports') and not any(d['kind']=='Service' for d in docs)
                    relay = yaml.safe_load(next(d for d in docs if d['kind']=='ConfigMap')['data']['relay'])
                    assert set(relay['receivers'])=={'file_log','kubeletstats'} and relay['receivers']['file_log']['include']==['/var/log/pods/svc-*_*/*/*.log']
                    assert set(relay['service']['pipelines'])=={'logs','metrics'}
                    exporter = relay['exporters']['otlp_http']
                    assert re.fullmatch(r'http://iris-otel-gateway-[a-z0-9]+\.elb\.ap-northeast-2\.amazonaws\.com:4318', exporter['endpoint']), 'Set the management gateway NLB DNS (kubectl -n observability get svc opentelemetry-collector).'
                    assert exporter['auth']['authenticator']=='basicauth/client'
                if name in ('aws-load-balancer-controller','metrics-server'):
                    deployment = next(d for d in docs if d['kind']=='Deployment')
                    assert deployment['spec']['replicas']==2
                    assert deployment['spec']['template']['spec']['topologySpreadConstraints'][0]['whenUnsatisfiable']=='ScheduleAnyway'
                    if name=='metrics-server':
                        args=deployment['spec']['template']['spec']['containers'][0]['args']
                        assert '--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt' in args
                        assert '--kubelet-insecure-tls' not in args
                elif name=='sealed-secrets':
                    # workload: user variables. management: Argo cluster Secrets of user-registered on-prem servers.
                    # Same pin and settings; each cluster generates its own key.
                    assert (ROOT/'clusters/aws-dev-management/values/sealed-secrets.yaml').read_text()==(ROOT/'clusters/aws-dev-workload/values/sealed-secrets.yaml').read_text()
                    [controller] = [d for d in docs if d['kind']=='Deployment']
                    assert controller['metadata']['name']=='sealed-secrets-controller' and controller['spec']['replicas']==1
                    args = controller['spec']['template']['spec']['containers'][0]['args']
                    # Deploy Worker seals with one fixed certificate, so the key must never rotate.
                    assert args[args.index('--key-renew-period')+1]=='0'
                    assert any(d['kind']=='CustomResourceDefinition' and d['spec']['names']['kind']=='SealedSecret' for d in docs)
                elif name=='argo-rollouts':
                    assert purpose=='workload', 'Rollouts run only where user services run.'
                    [controller] = [d for d in docs if d['kind']=='Deployment']
                    assert controller['spec']['replicas']==1 and not any(d['kind']=='Service' for d in docs), 'Controller only: no dashboard or metrics Service.'
                    assert {d['spec']['names']['kind'] for d in docs if d['kind']=='CustomResourceDefinition'} >= {'Rollout','AnalysisRun','AnalysisTemplate'}
                    rules = [r for d in docs if d['kind']=='ClusterRole' and d['metadata']['name']=='argo-rollouts' for r in d['rules']]
                    # The Rollout CRD has a structural pod schema (no preserve-unknown-fields), so fields it lacks are pruned.
                    crd = next(d for d in docs if d['kind']=='CustomResourceDefinition' and d['spec']['names']['kind']=='Rollout')
                    pod = crd['spec']['versions'][0]['schema']['openAPIV3Schema']['properties']['spec']['properties']['template']['properties']['spec']['properties']
                    assert 'sleep' in pod['containers']['items']['properties']['lifecycle']['properties']['preStop']['properties'] and 'terminationGracePeriodSeconds' in pod, 'iris-service preStop sleep would be pruned by this Rollout CRD.'
                    assert not any(g.endswith(('istio.io','elbv2.k8s.aws','traefik.io','gateway.networking.k8s.io')) for r in rules for g in r['apiGroups']), 'No traffic router RBAC: canary is pod-ratio only.'
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
                    # RWO PVC needs Recreate; a chart-generated admin Secret re-randomizes on every render and rolls Grafana.
                    grafana=next(d for d in docs if d['kind']=='Deployment' and d['metadata']['name']=='monitoring-grafana')
                    assert grafana['spec']['strategy']=={'type':'Recreate'}
                    assert not any(d['kind']=='Secret' and d['metadata']['name']=='monitoring-grafana' for d in docs), 'Grafana admin must come from the operator-managed grafana-admin Secret.'
            missing = {group_kind(d) for d in rendered if d['kind'] in CLUSTER_SCOPED} - allowed[purpose]
            assert not missing, f'{purpose} AppProject must allow cluster-scoped kinds: {sorted(missing)}'
    # Preserve latest main's Deploy Worker contract fixtures; legacy examples
    # contain removed fields and are intentionally not inputs to iris-service.
    chart=ROOT/'helm/charts/iris-service'
    for values in sorted((chart/'ci').glob('*.yaml')):
        docs=render(chart,values,release='demo',namespace='iris-check')
        kinds={d['kind'] for d in docs}
        assert {'Rollout','Service','Ingress'} <= kinds and 'Deployment' not in kinds
        has_variables='"variables"' in values.read_text()
        assert has_variables==('SealedSecret' in kinds)==any('envFrom' in c for d in docs if d['kind']=='Rollout' for c in d['spec']['template']['spec']['containers']), 'SealedSecret and envFrom exist only when variables are set.'
    print('Pinned charts/images, GitOps schema, baseline, platform TLS/migration/credentials, storage and replicas: passed. No runtime deployment tested.')


if __name__=='__main__':main()
