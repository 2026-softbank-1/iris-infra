#!/usr/bin/env python3
"""Explicit AWS target validation, SSM tunnels and local Kubernetes operations.

Terraform commands here are output-only. No AWS apply, account upgrade, Git
publication, tool installation or image publication is performed by this tool.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('aws-dev-management', 'aws-dev-workload')
REPO = 'https://github.com/2026-softbank-1/iris-infra.git'
SSH_REPO = 'git@github.com:2026-softbank-1/iris-infra.git'
# User-service values (ADR 0002); Argo reads it with the same read-only credential.
GITOPS_REPO = 'https://github.com/2026-softbank-1/iris-gitops-environments.git'
GITOPS_SSH_REPO = 'git@github.com:2026-softbank-1/iris-gitops-environments.git'


def need(condition, message):
    if not condition:
        raise ValueError(message)


def require(*names):
    for name in names:
        need(shutil.which(name), f'Required tool missing: {name}; see docs/runbooks/eks-access.md.')


def run(args, *, input=None, timeout=60, capture=True, check=True):
    result = subprocess.run(args, input=input, text=True, capture_output=capture, timeout=timeout)
    if check and result.returncode:
        # Do not expose stdin, token/Secret output, or command arguments.
        raise RuntimeError(f'{Path(args[0]).name} {args[1] if len(args)>1 else ""} failed (exit {result.returncode}).')
    return result.stdout if capture else result.returncode


def aws(service, action, *args, region=None):
    command = ['aws', service, action, *args, '--output', 'json', '--no-cli-pager']
    if region:
        command += ['--region', region]
    return json.loads(run(command))


def account():
    expected = os.environ.get('AWS_ACCOUNT_ID', '')
    need(re.fullmatch(r'[0-9]{12}', expected) and expected != '000000000000', 'Set AWS_ACCOUNT_ID explicitly.')
    identity = aws('sts', 'get-caller-identity')
    need(identity['Account'] == expected, 'AWS account mismatch; no operation performed.')
    return expected, identity['Arn']


def validate_target(target, target_id, expected):
    need(target_id in TARGETS and target['id'] == target_id, 'Target ID mismatch.')
    need(target['account_id'] == expected, 'Terraform target account mismatch.')
    need(target['region'] == os.environ.get('AWS_REGION', 'ap-northeast-2'), 'Target region mismatch.')
    for field,prefix in [('vpc_id','vpc'),('ssm_bridge_instance_id','i'),('api_security_group_id','sg'),('ssm_bridge_security_group_id','sg'),('node_security_group_id','sg'),('workload_api_target_security_group_id','sg'),('management_api_source_security_group_id','sg'),('cluster_security_group_id','sg')]:
        need(re.fullmatch(prefix+r'-[a-f0-9]{8,17}',target[field]),'Invalid target resource ID: '+field)
    need(target['kube_context']=='iris-dev-'+target_id.removeprefix('aws-dev-'),'Unexpected kube context.')
    need(set(target['subnet_ids_by_az'])=={'ap-northeast-2a','ap-northeast-2c'} and len(set(target['subnet_ids_by_az'].values()))==2 and all(re.fullmatch(r'subnet-[a-f0-9]{8,17}',v) for v in target['subnet_ids_by_az'].values()),'Unexpected AZ/subnet IDs.')
    name = target['name']
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', name), 'Invalid cluster name.')
    need(target['arn'] == f'arn:aws:eks:{target["region"]}:{expected}:cluster/{name}', 'Cluster ARN mismatch.')
    need(re.fullmatch(r'https://[A-Za-z0-9.-]+\.eks\.amazonaws\.com', target['endpoint']), 'Invalid EKS endpoint hostname.')
    need(bool(base64.b64decode(target['ca_data'], validate=True)), 'Missing CA data.')
    need(len(target['subnet_ids_by_az']) == 2, 'Expected two AZ subnets.')
    need(target['api_port'] == (10443 if target_id.endswith('management') else 11443), 'Unexpected tunnel port.')
    for principal in [target['operator_principal_arn'], *target.get('additional_operator_principal_arns', [])]:
        need(re.fullmatch(rf'arn:aws:iam::{expected}:(role|user)/[A-Za-z0-9/_+=,.@-]+', principal), 'Invalid operator principal.')
    return target


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic, private generated data, even if the operator's umask is permissive.
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    temporary.replace(path)


def export_targets():
    require('aws', 'terraform')
    expected, _ = account()
    targets = {}
    for target_id in TARGETS:
        purpose = target_id.removeprefix('aws-dev-')
        target = json.loads(run(['terraform', f'-chdir={ROOT}/terraform/environments/aws/dev/{purpose}', 'output', '-json', 'target']))
        targets[target_id] = validate_target(target, target_id, expected)
    payload = {'schema_version': 1, 'targets': targets}
    write_private(ROOT / '.generated/targets.json', payload)
    print('Exported two validated infrastructure targets to .generated/targets.json.')
    return payload


def load_target(target_id):
    require('aws')
    need(target_id in TARGETS, 'AWS target required; local-workload remains scaffold.')
    expected, caller = account()
    path = ROOT / '.generated/targets.json'
    need(path.is_file(), 'Run make export-targets after initializing the two EKS backends.')
    payload = json.loads(path.read_text())
    need(payload['schema_version'] == 1, 'Unsupported target schema.')
    target = validate_target(payload['targets'][target_id], target_id, expected)
    principals = [target['operator_principal_arn'], *target.get('additional_operator_principal_arns', [])]
    # STS assumed-role sessions omit an IAM role path, but role names are unique.
    matches = any(caller == principal or (
        ':role/' in principal and caller.startswith(
            f'arn:aws:sts::{expected}:assumed-role/{principal.rsplit("/", 1)[-1]}/'))
        for principal in principals)
    need(matches, 'Current AWS principal does not match an explicit operator Access Entry.')
    return target


def preflight(target):
    region = target['region']
    cluster = aws('eks', 'describe-cluster', '--name', target['name'], region=region)['cluster']
    vpc = cluster['resourcesVpcConfig']
    need(cluster['status'] == 'ACTIVE' and cluster['version'] == '1.35', 'EKS must be ACTIVE on 1.35.')
    need(cluster['arn'] == target['arn'] and cluster['endpoint'] == target['endpoint'] and cluster['certificateAuthority']['data'] == target['ca_data'], 'EKS identity, endpoint or CA changed; export targets again.')
    need(vpc['vpcId'] == target['vpc_id'] and vpc['endpointPrivateAccess'] and not vpc['endpointPublicAccess'], 'Unexpected VPC or public API configuration.')
    need(set(vpc['subnetIds']) == set(target['subnet_ids_by_az'].values()), 'EKS subnet contract mismatch.')
    need(target['api_security_group_id'] in vpc['securityGroupIds'], 'Expected API SG is not attached.')
    if target['id'].endswith('workload'):
        need(target['workload_api_target_security_group_id'] in vpc['securityGroupIds'], 'Workload API target SG is not attached.')
    subnets = aws('ec2', 'describe-subnets', '--subnet-ids', *target['subnet_ids_by_az'].values(), region=region)['Subnets']
    need(len(subnets) == 2 and all(s['VpcId'] == target['vpc_id'] and not s['MapPublicIpOnLaunch'] and target['subnet_ids_by_az'].get(s['AvailabilityZone']) == s['SubnetId'] for s in subnets), 'Private subnet/AZ contract mismatch.')
    bridge = aws('ec2', 'describe-instances', '--instance-ids', target['ssm_bridge_instance_id'], region=region)['Reservations'][0]['Instances'][0]
    need(bridge['VpcId'] == target['vpc_id'] and bridge['State']['Name'] == 'running' and not bridge.get('PublicIpAddress'), 'SSM bridge must be running in the same private VPC.')
    need(target['ssm_bridge_security_group_id'] in {g['GroupId'] for g in bridge['SecurityGroups']}, 'Bridge SG mismatch.')
    path_group = target['workload_api_target_security_group_id'] if target['id'].endswith('workload') else target['management_api_source_security_group_id']
    groups = aws('ec2', 'describe-security-groups', '--group-ids', target['api_security_group_id'], target['ssm_bridge_security_group_id'], path_group, region=region)['SecurityGroups']
    by_id = {g['GroupId']: g for g in groups}
    need(not by_id[target['ssm_bridge_security_group_id']]['IpPermissions'], 'Bridge must have no inbound rules.')
    api_rules = by_id[target['api_security_group_id']]['IpPermissions']
    need(any(rule.get('IpProtocol') == 'tcp' and rule.get('FromPort') == 443 and rule.get('ToPort') == 443 and any(pair['GroupId'] == target['ssm_bridge_security_group_id'] for pair in rule.get('UserIdGroupPairs', [])) for rule in api_rules), 'Bridge-to-private-API TCP 443 rule missing.')
    path_rules = by_id[path_group]['IpPermissions'] if target['id'].endswith('workload') else by_id[path_group]['IpPermissionsEgress']
    peer = target['management_api_source_security_group_id'] if target['id'].endswith('workload') else target['workload_api_target_security_group_id']
    need(any(rule.get('IpProtocol')=='tcp' and rule.get('FromPort')==443 and rule.get('ToPort')==443 and any(pair['GroupId']==peer for pair in rule.get('UserIdGroupPairs',[])) for rule in path_rules),'Management-to-workload TCP 443 SG reference missing.')
    if target['id'].endswith('management'):
        reservations = aws('ec2','describe-instances','--filters',f'Name=instance.group-id,Values={target["node_security_group_id"]}','Name=instance-state-name,Values=running',region=region)['Reservations']
        nodes = [i for r in reservations for i in r['Instances']]
        need(len(nodes)==2 and all(target['management_api_source_security_group_id'] in {g['GroupId'] for g in i['SecurityGroups']} and not i.get('PublicIpAddress') for i in nodes),'Management node source SG/private-IP attachment mismatch.')
    info = aws('ssm', 'describe-instance-information', '--filters', f'Key=InstanceIds,Values={target["ssm_bridge_instance_id"]}', region=region)['InstanceInformationList']
    need(len(info) == 1 and info[0]['PingStatus'] == 'Online', 'SSM bridge is not Online.')
    version = tuple(int(x) for x in info[0]['AgentVersion'].split('.'))
    need(version >= (3, 1, 1374, 0), 'SSM Agent >=3.1.1374.0 required for remote-host forwarding.')
    return target


def kubeconfig(target):
    context = target['kube_context']
    exec_args = ['eks', 'get-token', '--cluster-name', target['name'], '--region', target['region'], '--output', 'json']
    if os.environ.get('AWS_PROFILE'):
        exec_args += ['--profile', os.environ['AWS_PROFILE']]
    config = {'apiVersion':'v1', 'kind':'Config', 'clusters':[{'name':context, 'cluster':{
        'server':f'https://127.0.0.1:{target["api_port"]}',
        'tls-server-name':target['endpoint'].removeprefix('https://'),
        'certificate-authority-data':target['ca_data']}}],
        'users':[{'name':context,'user':{'exec':{'apiVersion':'client.authentication.k8s.io/v1beta1','command':'aws','args':exec_args,'interactiveMode':'Never'}}}],
        'contexts':[{'name':context,'context':{'cluster':context,'user':context}}], 'current-context':context}
    path = ROOT / f'.generated/kubeconfig-{target["id"]}.json'
    write_private(path, config)
    return path


def free_port(port):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))


def tunnel(target):
    require('session-manager-plugin')
    preflight(target)
    free_port(target['api_port'])
    config = kubeconfig(target)
    print(f'Validated {target["id"]}; kubeconfig={config}; local API port={target["api_port"]}. Keep this session open.')
    params = {'host':[target['endpoint'].removeprefix('https://')], 'portNumber':['443'], 'localPortNumber':[str(target['api_port'])]}
    return run(['aws','ssm','start-session','--region',target['region'],'--target',target['ssm_bridge_instance_id'],'--document-name','AWS-StartPortForwardingSessionToRemoteHost','--parameters',json.dumps(params),'--no-cli-pager'], capture=False, timeout=None)


def tls_check(target):
    context = ssl.create_default_context(cadata=base64.b64decode(target['ca_data']).decode('ascii'))
    with socket.create_connection(('127.0.0.1',target['api_port']),timeout=10) as connection:
        with context.wrap_socket(connection, server_hostname=target['endpoint'].removeprefix('https://')):
            pass


def kubectl(target, *args, **kwargs):
    return run(['kubectl','--kubeconfig',str(kubeconfig(target)),'--context',target['kube_context'],*args],**kwargs)


def ready(target):
    require('kubectl')
    client = json.loads(run(['kubectl','version','--client','-o','json']))['clientVersion']
    need(client['major']=='1' and client['minor'].rstrip('+') == '35', 'Use kubectl 1.35 for this reviewed stack.')
    preflight(target)
    tls_check(target)
    need(kubectl(target,'auth','can-i','*','*','--all-namespaces').strip()=='yes','Operator Kubernetes admin authorization missing.')
    need(kubectl(target,'get','--raw','/readyz').strip()=='ok','Kubernetes API is not ready.')
    nodes = json.loads(kubectl(target,'get','nodes','-o','json'))['items']
    need(len(nodes)==2 and {n['metadata']['labels']['topology.kubernetes.io/zone'] for n in nodes} == set(target['subnet_ids_by_az']), 'Expected one node in each reviewed AZ.')
    need(all(n['status']['allocatable']['pods']=='35' and any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions']) for n in nodes), 'Nodes must be Ready with allocatable.pods=35; refusing bootstrap.')
    ds = json.loads(kubectl(target,'get','daemonset','aws-node','-n','kube-system','-o','json'))
    cni = next(c for c in ds['spec']['template']['spec']['containers'] if c['name']=='aws-node')
    env={x['name']:x.get('value') for x in cni.get('env',[])}
    need(env.get('ENABLE_PREFIX_DELEGATION')=='true' and any(c['name']=='aws-eks-nodeagent' for c in ds['spec']['template']['spec']['containers']), 'CNI prefix delegation/network policy agent missing.')
    return target


def apply(target, objects):
    # Secret payloads exist only in memory/stdin, never in generated files/logs.
    return kubectl(target,'apply','-f','-',input=json.dumps({'apiVersion':'v1','kind':'List','items':objects}))


def wait_for(check, message, timeout=600):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if check():return
        time.sleep(5)
    raise RuntimeError(message)


def bootstrap():
    require('helm','git')
    platform_flag=os.environ.get('GITOPS_PLATFORM_ENABLED','0')
    need(platform_flag in ('0','1'),'GITOPS_PLATFORM_ENABLED must be 0 or 1.')
    platform_enabled=platform_flag=='1'
    sha=os.environ.get('GITOPS_REVISION','')
    need(re.fullmatch(r'[0-9a-f]{40}([0-9a-f]{24})?',sha),'Set GITOPS_REVISION to the reviewed commit merged into main.')
    need(run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip()==sha,'Check out GITOPS_REVISION before bootstrap.')
    need(not run(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=all','--','helm','scripts','terraform','clusters']).strip(),'Bootstrap source must be a clean reviewed checkout.')
    token_path=os.environ.get('ARGOCD_GIT_TOKEN_FILE')
    key_path=os.environ.get('ARGOCD_GIT_SSH_KEY_FILE')
    need(bool(token_path) != bool(key_path),'Provide exactly one private Git token file or SSH key file.')
    credential=Path(token_path or key_path).read_text().strip()
    need(credential,'Empty Git credential file.')
    if key_path:
        need('PRIVATE KEY' in credential,'Expected a Git SSH private key.')
    versions=json.loads((ROOT/'helm/versions.json').read_text())
    need(run(['helm','version','--short']).strip().startswith('v'+versions['helm']+'+'),'Use the pinned Helm version from helm/versions.json.')
    targets={id.removeprefix('aws-dev-'):ready(load_target(id)) for id in TARGETS}
    management=targets['management']
    if platform_enabled:
        chart=str(ROOT/'helm/charts/iris-platform')
        platform_values=str(ROOT/'clusters/aws-dev-management/values/platform.yaml')
        version=versions['kubernetes']+'.0'
        run(['helm','lint','--strict',chart,'-f',platform_values,'--kube-version',version,'--namespace','iris-platform'])
        run(['helm','template','iris-platform',chart,'-f',platform_values,'--kube-version',version,'--namespace','iris-platform'])
    # All inputs, both APIs, CA/RBAC, nodes and CNI are checked before any write.
    kubectl(management,'create','namespace','argocd','--dry-run=client','-o','json')
    ns={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'argocd'}}
    apply(management,[ns])
    run(['helm','repo','add','iris-argocd',versions['charts']['argo-cd']['repo']],timeout=120)
    run(['helm','dependency','build',str(ROOT/'helm/bootstrap')],timeout=300)
    run(['helm','upgrade','--install','argocd',str(ROOT/'helm/bootstrap'),'--namespace','argocd','--kubeconfig',str(kubeconfig(management)),'--kube-context',management['kube_context'],'--wait','--timeout','15m'],timeout=960)
    repo_url=SSH_REPO if key_path else REPO
    gitops_url=GITOPS_SSH_REPO if key_path else GITOPS_REPO
    objects=[]
    for name,url in (('iris-infra-repository',repo_url),('iris-gitops-environments-repository',gitops_url)):
        data={'type':'git','url':url}
        if key_path:data['sshPrivateKey']=credential
        else:data.update(username='x-access-token',password=credential)
        objects.append({'apiVersion':'v1','kind':'Secret','metadata':{'name':name,'namespace':'argocd','labels':{'argocd.argoproj.io/secret-type':'repository'}},'type':'Opaque','stringData':data})
    for purpose,target in targets.items():
        auth={'awsAuthConfig':{'clusterName':target['name'],'roleARN':target['argocd_role_arn']},'tlsClientConfig':{'insecure':False,'caData':target['ca_data']}}
        objects.append({'apiVersion':'v1','kind':'Secret','metadata':{'name':f'iris-{purpose}-cluster','namespace':'argocd','labels':{'argocd.argoproj.io/secret-type':'cluster'}},'type':'Opaque','stringData':{'name':target['name'],'server':target['endpoint'],'config':json.dumps(auth)}})
    values={'repoURL':repo_url,'revision':sha,'services':{'repoURL':gitops_url},'platform':{'enabled':platform_enabled},'targets':{p:{k:t[k] for k in ('endpoint','name','region','vpc_id')} for p,t in targets.items()}}
    objects += [
        {'apiVersion':'argoproj.io/v1alpha1','kind':'AppProject','metadata':{'name':'iris-root','namespace':'argocd'},'spec':{'sourceRepos':[repo_url],'destinations':[{'server':management['endpoint'],'namespace':'argocd'}],'clusterResourceWhitelist':[],'namespaceResourceWhitelist':[{'group':'argoproj.io','kind':'Application'},{'group':'argoproj.io','kind':'AppProject'},{'group':'argoproj.io','kind':'ApplicationSet'}]}},
        {'apiVersion':'argoproj.io/v1alpha1','kind':'Application','metadata':{'name':'iris-addons','namespace':'argocd'},'spec':{'project':'iris-root','source':{'repoURL':repo_url,'targetRevision':sha,'path':'helm/gitops','helm':{'valuesObject':values}},'destination':{'server':management['endpoint'],'namespace':'argocd'},'syncPolicy':{'automated':{'prune':False,'selfHeal':True},'syncOptions':['ServerSideApply=true']}}}]
    apply(management,objects)
    addons=('baseline','aws-load-balancer-controller','metrics-server','kube-prometheus-stack')
    addons+=('opentelemetry-collector',)
    # Loki stores logs in management only (helm/gitops applications.yaml).
    names=['iris-addons']+[f'iris-{p}-{a}' for p in targets for a in addons+(('loki',) if p=='management' else ())]
    def synced():
        apps=json.loads(kubectl(management,'get','applications','-n','argocd','-o','json'))['items']
        statuses={a['metadata']['name']:a.get('status',{}) for a in apps}
        addons_healthy=all(statuses.get(n,{}).get('sync',{}).get('status')=='Synced' and statuses.get(n,{}).get('health',{}).get('status')=='Healthy' for n in names[1:])
        root=statuses.get('iris-addons',{})
        root_ready=root.get('sync',{}).get('status')=='Synced' and (platform_enabled or root.get('health',{}).get('status')=='Healthy')
        return addons_healthy and root_ready and (not platform_enabled or 'iris-platform' in statuses)
    wait_for(synced,'Addon sync/health timeout; inspect Applications without exposing repository Secrets.',1800)
    if platform_enabled:
        print('Root is Synced; all addons are Synced/Healthy; iris-platform Application exists. Platform requires a manual full sync; deployment and ECR pull are not verified.')
    else:
        print('ArgoCD and eight addon Applications are Synced/Healthy. Platform ECR pull: not verified.')


def port_forward(target, service, port):
    free_port(port)
    process=subprocess.Popen(['kubectl','--kubeconfig',str(kubeconfig(target)),'--context',target['kube_context'],'-n','observability','port-forward',f'service/{service}',f'{port}:9090','--address','127.0.0.1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return process


def monitoring(target):
    metrics=json.loads(kubectl(target,'get','--raw','/apis/metrics.k8s.io/v1beta1/nodes'))
    need(len(metrics['items'])==2,'metrics-server node collection incomplete.')
    pods=json.loads(kubectl(target,'get','pods','-n','observability','-o','json'))['items']
    need(pods and all(p['status'].get('phase')=='Succeeded' or any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[])) for p in pods),'Monitoring Pods are not all Ready.')
    claims=json.loads(kubectl(target,'get','pvc','-n','observability','-o','json'))['items']
    need(len(claims)>=3 and all(c['status']['phase']=='Bound' for c in claims),'Monitoring EBS claims are not Bound.')
    process=port_forward(target,'monitoring-prometheus',19090)
    try:
        wait_for(lambda: socket_open(19090), 'Prometheus port-forward did not start.',30)
        with urllib.request.urlopen('http://127.0.0.1:19090/api/v1/targets?state=active',timeout=15) as response:
            targets=json.load(response)['data']['activeTargets']
        jobs={t['labels'].get('job','') for t in targets if t['health']=='up'}
        for expected in ('kubelet','node-exporter','kube-state-metrics','apiserver'):
            need(any(expected in job for job in jobs),f'Missing healthy Prometheus job: {expected}.')
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()


def socket_open(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1',port))==0


def smoke(target):
    ready(target)
    for addon in ('vpc-cni','coredns','kube-proxy','eks-pod-identity-agent','aws-ebs-csi-driver'):
        status=aws('eks','describe-addon','--cluster-name',target['name'],'--addon-name',addon,region=target['region'])['addon']['status']
        need(status=='ACTIVE',f'Addon not ACTIVE: {addon}.')
    monitoring(target)
    management=ready(load_target('aws-dev-management'))
    apps=json.loads(kubectl(management,'get','applications','-n','argocd','-o','json'))['items']
    matching=[a for a in apps if a['metadata']['name'].startswith('iris-'+target['id'].removeprefix('aws-dev-')+'-')]
    need(len(matching)==4 and all(a.get('status',{}).get('sync',{}).get('status')=='Synced' and a.get('status',{}).get('health',{}).get('status')=='Healthy' for a in matching),'Target GitOps Applications are not Synced/Healthy.')
    print(f'{target["id"]}: API, two AZ nodes, addons, storage, metrics and GitOps checks passed. Platform ECR pull: not verified.')


def exercise(target, image_ref=None, drain_node=None):
    """Opt-in transient resources; drain is a separate explicit operator choice."""
    if image_ref:
        prefix=f'{target["account_id"]}.dkr.ecr.{target["region"]}.amazonaws.com/iris/'
        repositories=json.loads((ROOT/'terraform/config/platform-ecr-repositories.json').read_text())
        need(re.fullmatch(r'.+@sha256:[a-f0-9]{64}',image_ref) and image_ref.rsplit('@',1)[0] in {prefix+name for name in repositories},'Supply an existing platform ECR digest in this account/region.')
    nodes=json.loads(kubectl(target,'get','nodes','-o','json'))['items']
    if drain_node:
        need(target['id']=='aws-dev-workload','Failover exercise is limited to the workload cluster.')
        need(drain_node in {n['metadata']['name'] for n in nodes},'Drain node is not in this validated cluster.')
        need(all(not n['spec'].get('unschedulable',False) for n in nodes),'Both nodes must initially be schedulable.')
    lock=json.loads((ROOT/'helm/images.lock.json').read_text())['verification_images']
    namespace='iris-verification-'+str(os.getpid())
    created=[]
    cordoned=False
    def pod(name,namespace=namespace,service_account=None,image=None,command=None):
        spec={'restartPolicy':'Never','containers':[{'name':'check','image':image or lock['python'],'command':command or ['python','-c','import time; time.sleep(3600)'],'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'200m','memory':'128Mi'}}}]}
        if service_account:spec['serviceAccountName']=service_account
        obj={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':namespace,'labels':{'iris.dev/verification':namespace}},'spec':spec}
        created.append((namespace,name))
        return obj
    def complete(name,namespace):
        def finished():
            status=json.loads(kubectl(target,'get','pod',name,'-n',namespace,'-o','json'))['status']
            need(status.get('phase')!='Failed','Verification Pod failed; inspect its status/events locally.')
            return status.get('phase')=='Succeeded'
        wait_for(finished,'Verification Pod completion timed out.',300)
    def exec_python(name,code):
        return kubectl(target,'exec',name,'-n',namespace,'--','python','-c',code)
    try:
        apply(target,[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace,'labels':{'pod-security.kubernetes.io/enforce':'baseline'}}}])
        server=pod('server',command=['python','-m','http.server','8080'])
        application_ns='iris-platform' if target['id'].endswith('management') else 'iris-apps'
        application_probe=pod('iris-egress-'+str(os.getpid()),application_ns,command=['python','-c',"import socket,urllib.request; socket.gethostbyname('kubernetes.default.svc'); urllib.request.urlopen('https://aws.amazon.com',timeout=15).read(1)"])
        apply(target,[application_probe]);complete(application_probe['metadata']['name'],application_ns)
        client=pod('client')
        apply(target,[server,client,{'apiVersion':'v1','kind':'Service','metadata':{'name':'server','namespace':namespace},'spec':{'selector':{'iris.dev/check':'server'},'ports':[{'port':8080,'targetPort':8080}]}}])
        kubectl(target,'label','pod','server','-n',namespace,'iris.dev/check=server')
        kubectl(target,'wait','--for=condition=Ready','pod/server','pod/client','-n',namespace,'--timeout=180s',timeout=200)
        # DNS, same-VPC transport and NAT HTTPS are positively checked first.
        exec_python('client',"import socket,urllib.request; socket.gethostbyname('kubernetes.default.svc'); urllib.request.urlopen('http://server:8080',timeout=5).read(); urllib.request.urlopen('https://aws.amazon.com',timeout=15).read(1)")
        apply(target,[{'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'deny-server','namespace':namespace},'spec':{'podSelector':{'matchLabels':{'iris.dev/check':'server'}},'policyTypes':['Ingress'],'ingress':[]}}])
        # Allow CNI policy propagation; test only the controlled in-cluster server.
        time.sleep(10)
        exec_python('client',"import socket; s=socket.socket(); s.settimeout(5); result=s.connect_ex(('server',8080)); assert result != 0, 'NetworkPolicy did not block traffic'")
        claim={'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':'storage','namespace':namespace},'spec':{'storageClassName':'gp3','accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':'1Gi'}}}}
        storage=pod('storage',command=['python','-c',"from pathlib import Path; p=Path('/data/probe'); p.write_text('iris'); assert p.read_text()=='iris'"])
        storage['spec']['volumes']=[{'name':'data','persistentVolumeClaim':{'claimName':'storage'}}]
        storage['spec']['containers'][0]['volumeMounts']=[{'name':'data','mountPath':'/data'}]
        apply(target,[claim,storage]);complete('storage',namespace)
        pvc=json.loads(kubectl(target,'get','pvc','storage','-n',namespace,'-o','json'))
        need(pvc['status']['phase']=='Bound','Sample EBS claim failed to bind.')
        pv=json.loads(kubectl(target,'get','pv',pvc['spec']['volumeName'],'-o','json'))
        volume=aws('ec2','describe-volumes','--volume-ids',pv['spec']['csi']['volumeHandle'],region=target['region'])['Volumes'][0]
        need(volume['Encrypted'] and volume['VolumeType']=='gp3','Sample volume must be encrypted gp3.')
        # Reuse existing associated SA; no new IAM grant or association is created.
        identities=[('kube-system','aws-load-balancer-controller',target['load_balancer_controller_role_arn'])]
        if target['id'].endswith('management'):
            role=target['argocd_management_role_arn']
            identities += [('argocd',sa,role) for sa in ('argocd-server','argocd-application-controller','argocd-applicationset-controller')]
        for number,(ns,sa,role) in enumerate(identities):
            name=f'iris-identity-{os.getpid()}-{number}'
            obj=pod(name,ns,sa,lock['aws_cli'],['aws','sts','get-caller-identity','--query','Arn','--output','text','--region',target['region']])
            apply(target,[obj]);complete(name,ns)
            arn=kubectl(target,'logs',name,'-n',ns).strip()
            need(arn.startswith(f'arn:aws:sts::{target["account_id"]}:assumed-role/{role.rsplit("/",1)[-1]}/'),'Pod Identity returned an unexpected role.')
        if image_ref:
            obj=pod('platform-image',image=image_ref,command=None)
            # Override entrypoint without relying on shell/tools inside the image.
            obj['spec']['containers'][0].pop('command')
            apply(target,[obj])
            def pulled():
                status=json.loads(kubectl(target,'get','pod','platform-image','-n',namespace,'-o','json'))['status']
                # OCI indexes resolve to an architecture-specific imageID digest.
                return any(re.search(r'sha256:[a-f0-9]{64}$',s.get('imageID','')) for s in status.get('containerStatuses',[]))
            wait_for(pulled,'Platform ECR digest pull was not confirmed.',300)
            print('Platform ECR digest pull: verified (application readiness is outside this test).')
        else:print('Platform ECR digest pull: not verified; no existing digest supplied.')
        # Storage exercises are finished before evicting any node.
        kubectl(target,'delete','pod','storage','-n',namespace,'--wait=true','--timeout=120s',timeout=140)
        if drain_node:
            for name in ('server','client','platform-image'):
                kubectl(target,'delete','pod',name,'-n',namespace,'--ignore-not-found','--wait=true','--timeout=120s',timeout=140)
            deployment={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'stateless','namespace':namespace},'spec':{'replicas':2,'selector':{'matchLabels':{'iris.dev/check':'stateless'}},'template':{'metadata':{'labels':{'iris.dev/check':'stateless'}},'spec':{'topologySpreadConstraints':[{'maxSkew':1,'topologyKey':'topology.kubernetes.io/zone','whenUnsatisfiable':'ScheduleAnyway','labelSelector':{'matchLabels':{'iris.dev/check':'stateless'}}}],'containers':[{'name':'server','image':lock['python'],'command':['python','-m','http.server','8080'],'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'200m','memory':'128Mi'}},'readinessProbe':{'httpGet':{'path':'/','port':8080},'periodSeconds':3}}]}}}}
            apply(target,[deployment]);kubectl(target,'rollout','status','deployment/stateless','-n',namespace,'--timeout=180s',timeout=200)
            cordoned=True
            kubectl(target,'drain',drain_node,'--ignore-daemonsets','--delete-emptydir-data','--timeout=300s',timeout=320)
            kubectl(target,'rollout','status','deployment/stateless','-n',namespace,'--timeout=300s',timeout=320)
            pods=json.loads(kubectl(target,'get','pods','-n',namespace,'-l','iris.dev/check=stateless','-o','json'))['items']
            need(sum(p['spec'].get('nodeName')!=drain_node and any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[])) for p in pods if not p['metadata'].get('deletionTimestamp'))==2,'Two stateless replicas did not recover on the surviving node.')
            print('Explicit node drain: two stateless replicas recovered. AZ-bound EBS services are not HA.')
        print('DNS, NAT HTTPS, NetworkPolicy denial, encrypted gp3 write/read and Pod Identity verified.')
    finally:
        # Cleanup runs on failure too; attempt each independently.
        errors=[]
        if cordoned:
            try:kubectl(target,'uncordon',drain_node)
            except (RuntimeError,OSError,subprocess.TimeoutExpired):errors.append('uncordon '+drain_node)
        for ns,name in created:
            if ns==namespace:continue
            try:kubectl(target,'delete','pod',name,'-n',ns,'--ignore-not-found','--wait=false')
            except (RuntimeError,OSError,subprocess.TimeoutExpired):errors.append('Pod '+ns+'/'+name)
        try:kubectl(target,'delete','namespace',namespace,'--ignore-not-found','--wait=true','--timeout=180s',timeout=200)
        except (RuntimeError,OSError,subprocess.TimeoutExpired):errors.append('Namespace '+namespace)
        if errors:
            print('Manual cleanup needed: '+', '.join(errors),file=sys.stderr)
            raise RuntimeError('Verification cleanup was incomplete.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('export','preflight','tunnel','bootstrap','smoke'))
    parser.add_argument('target',nargs='?',choices=(*TARGETS,'local-workload'))
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--image-ref')
    parser.add_argument('--drain-node')
    args=parser.parse_args()
    need(args.action=='smoke' or not (args.exercise or args.image_ref or args.drain_node),'Exercise options are allowed only with smoke.')
    need(args.exercise or not (args.image_ref or args.drain_node),'Image/drain options require explicit --exercise.')
    if args.target=='local-workload':raise ValueError('local-workload remains scaffold; no deployment performed.')
    if args.action=='export':export_targets();return
    if args.action=='bootstrap':
        need(args.target=='aws-dev-management','Bootstrap is initiated on management for both clusters.')
        bootstrap();return
    need(args.target in TARGETS,'Specify an AWS target.')
    target=load_target(args.target)
    if args.action=='preflight':preflight(target);print(f'{target["id"]}: AWS endpoint/VPC/SG/SSM preflight passed.')
    elif args.action=='tunnel':sys.exit(tunnel(target))
    else:
        smoke(target)
        if args.exercise:exercise(target,args.image_ref,args.drain_node)
        else:need(not args.image_ref and not args.drain_node,'Image/drain options require explicit --exercise.')


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,KeyError,OSError,subprocess.TimeoutExpired) as error:
        # Messages never include Secret payloads or raw state/plan output.
        print(f'EKS operation stopped: {error}',file=sys.stderr)
        sys.exit(1)
