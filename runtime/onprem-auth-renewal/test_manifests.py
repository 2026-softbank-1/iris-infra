"""Check the coupled runtime/RBAC contracts; actual CEL/API checks are deployment gates."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


def documents(path):
    return [value for value in yaml.safe_load_all((ROOT / path).read_text()) if value]


class ManifestTests(unittest.TestCase):
    def test_job_is_staged_with_own_credentials_and_reachable_proxy(self):
        docs = documents('runtime/onprem-auth-renewal/all-services.yaml')
        job = next(d for d in docs if d['kind'] == 'CronJob')
        self.assertTrue(job['spec']['suspend'])
        self.assertEqual(job['spec']['schedule'], '* * * * *')
        self.assertEqual(job['spec']['concurrencyPolicy'], 'Forbid')
        pod = job['spec']['jobTemplate']['spec']['template']['spec']
        self.assertEqual(pod['serviceAccountName'], 'iris-onprem-ecr-renewer')
        env = {e['name']: e.get('value') for e in pod['containers'][0]['env']}
        self.assertEqual(env['MODE'], 'ecr-all')
        self.assertEqual(env['AUTH_SECRET'], 'iris-onprem-ecr-renew-auth')
        role = next(d for d in docs if d['kind'] == 'Role')
        self.assertEqual(role['rules'], [{'apiGroups': [''], 'resources': ['secrets'],
            'resourceNames': ['iris-onprem-ecr-renew-auth'], 'verbs': ['get', 'patch']}])
        ingress = next(d for d in documents('runtime/onprem-auth-renewal/kubernetes.yaml')
                       if d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'iris-onprem-auth-renewal-api')
        allowed = ingress['spec']['ingress'][0]['from']
        self.assertTrue(any(p['podSelector']['matchLabels']['app.kubernetes.io/name'] == 'iris-onprem-ecr-renewer'
                            for p in allowed))

    def test_namespace_rbac_does_not_grant_secret_listing_or_namespace_writes(self):
        docs = documents('clusters/onprem-workload/ecr-all-rbac.yaml')
        rules = next(d for d in docs if d['kind'] == 'ClusterRole')['rules']
        self.assertEqual(next(r for r in rules if r['resources'] == ['namespaces'])['verbs'], ['get', 'list'])
        secret_rules = [r for r in rules if r['resources'] == ['secrets']]
        self.assertEqual(secret_rules, [
            {'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': ['iris-ecr-pull'], 'verbs': ['get', 'patch']},
            {'apiGroups': [''], 'resources': ['secrets'], 'verbs': ['create']}])
        self.assertFalse(any(set(r['verbs']) & {'bind', 'escalate', 'impersonate', '*'} for r in rules))
        token_role = next(d for d in docs if d['kind'] == 'Role')
        self.assertEqual(token_role['rules'], [{'apiGroups': [''], 'resources': ['serviceaccounts/token'],
            'resourceNames': ['iris-ecr-renewer'], 'verbs': ['create']}])

    def test_every_write_type_has_actor_scoped_deny_policy(self):
        docs = documents('clusters/onprem-workload/ecr-all-admission.yaml')
        policies = {d['metadata']['name']: d for d in docs if d['kind'] == 'ValidatingAdmissionPolicy'}
        bindings = {d['spec']['policyName']: d for d in docs if d['kind'] == 'ValidatingAdmissionPolicyBinding'}
        self.assertEqual(set(policies), set(bindings))
        self.assertEqual(len(policies), 3)
        kinds = set()
        for name, policy in policies.items():
            spec = policy['spec']
            self.assertEqual(spec['failurePolicy'], 'Fail')
            self.assertEqual(bindings[name]['spec']['validationActions'], ['Deny'])
            self.assertEqual(spec['matchConditions'][0]['expression'],
                "request.userInfo.username == 'system:serviceaccount:iris-system:iris-ecr-renewer'")
            self.assertEqual(spec['validations'][0]['expression'], "request.namespace.matches('^svc-[1-9][0-9]*$')")
            rule = spec['matchConstraints']['resourceRules'][0]
            kinds.update(rule['resources'])
            if rule['resources'] == ['pods']:
                self.assertEqual(rule['operations'], ['DELETE'])
                expressions = ' '.join(v['expression'] for v in spec['validations'][1:])
                self.assertIn('oldObject.status.phase', expressions)
                self.assertIn('oldObject.metadata.ownerReferences', expressions)
                self.assertIn('request.namespace.substring(4)', expressions)
                self.assertIn('ImagePullBackOff', expressions)
                self.assertNotIn('object.', expressions)
        self.assertEqual(kinds, {'secrets', 'serviceaccounts', 'pods'})

    def test_both_configmaps_include_generic_module(self):
        config = documents('runtime/onprem-auth-renewal/kustomization.yaml')[0]
        for generator in config['configMapGenerator']:
            self.assertIn('all_services.py', generator['files'])


if __name__ == '__main__':
    unittest.main()
