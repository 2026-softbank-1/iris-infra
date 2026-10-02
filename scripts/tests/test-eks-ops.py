#!/usr/bin/env python3
"""No AWS calls: reject wrong targets, preserve TLS and exercise cleanup safety."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
ops=module('ops','scripts/eks-ops.py')
guard=module('guard','scripts/check-foundation-plan.py')

def target():
    return {'id':'aws-dev-workload','account_id':'123456789012','region':'ap-northeast-2','name':'iris-dev-workload','arn':'arn:aws:eks:ap-northeast-2:123456789012:cluster/iris-dev-workload','endpoint':'https://example.eks.amazonaws.com','ca_data':base64.b64encode(b'certificate').decode(),'vpc_id':'vpc-0123456789abcdef0','subnet_ids_by_az':{'ap-northeast-2a':'subnet-0123456789abcdef0','ap-northeast-2c':'subnet-0123456789abcdef1'},'api_port':11443,'kube_context':'iris-dev-workload','operator_principal_arn':'arn:aws:iam::123456789012:role/operator','api_security_group_id':'sg-0123456789abcdef0','ssm_bridge_security_group_id':'sg-0123456789abcdef1','ssm_bridge_instance_id':'i-0123456789abcdef0','workload_api_target_security_group_id':'sg-0123456789abcdef2','node_security_group_id':'sg-0123456789abcdef3','management_api_source_security_group_id':'sg-0123456789abcdef4','cluster_security_group_id':'sg-0123456789abcdef5','argocd_role_arn':'arn:aws:iam::123456789012:role/argocd-workload-deploy','load_balancer_controller_role_arn':'arn:aws:iam::123456789012:role/lbc','node_group_names_by_az':{'ap-northeast-2a':'ng-a','ap-northeast-2c':'ng-c'}}

class Operations(unittest.TestCase):
    def setUp(self):
        self.t=target();self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.joinpath('helm').mkdir();self.root.joinpath('terraform/config').mkdir(parents=True);self.root.joinpath('terraform/config/platform-ecr-repositories.json').write_text((ROOT/'terraform/config/platform-ecr-repositories.json').read_text());self.root.joinpath('helm/images.lock.json').write_text((ROOT/'helm/images.lock.json').read_text())
        self.patcher=patch.object(ops,'ROOT',self.root);self.patcher.start();self.addCleanup(self.patcher.stop)
        env=patch.dict(os.environ,{'AWS_ACCOUNT_ID':'123456789012','AWS_REGION':'ap-northeast-2'},clear=True);env.start();self.addCleanup(env.stop)
    def test_target_rejects_account_endpoint_port_and_principal(self):
        for field,value in [('account_id','999999999999'),('endpoint','https://attacker.example'),('api_port',443),('operator_principal_arn','arn:aws:sts::123456789012:assumed-role/admin/session')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                t=copy.deepcopy(self.t);t[field]=value;ops.validate_target(t,t['id'],'123456789012')
    def test_target_rejects_missing_or_unsafe_ids(self):
        for field,value in [('vpc_id','--bad'),('ssm_bridge_instance_id',''),('kube_context','another-context')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                t=copy.deepcopy(self.t);t[field]=value;ops.validate_target(t,t['id'],'123456789012')
    def test_kubeconfig_preserves_ca_hostname_and_private_mode(self):
        config=ops.kubeconfig(self.t);data=json.loads(config.read_text());cluster=data['clusters'][0]['cluster']
        self.assertEqual(cluster['server'],'https://127.0.0.1:11443');self.assertEqual(cluster['tls-server-name'],'example.eks.amazonaws.com');self.assertEqual(cluster['certificate-authority-data'],self.t['ca_data']);self.assertNotIn('insecure-skip-tls-verify',cluster);self.assertEqual(config.stat().st_mode & 0o777,0o600)
    def test_free_port_refuses_existing_listener(self):
        import socket
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));s.listen()
            with self.assertRaises(OSError):ops.free_port(s.getsockname()[1])
    def test_wrong_aws_account_stops(self):
        with patch.object(ops,'aws',return_value={'Account':'999999999999','Arn':'bad'}),self.assertRaises(ValueError):ops.account()
    def test_wrong_operator_stops(self):
        ops.write_private(self.root/'.generated/targets.json',{'schema_version':1,'targets':{self.t['id']:self.t}})
        with patch.object(ops,'require'),patch.object(ops,'account',return_value=('123456789012','arn:aws:iam::123456789012:role/not-operator')),self.assertRaises(ValueError):ops.load_target(self.t['id'])
    def test_assumed_role_path_matches_operator(self):
        self.t['operator_principal_arn']='arn:aws:iam::123456789012:role/team/operator'
        ops.write_private(self.root/'.generated/targets.json',{'schema_version':1,'targets':{self.t['id']:self.t}})
        with patch.object(ops,'require'),patch.object(ops,'account',return_value=('123456789012','arn:aws:sts::123456789012:assumed-role/operator/session')):self.assertEqual(ops.load_target(self.t['id']),self.t)
    def test_additional_operator_matches(self):
        self.t['additional_operator_principal_arns']=['arn:aws:iam::123456789012:user/second']
        ops.write_private(self.root/'.generated/targets.json',{'schema_version':1,'targets':{self.t['id']:self.t}})
        with patch.object(ops,'require'),patch.object(ops,'account',return_value=('123456789012','arn:aws:iam::123456789012:user/second')):self.assertEqual(ops.load_target(self.t['id']),self.t)
        self.t['additional_operator_principal_arns']=['arn:aws:sts::123456789012:assumed-role/admin/session']
        with self.assertRaises(ValueError):ops.validate_target(self.t,self.t['id'],'123456789012')
    def test_public_api_and_changed_ca_stop_before_bridge_lookup(self):
        cluster={'status':'ACTIVE','version':'1.35','arn':self.t['arn'],'endpoint':self.t['endpoint'],'certificateAuthority':{'data':self.t['ca_data']},'resourcesVpcConfig':{'vpcId':self.t['vpc_id'],'endpointPrivateAccess':True,'endpointPublicAccess':True}}
        with patch.object(ops,'aws',return_value={'cluster':cluster}) as aws,self.assertRaises(ValueError):ops.preflight(self.t)
        self.assertEqual(aws.call_count,1)
        cluster['resourcesVpcConfig']['endpointPublicAccess']=False;cluster['certificateAuthority']['data']='different'
        with patch.object(ops,'aws',return_value={'cluster':cluster}),self.assertRaises(ValueError):ops.preflight(self.t)
    def test_bootstrap_checks_both_targets_before_first_write(self):
        (self.root/'helm/versions.json').write_text((ROOT/'helm/versions.json').read_text())
        token=self.root/'token';token.write_text('fixture-not-a-real-credential');token.chmod(0o600)
        sha='a'*40
        def run(args,**kwargs):
            if 'rev-parse' in args:return sha
            if 'version' in args:return 'v3.19.1+fixture'
            return ''
        with patch.dict(os.environ,{'GITOPS_REVISION':sha,'ARGOCD_GIT_TOKEN_FILE':str(token)}),patch.object(ops,'require'),patch.object(ops,'run',side_effect=run),patch.object(ops,'load_target',return_value=self.t),patch.object(ops,'ready',side_effect=[self.t,ValueError('second target unsafe')]),patch.object(ops,'apply') as apply,patch.object(ops,'kubectl') as kubectl,self.assertRaises(ValueError):ops.bootstrap()
        apply.assert_not_called();kubectl.assert_not_called()
    def bootstrap_fixture(self, flag=None, root_health='Healthy', addon_health='Healthy', platform_exists=True, fail_validation=False):
        (self.root/'helm/versions.json').write_text((ROOT/'helm/versions.json').read_text())
        token=self.root/'token';token.write_text('fixture-not-a-real-credential')
        sha='a'*40; events=[]; applied=[]
        env={'GITOPS_REVISION':sha,'ARGOCD_GIT_TOKEN_FILE':str(token)}
        if flag is not None:env['GITOPS_PLATFORM_ENABLED']=flag
        def run(args,**kwargs):
            events.append(('run',args))
            if 'rev-parse' in args:return sha
            if 'version' in args:return 'v3.19.1+fixture'
            if fail_validation and args[:2]==['helm','lint']:raise RuntimeError('invalid platform inputs')
            return ''
        def kubectl(t,*args,**kwargs):
            events.append(('kubectl',args))
            if args[:2]==('get','applications'):
                names=['iris-addons']+[f'iris-{purpose}-{addon}' for purpose in ('management','workload') for addon in ('baseline','aws-load-balancer-controller','metrics-server','kube-prometheus-stack','opentelemetry-collector')+(('loki',) if purpose=='management' else ())]
                apps=[{'metadata':{'name':name},'status':{'sync':{'status':'Synced'},'health':{'status':root_health if name=='iris-addons' else addon_health}}} for name in names]
                if platform_exists:apps.append({'metadata':{'name':'iris-platform'},'status':{}})
                return json.dumps({'items':apps})
            return ''
        def apply(t,objects):events.append(('apply',None));applied.extend(objects)
        def wait(check,*args):
            if not check():raise RuntimeError('wait condition not met')
        with patch.dict(os.environ,env),patch.object(ops,'require'),patch.object(ops,'run',side_effect=run),patch.object(ops,'load_target',return_value=self.t),patch.object(ops,'ready',return_value=self.t),patch.object(ops,'apply',side_effect=apply),patch.object(ops,'kubectl',side_effect=kubectl),patch.object(ops,'wait_for',side_effect=wait),patch('builtins.print'):
            try:ops.bootstrap()
            except (RuntimeError,ValueError) as exc:return events,applied,exc
        return events,applied,None
    def test_bootstrap_default_platform_disabled(self):
        events,applied,error=self.bootstrap_fixture()
        self.assertIsNone(error)
        app=next(d for d in applied if d['kind']=='Application')
        self.assertEqual(app['spec']['source']['helm']['valuesObject']['platform'],{'enabled':False})
        self.assertFalse(any(e[0]=='run' and e[1][:2]==['helm','lint'] for e in events))
    def test_bootstrap_invalid_flag_stops_before_any_write(self):
        for flag in ('true','yes','2',''):
            events,applied,error=self.bootstrap_fixture(flag)
            self.assertIsInstance(error,ValueError);self.assertEqual(events,[]);self.assertEqual(applied,[])
    def test_bootstrap_enabled_validates_before_first_write(self):
        events,applied,error=self.bootstrap_fixture('1',root_health='Progressing')
        self.assertIsNone(error)
        lint=next(i for i,e in enumerate(events) if e[0]=='run' and e[1][:2]==['helm','lint'])
        render=next(i for i,e in enumerate(events) if e[0]=='run' and e[1][:2]==['helm','template'])
        write=next(i for i,e in enumerate(events) if e[0]=='apply')
        self.assertLess(lint,render);self.assertLess(render,write)
        self.assertEqual(events[lint][1][-4:],['--kube-version','1.35.0','--namespace','iris-platform'])
        self.assertEqual(events[render][1][-4:],['--kube-version','1.35.0','--namespace','iris-platform'])
        app=next(d for d in applied if d['kind']=='Application')
        self.assertEqual(app['spec']['source']['helm']['valuesObject']['platform'],{'enabled':True})
    def test_bootstrap_failed_platform_validation_stops_before_writes(self):
        events,applied,error=self.bootstrap_fixture('1',fail_validation=True)
        self.assertIsInstance(error,RuntimeError);self.assertEqual(applied,[])
        self.assertFalse(any(e[0]=='kubectl' for e in events))
    def test_bootstrap_wait_distinguishes_root_from_addons_and_platform(self):
        self.assertIsNotNone(self.bootstrap_fixture(root_health='Progressing')[2])
        self.assertIsNotNone(self.bootstrap_fixture('1',addon_health='Progressing')[2])
        self.assertIsNotNone(self.bootstrap_fixture('1',platform_exists=False)[2])

    def test_ssm_offline_old_agent_or_inbound_stop(self):
        cluster={'status':'ACTIVE','version':'1.35','arn':self.t['arn'],'endpoint':self.t['endpoint'],'certificateAuthority':{'data':self.t['ca_data']},'resourcesVpcConfig':{'vpcId':self.t['vpc_id'],'endpointPrivateAccess':True,'endpointPublicAccess':False,'subnetIds':list(self.t['subnet_ids_by_az'].values()),'securityGroupIds':[self.t['api_security_group_id'],self.t['workload_api_target_security_group_id']]}}
        groups=[{'GroupId':self.t['api_security_group_id'],'IpPermissions':[{'IpProtocol':'tcp','FromPort':443,'ToPort':443,'UserIdGroupPairs':[{'GroupId':self.t['ssm_bridge_security_group_id']}]}]},{'GroupId':self.t['ssm_bridge_security_group_id'],'IpPermissions':[]},{'GroupId':self.t['workload_api_target_security_group_id'],'IpPermissions':[{'IpProtocol':'tcp','FromPort':443,'ToPort':443,'UserIdGroupPairs':[{'GroupId':self.t['management_api_source_security_group_id']}]}]}]
        responses={'describe-cluster':{'cluster':cluster},'describe-subnets':{'Subnets':[{'VpcId':self.t['vpc_id'],'SubnetId':id,'AvailabilityZone':az,'MapPublicIpOnLaunch':False} for az,id in self.t['subnet_ids_by_az'].items()]},'describe-instances':{'Reservations':[{'Instances':[{'VpcId':self.t['vpc_id'],'State':{'Name':'running'},'SecurityGroups':[{'GroupId':self.t['ssm_bridge_security_group_id']}]}]}]},'describe-security-groups':{'SecurityGroups':groups},'describe-instance-information':{'InstanceInformationList':[{'PingStatus':'Online','AgentVersion':'3.3.0.0'}]}}
        def aws(service,action,*args,**kwargs):return responses[action]
        with patch.object(ops,'aws',side_effect=aws):self.assertEqual(ops.preflight(self.t),self.t)
        for status,version in [('Offline','3.3.0.0'),('Online','3.1.0.0')]:
            responses['describe-instance-information']['InstanceInformationList'][0].update(PingStatus=status,AgentVersion=version)
            with patch.object(ops,'aws',side_effect=aws),self.assertRaises(ValueError):ops.preflight(self.t)
        responses['describe-instance-information']['InstanceInformationList'][0].update(PingStatus='Online',AgentVersion='3.3.0.0');groups[1]['IpPermissions']=[{'IpProtocol':'tcp','FromPort':22,'ToPort':22}]
        with patch.object(ops,'aws',side_effect=aws),self.assertRaises(ValueError):ops.preflight(self.t)
    def test_exercise_failure_cleans_namespace(self):
        calls=[]
        def kubectl(t,*args,**kw):
            calls.append(args)
            if args[:2]==('get','nodes'):return json.dumps({'items':[{'metadata':{'name':'node-a'},'spec':{}},{'metadata':{'name':'node-c'},'spec':{}}]})
            return ''
        with patch.object(ops,'kubectl',side_effect=kubectl),patch.object(ops,'apply',side_effect=RuntimeError('injected failure')),self.assertRaises(RuntimeError):ops.exercise(self.t)
        self.assertTrue(any(c[:2]==('delete','namespace') for c in calls));self.assertFalse(any(c[0]=='drain' for c in calls))
    def test_drain_wrong_target_or_node_stops_before_writes(self):
        with patch.object(ops,'kubectl',return_value=json.dumps({'items':[]})),patch.object(ops,'apply') as apply,self.assertRaises(ValueError):ops.exercise(self.t,drain_node='foreign-node')
        apply.assert_not_called()
    def test_platform_digest_wrong_account_stops(self):
        with patch.object(ops,'apply') as apply,self.assertRaises(ValueError):ops.exercise(self.t,'999999999999.dkr.ecr.ap-northeast-2.amazonaws.com/iris/platform/was@sha256:'+'a'*64)
        apply.assert_not_called()

class PlanProtection(unittest.TestCase):
    def plan(self,address,actions,**extra):return {'format_version':'1.2','resource_changes':[{'address':address,'change':{'actions':actions},**extra}]}
    def test_protected_moves_and_replacements_block(self):
        for address in guard.EXACT | {'aws_ecr_repository.platform["was"]','aws_subnet.private["management-0"]'}:
            self.assertTrue(guard.check(self.plan(address,['create','delete'])))
        self.assertTrue(guard.check(self.plan('aws_vpc.renamed',['delete'],previous_address='aws_vpc.shared')))
    def test_second_nat_and_routes_allowed(self):
        self.assertFalse(guard.check(self.plan('aws_nat_gateway.egress["1"]',['create'])))
        self.assertFalse(guard.check(self.plan('aws_route.private_internet["workload-1"]',['update'])))
    def test_malformed_actions_fail(self):
        for actions in (None,[],['invalid'],[42]):
            with self.subTest(actions=actions),self.assertRaises(ValueError):guard.check(self.plan('aws_vpc.shared',actions))

if __name__=='__main__':unittest.main()
