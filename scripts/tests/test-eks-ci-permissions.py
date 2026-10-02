#!/usr/bin/env python3
"""Test audit completeness, plan selection and failures with a fake AWS CLI.

These tests do not evaluate IAM semantics. Real AWS simulation is a separate check.
"""
from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("eks_ci_audit", ROOT / "scripts/check-eks-ci-permissions.py")
audit = importlib.util.module_from_spec(SPEC)
# dataclasses resolves its module through sys.modules.
import sys
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)
ACCOUNT = "123456789012"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/iris-dev-github-terraform"
C = audit.Config(ACCOUNT, "ap-northeast-2", "iris", "dev", "iris-dev-management", "iris-dev-workload")
SECRET = "never-print-plan-or-aws-secret"
POLICY = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*", "Condition": {"StringEquals": {"aws:RequestTag/Component": "eks"}}}]}


def plan_fixture():
    def row(kind, name, values):
        return {"address": f"{kind}.{name}", "type": kind, "values": values}
    rows = [row("aws_iam_role", "terraform_apply", {"arn": ROLE, "name": "iris-dev-github-terraform", "id": "iris-dev-github-terraform"})]
    managed, inline = audit.expected_names(C)
    for i, name in enumerate(sorted(managed)):
        arn = f"arn:aws:iam::{ACCOUNT}:policy/{name}"
        rows += [row("aws_iam_policy", f"p{i}", {"arn": arn, "name": name, "policy": json.dumps(POLICY)}),
                 row("aws_iam_role_policy_attachment", f"a{i}", {"role": "iris-dev-github-terraform", "policy_arn": arn})]
    for i, name in enumerate(sorted(inline)):
        rows.append(row("aws_iam_role_policy", f"i{i}", {"role": "iris-dev-github-terraform", "name": name, "policy": json.dumps(POLICY)}))
    # Unrelated policy and a sensitive state/config value must not enter the audit.
    rows.append(row("aws_iam_policy", "publisher", {"arn": f"arn:aws:iam::{ACCOUNT}:policy/publisher", "name": "publisher", "policy": SECRET}))
    rows.append(row("aws_iam_role_policy", "publisher", {"role": "publisher", "name": "publisher", "policy": SECRET}))
    values = dict(aws_account_id=ACCOUNT, aws_region=C.region, project=C.project,
                  environment=C.environment, management_cluster_name=C.management, workload_cluster_name=C.workload)
    return {"variables": {k: {"value": v} for k, v in values.items()},
            "planned_values": {"root_module": {"resources": rows}}, "sensitive": SECRET}


FAKE_AWS = r'''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['FAKE_LOG'],'a') as f:f.write(json.dumps(args)+'\n')
def val(flag):return args[args.index(flag)+1]
def vals(flag):
 i=args.index(flag)+1;j=i
 while j<len(args) and not args[j].startswith('--'):j+=1
 return args[i:j]
if os.environ.get('FAKE_ERROR'):
 print('never-print-plan-or-aws-secret',file=sys.stderr);sys.exit(2)
if os.environ.get('FAKE_INVALID_JSON'):
 print('never-print-plan-or-aws-secret');sys.exit(0)
policy={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Action':'*','Resource':'*','Condition':{'StringEquals':{'aws:RequestTag/Component':'eks'}}}]}
service,action=args[:2]
if service=='sts':out={'Account':os.environ.get('FAKE_ACCOUNT','123456789012')}
elif action=='get-role':out={'Role':{'Arn':'arn:aws:iam::123456789012:role/iris-dev-github-terraform'}}
elif action=='list-attached-role-policies':out={'AttachedPolicies':[{'PolicyName':'iris-dev-'+n,'PolicyArn':'arn:aws:iam::123456789012:policy/iris-dev-'+n} for n in ['eks-deployment','runtime-compute','runtime-iam','bridge-launch','foundation-network','alb-access-logs-deployment']]}
elif action=='get-policy':out={'Policy':{'DefaultVersionId':'v1'}}
elif action=='get-policy-version':out={'PolicyVersion':{'Document':policy}}
elif action=='list-role-policies':out={'PolicyNames':['bootstrap-state-bucket','foundation-build-resources','platform-ecr-resources']}
elif action=='get-role-policy':out={'PolicyDocument':policy}
elif action=='validate-policy':out={'findings':[{'findingType':'ERROR','issueCode':'FAKE'}] if os.environ.get('FAKE_VALIDATE_ERROR') else []}
elif action.startswith('simulate-'):
 assert '--no-paginate' not in args and '--max-items' not in args
 ctx={x['ContextKeyName']:x['ContextKeyValues'][0] for x in json.loads(val('--context-entries'))}
 out={'EvaluationResults':[]}
 for a in vals('--action-names'):
  children=[]
  for r in vals('--resource-arns'):
   denied=(ctx.get('aws:RequestedRegion')=='us-west-2' or 'iris-permission-audit-unrelated' in r or
    (a in ('sqs:ReceiveMessage','sqs:SendMessage','sqs:DeleteMessage') and '-alb-access-logs' in r) or
    (a in ('s3:GetObject','s3:PutObject','s3:DeleteObject') and '-alb-access-logs-' in r) or
    (a=='iam:PassRole' and ((r.endswith('build-worker') and ctx.get('iam:PassedToService')=='ec2.amazonaws.com') or (r.endswith(('deploy-worker','alb-log-collector')) and ctx.get('iam:PassedToService')!='pods.eks.amazonaws.com'))) or
    (a=='eks:CreateCluster' and (ctx.get('eks:endpointPublicAccess')=='true' or ctx.get('aws:RequestTag/Project','').endswith('-other'))) or
    (a=='ec2:RunInstances' and ((':instance/' in r and ctx.get('ec2:InstanceType')!='t3.micro') or
     (':image/' in r and ctx.get('ec2:Owner')!='amazon') or (':volume/' in r and ctx.get('aws:RequestTag/Project','').endswith('-other')))))
   decision='implicitDeny' if denied else 'allowed'
   if os.environ.get('FAKE_DENY')==a:decision='implicitDeny'
   if os.environ.get('FAKE_ALLOW_NEGATIVES'):decision='allowed'
   if os.environ.get('FAKE_UNKNOWN_DECISION'):decision='unknown'
   children.append({'EvalResourceName':r,'EvalResourceDecision':decision})
  if os.environ.get('FAKE_PARTIAL'):children=children[:-1]
  if os.environ.get('FAKE_DUPLICATE') and children:children.append(children[0])
  if os.environ.get('FAKE_EXTRA'):children.append({'EvalResourceName':'unexpected','EvalResourceDecision':'allowed'})
  entry={'EvalActionName':a,'EvalResourceName':'aggregate','EvalDecision':'allowed','ResourceSpecificResults':children}
  if os.environ.get('FAKE_CONTEXT'):entry['MissingContextValues']=['aws:RequestTag/Component']
  out['EvaluationResults'].append(entry)
else:raise AssertionError('unexpected API '+action)
print(json.dumps(out))
'''


class PermissionAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="iris-permissions-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.log = self.directory / "aws.jsonl"
        binary = self.directory / "aws"
        binary.write_text(FAKE_AWS)
        binary.chmod(0o755)
        self.environment = {"PATH": str(self.directory) + os.pathsep + os.environ["PATH"], "FAKE_LOG": str(self.log)}
        self.plan = plan_fixture()
        self.path = self.directory / "plan.json"

    def invoke(self, plan=True, extra=(), environment=None):
        self.path.write_text(json.dumps(self.plan))
        args = ["--role-arn", ROLE]
        if plan:
            args += ["--account-plan-json", str(self.path)]
        output = io.StringIO()
        with patch.dict(os.environ, {**self.environment, **(environment or {})}), patch.object(audit.time, "sleep"), redirect_stdout(output), redirect_stderr(output):
            code = audit.main(args + list(extra))
        self.assertNotIn(SECRET, output.getvalue())
        return code, output.getvalue()

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_custom_selects_only_attached_ci_policies(self):
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assertIn("custom identity policies", output)
        calls = self.calls()
        sim = next(c for c in calls if c[1] == "simulate-custom-policy")
        docs = sim[sim.index("--policy-input-list") + 1:sim.index("--action-names")]
        self.assertEqual(len(docs), 9)
        self.assertNotIn(SECRET, " ".join(sim))
        self.assertTrue(all(c[0] in ("sts", "iam", "accessanalyzer") for c in calls))
        self.assertFalse(any(c[1].startswith(("create-", "put-", "delete-", "attach-")) for c in calls))

    def test_live_reads_current_policy_versions(self):
        code, output = self.invoke(plan=False)
        self.assertEqual(code, 0, output)
        self.assertIn("live principal", output)
        self.assertTrue(any(c[1] == "get-policy-version" for c in self.calls()))
        self.assertTrue(any(c[1] == "simulate-principal-policy" for c in self.calls()))
        self.assertFalse(any(c[1] == "simulate-custom-policy" for c in self.calls()))

    def test_positive_permission_denial_fails(self):
        code, output = self.invoke(environment={"FAKE_DENY": "ec2:DescribeInstanceCreditSpecifications"})
        self.assertEqual(code, 1)
        self.assertIn("ec2:DescribeInstanceCreditSpecifications", output)

    def test_negative_permission_allow_fails(self):
        code, output = self.invoke(environment={"FAKE_ALLOW_NEGATIVES": "1"})
        self.assertEqual(code, 1)
        self.assertIn("Reject public endpoint", output)

    def test_missing_duplicate_extra_unknown_or_context_results_fail(self):
        for key in ("FAKE_PARTIAL", "FAKE_DUPLICATE", "FAKE_EXTRA", "FAKE_UNKNOWN_DECISION", "FAKE_CONTEXT"):
            with self.subTest(key=key):
                code, output = self.invoke(environment={key: "1"})
                self.assertEqual(code, 1, output)

    def test_access_analyzer_errors_fail(self):
        self.assertEqual(self.invoke(environment={"FAKE_VALIDATE_ERROR": "1"})[0], 1)

    def test_aws_error_and_invalid_json_are_not_exposed(self):
        for key in ("FAKE_ERROR", "FAKE_INVALID_JSON"):
            with self.subTest(key=key):
                self.assertEqual(self.invoke(environment={key: "1"})[0], 1)

    def test_wrong_caller_account_stops_before_policy_reads(self):
        self.assertEqual(self.invoke(environment={"FAKE_ACCOUNT": "999999999999"})[0], 1)
        self.assertEqual([c[1] for c in self.calls()], ["get-caller-identity"])

    def test_unknown_role_attachment_policy_or_inventory_fails_before_aws(self):
        original = copy.deepcopy(self.plan)
        changes = [lambda r: r[0]["values"].pop("arn"),
                   lambda r: r[2]["values"].pop("role"),
                   lambda r: r[2]["values"].pop("policy_arn"),
                   lambda r: r[1]["values"].pop("policy"),
                   lambda r: r.pop(2)]
        for change in changes:
            with self.subTest(change=change):
                self.plan = copy.deepcopy(original)
                change(self.plan["planned_values"]["root_module"]["resources"])
                self.assertEqual(self.invoke()[0], 1)
                self.assertEqual(self.calls(), [])

    def test_conflicting_plan_inputs_or_account_fail_before_aws(self):
        self.assertEqual(self.invoke(extra=("--region", "us-west-2"))[0], 1)
        self.plan["variables"]["aws_account_id"]["value"] = "999999999999"
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.calls(), [])

    def test_missing_plan_variable_fails(self):
        del self.plan["variables"]["project"]
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.calls(), [])

    def test_child_module_policy_is_selected(self):
        module = self.plan["planned_values"]["root_module"]
        module["child_modules"] = [{"resources": [module["resources"].pop(1)]}]
        selected, policies = audit.plan_policies(self.plan, ROLE, C)
        self.assertEqual(len(selected), 6)
        self.assertEqual(len(policies), 9)

    def test_embedded_role_policies_fail(self):
        self.plan["planned_values"]["root_module"]["resources"][0]["values"]["inline_policy"] = [{"policy": SECRET}]
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.calls(), [])

    def test_computed_role_policy_mirrors_are_not_omitted_or_rejected(self):
        rows = self.plan["planned_values"]["root_module"]["resources"]
        role = rows[0]["values"]
        role["managed_policy_arns"] = [r["values"]["policy_arn"] for r in rows if r["type"] == "aws_iam_role_policy_attachment"]
        role["inline_policy"] = [{"name": r["values"]["name"], "policy": r["values"]["policy"]} for r in rows if r["type"] == "aws_iam_role_policy" and r["values"]["role"] == "iris-dev-github-terraform"]
        self.assertEqual(len(audit.plan_policies(self.plan, ROLE, C)[1]), 9)
        role["managed_policy_arns"].append("arn:aws:iam::aws:policy/AdministratorAccess")
        with self.assertRaises(audit.AuditError):
            audit.plan_policies(self.plan, ROLE, C)

    def test_missing_irrelevant_context_does_not_hide_a_permission_denial(self):
        case = audit.Case("credit", ["ec2:DescribeInstanceCreditSpecifications"], ["*"], C.context())
        result = {"EvalActionName": case.actions[0], "EvalResourceName": "*", "EvalDecision": "implicitDeny", "MissingContextValues": ["iam:PassedToService"]}
        policies = [{"Statement": [{"Action": "iam:PassRole", "Resource": "*", "Condition": {"StringEquals": {"iam:PassedToService": "pods.eks.amazonaws.com"}}}]}]
        self.assertEqual(audit.decisions(case, {"EvaluationResults": [result]}, policies)[(case.actions[0], "*")], "implicitDeny")
        policies[0]["Statement"][0]["Action"] = case.actions[0]
        with self.assertRaises(audit.AuditError):
            audit.decisions(case, {"EvaluationResults": [result]}, policies)

    def test_single_resource_aws_result_and_missing_page(self):
        case = audit.Case("one", ["eks:CreateCluster"], ["*"], C.context())
        result = {"EvalActionName": "eks:CreateCluster", "EvalResourceName": "*", "EvalDecision": "allowed"}
        self.assertEqual(audit.decisions(case, {"EvaluationResults": [result]}), {("eks:CreateCluster", "*"): "allowed"})
        case.resources.append("other")
        with self.assertRaises(audit.AuditError):
            audit.decisions(case, {"EvaluationResults": [result]})

    def test_matrix_comes_from_provider_paths_and_checks_both_clusters(self):
        cases = audit.matrix(C)
        positive = {(a, r) for c in cases if c.allow for a in c.actions for r in c.resources}
        for name in C.clusters:
            self.assertIn(("eks:CreateAddon", f"arn:aws:eks:{C.region}:{ACCOUNT}:podidentityassociation/{name}/a-test123"), positive)
        self.assertIn(("iam:PassRole", f"arn:aws:iam::{ACCOUNT}:role/iris-dev-build-worker"), positive)
        deploy = f"arn:aws:iam::{ACCOUNT}:role/iris-dev-deploy-worker"
        self.assertIn(("iam:PassRole", deploy), positive)
        self.assertIn(("iam:CreateRole", deploy), positive)
        negative = [case for case in cases if not case.allow and deploy in case.resources]
        self.assertEqual({next(x['ContextKeyValues'][0] for x in case.context if x['ContextKeyName']=='iam:PassedToService') for case in negative}, {'ec2.amazonaws.com','eks.amazonaws.com'})
        self.assertIn(("ec2:DescribeInstanceCreditSpecifications", "*"), positive)
        self.assertGreater(sum(len(c.actions) * len(c.resources) for c in cases if c.allow), 440)
        self.assertGreaterEqual(sum(len(c.actions) * len(c.resources) for c in cases if not c.allow), 12)


if __name__ == "__main__":
    unittest.main()
