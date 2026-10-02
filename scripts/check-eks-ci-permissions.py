#!/usr/bin/env python3
"""Read-only IAM preflight for the current EKS module and SSM bridge.

Expectations come from AWS provider 6.67.0 API paths, not the policy Action lists:
internal/service/{ec2/ec2_instance,ec2/ec2_launch_template,eks/cluster,
eks/node_group,eks/addon,eks/access_entry,eks/access_policy_association,
eks/pod_identity_association,iam/role,iam/policy,iam/instance_profile,
iam/openid_connect_provider,logs/group}.go, including find/update/tag helpers.
Resource boundaries follow AWS Service Authorization Reference list_eks/list_ec2.
Optional CPU/metadata/CMK changes outside the fixed configuration are excluded.
No AWS writes, Terraform commands, role assumption or state changes are performed.
"""
import argparse
from dataclasses import dataclass
import json
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import unquote


class AuditError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    account: str
    region: str
    project: str
    environment: str
    management: str
    workload: str

    @property
    def stem(self):
        return f"{self.project}-{self.environment}"

    @property
    def clusters(self):
        return (self.management, self.workload)

    def context(self, extra=None):
        values = {"aws:RequestedRegion": self.region}
        for key, value in {"Project": self.project, "Environment": self.environment,
                           "ManagedBy": "Terraform", "Component": "eks"}.items():
            for kind in ("RequestTag", "ResourceTag"):
                values[f"aws:{kind}/{key}"] = value
        values.update(extra or {})
        return [{"ContextKeyName": key, "ContextKeyValues": [value],
                 "ContextKeyType": "string"} for key, value in values.items()]


@dataclass
class Case:
    label: str
    actions: list
    resources: list
    context: list
    allow: bool = True


def matrix(c):
    cases = []

    def add(label, service, actions, resources, extra=None, allow=True):
        cases.append(Case(label, [f"{service}:{a}" for a in actions.split()],
                          resources if isinstance(resources, list) else [resources],
                          c.context(extra), allow))

    eks = f"arn:aws:eks:{c.region}:{c.account}:"
    ec2 = f"arn:aws:ec2:{c.region}:{c.account}:"
    iam = f"arn:aws:iam::{c.account}:"
    clusters = [eks + "cluster/" + n for n in c.clusters]
    nodes = [eks + f"nodegroup/{n}/{n}-{c.region}a/test-id" for n in c.clusters]
    addons = [eks + f"addon/{n}/aws-ebs-csi-driver/test-id" for n in c.clusters]
    pods = [eks + f"podidentityassociation/{n}/a-test123" for n in c.clusters]
    entries = [eks + f"access-entry/{n}/role/{c.account}/operator/test-id" for n in c.clusters]
    create = {"eks:authenticationMode": "API", "eks:supportType": "STANDARD",
              "eks:endpointPrivateAccess": "true", "eks:endpointPublicAccess": "false",
              "eks:bootstrapClusterCreatorAdminPermissions": "false",
              "eks:bootstrapSelfManagedAddons": "false"}
    add("EKS create", "eks", "CreateCluster", "*", create)
    add("EKS clusters", "eks", "DescribeCluster UpdateClusterConfig UpdateClusterVersion DeleteCluster ListUpdates DescribeUpdate ListNodegroups ListAddons ListAccessEntries ListPodIdentityAssociations CreateNodegroup CreateAddon CreateAccessEntry CreatePodIdentityAssociation TagResource UntagResource ListTagsForResource", clusters)
    add("EKS nodegroups", "eks", "DescribeNodegroup UpdateNodegroupConfig UpdateNodegroupVersion DeleteNodegroup DescribeUpdate ListUpdates TagResource UntagResource ListTagsForResource", nodes)
    add("EKS addons", "eks", "DescribeAddon UpdateAddon DeleteAddon DescribeUpdate ListUpdates TagResource UntagResource ListTagsForResource", addons)
    add("Addon Pod Identity", "eks", "CreateAddon UpdateAddon DeleteAddon", pods)
    add("EKS access entries", "eks", "DescribeAccessEntry UpdateAccessEntry DeleteAccessEntry AssociateAccessPolicy DisassociateAccessPolicy ListAssociatedAccessPolicies TagResource UntagResource", entries)
    add("EKS Pod Identity", "eks", "DescribePodIdentityAssociation UpdatePodIdentityAssociation DeletePodIdentityAssociation TagResource UntagResource", pods)
    add("EKS discovery", "eks", "ListClusters DescribeAddonVersions DescribeAddonConfiguration DescribeClusterVersions", "*")
    roles = [iam + f"role/{n}-{s}" for n in c.clusters for s in ("cluster", "node", "cni", "ebs", "lbc")]
    roles += [iam + f"role/{c.stem}-{s}" for s in ("ssm-bridge", "argocd-management", "argocd-management-deploy", "argocd-workload-deploy")]
    add("Runtime roles", "iam", "CreateRole GetRole UpdateAssumeRolePolicy UpdateRole UpdateRoleDescription DeleteRole ListRolePolicies ListAttachedRolePolicies ListInstanceProfilesForRole PutRolePolicy GetRolePolicy DeleteRolePolicy TagRole UntagRole ListRoleTags", roles)
    for service, suffixes in (("eks.amazonaws.com", ("cluster", "node", "cni", "ebs")),
                              ("pods.eks.amazonaws.com", ("ebs", "lbc")),
                              ("ec2.amazonaws.com", ("node",))):
        add("Pass runtime roles", "iam", "PassRole", [iam + f"role/{n}-{s}" for n in c.clusters for s in suffixes], {"iam:PassedToService": service})
    add("Pass Argo role", "iam", "PassRole", iam + f"role/{c.stem}-argocd-management", {"iam:PassedToService": "pods.eks.amazonaws.com"})
    worker = iam + f"role/{c.stem}-build-worker"
    add("Pass Build Worker", "iam", "PassRole", worker, {"iam:PassedToService": "pods.eks.amazonaws.com"})
    bridge = iam + f"role/{c.stem}-ssm-bridge"
    add("Pass bridge", "iam", "PassRole", bridge, {"iam:PassedToService": "ec2.amazonaws.com"})
    for policy, suffix in (("AmazonEKSClusterPolicy", "cluster"), ("AmazonEKSWorkerNodePolicy", "node"),
                           ("AmazonEC2ContainerRegistryPullOnly", "node"), ("AmazonEKS_CNI_Policy", "cni"),
                           ("service-role/AmazonEBSCSIDriverPolicy", "ebs")):
        add("Attach AWS policies", "iam", "AttachRolePolicy DetachRolePolicy", [iam + f"role/{n}-{suffix}" for n in c.clusters], {"iam:PolicyARN": "arn:aws:iam::aws:policy/" + policy})
    add("Attach SSM policy", "iam", "AttachRolePolicy DetachRolePolicy", bridge, {"iam:PolicyARN": "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"})
    for n in c.clusters:
        add("Attach LBC policy", "iam", "AttachRolePolicy DetachRolePolicy", iam + f"role/{n}-lbc", {"iam:PolicyARN": iam + f"policy/{n}-lbc"})
    add("LBC policy lifecycle", "iam", "CreatePolicy GetPolicy GetPolicyVersion CreatePolicyVersion DeletePolicyVersion DeletePolicy ListPolicyVersions ListPolicyTags TagPolicy UntagPolicy", [iam + f"policy/{n}-lbc" for n in c.clusters])
    add("EKS OIDC", "iam", "CreateOpenIDConnectProvider GetOpenIDConnectProvider DeleteOpenIDConnectProvider UpdateOpenIDConnectProviderThumbprint AddClientIDToOpenIDConnectProvider RemoveClientIDFromOpenIDConnectProvider ListOpenIDConnectProviderTags TagOpenIDConnectProvider UntagOpenIDConnectProvider", iam + f"oidc-provider/oidc.eks.{c.region}.amazonaws.com/id/TEST")
    add("Bridge profile", "iam", "CreateInstanceProfile GetInstanceProfile DeleteInstanceProfile AddRoleToInstanceProfile RemoveRoleFromInstanceProfile ListInstanceProfileTags TagInstanceProfile UntagInstanceProfile", iam + f"instance-profile/{c.stem}-ssm-bridge")
    for service in ("eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "autoscaling.amazonaws.com"):
        name = {"eks.amazonaws.com": "AWSServiceRoleForAmazonEKS", "eks-nodegroup.amazonaws.com": "AWSServiceRoleForAmazonEKSNodegroup", "autoscaling.amazonaws.com": "AWSServiceRoleForAutoScaling"}[service]
        add("Service linked role", "iam", "CreateServiceLinkedRole", iam + f"role/aws-service-role/{service}/{name}", {"iam:AWSServiceName": service})
    add("EKS logs", "logs", "CreateLogGroup PutRetentionPolicy DeleteLogGroup ListTagsForResource TagResource UntagResource", [f"arn:aws:logs:{c.region}:{c.account}:log-group:/aws/eks/{n}/cluster" for n in c.clusters])
    add("Logs discovery", "logs", "DescribeLogGroups", "*")
    add("EC2 discovery", "ec2", "DescribeInstances DescribeInstanceAttribute DescribeInstanceStatus DescribeInstanceCreditSpecifications DescribeInstanceTypes DescribeInstanceTypeOfferings DescribeImages DescribeVolumes DescribeLaunchTemplates DescribeLaunchTemplateVersions DescribeNetworkInterfaces DescribeSecurityGroups DescribeSecurityGroupRules DescribeTags DescribeSubnets DescribeVpcs", "*")
    add("Launch template", "ec2", "CreateLaunchTemplate ModifyLaunchTemplate CreateLaunchTemplateVersion DeleteLaunchTemplateVersions DeleteLaunchTemplate", ec2 + "launch-template/lt-test123")
    access = {"aws:RequestTag/Component": "access", "aws:ResourceTag/Component": "access", "ec2:InstanceType": "t3.micro"}
    for kind in ("instance", "volume", "network-interface", "subnet", "security-group"):
        add("Bridge launch " + kind, "ec2", "RunInstances", ec2 + kind + "/test", access)
    add("Bridge launch AMI", "ec2", "RunInstances", f"arn:aws:ec2:{c.region}::image/ami-test", {"ec2:Owner": "amazon"})
    for kind in ("instance", "volume"):
        add("Bridge launch tags", "ec2", "CreateTags", ec2 + kind + "/test", {**access, "ec2:CreateAction": "RunInstances"})
    add("Bridge AMI lookup", "ssm", "GetParameter", f"arn:aws:ssm:{c.region}::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")
    add("Bridge maintenance", "ec2", "TerminateInstances ModifyInstanceAttribute ModifyInstanceCreditSpecification StopInstances StartInstances", ec2 + "instance/test", access)
    add("SG creation parent", "ec2", "CreateSecurityGroup", ec2 + "vpc/test")
    add("SG creation", "ec2", "CreateSecurityGroup", ec2 + "security-group/test")
    for kind in ("security-group", "security-group-rule"):
        add("SG rule creation", "ec2", "AuthorizeSecurityGroupIngress AuthorizeSecurityGroupEgress", ec2 + kind + "/test")
    add("SG lifecycle", "ec2", "DeleteSecurityGroup RevokeSecurityGroupIngress RevokeSecurityGroupEgress", ec2 + "security-group/test")
    add("SG rule lifecycle", "ec2", "ModifySecurityGroupRules", ec2 + "security-group-rule/test")
    add("LT creation tags", "ec2", "CreateTags", ec2 + "launch-template/test", {"ec2:CreateAction": "CreateLaunchTemplate"})
    # Each negative changes one relevant boundary. CreateCluster has no name key.
    add("Reject other region", "ec2", "DescribeInstanceCreditSpecifications", "*", {"aws:RequestedRegion": "us-west-2" if c.region != "us-west-2" else "ap-northeast-2"}, False)
    other = "iris-permission-audit-unrelated"
    if other in c.clusters:
        other += "-other"
    add("Reject other cluster", "eks", "DescribeCluster TagResource", eks + "cluster/" + other, allow=False)
    add("Reject other addon association", "eks", "CreateAddon", eks + f"podidentityassociation/{other}/a-test", allow=False)
    add("Reject other child update", "eks", "DescribeUpdate", eks + f"nodegroup/{other}/test/id", allow=False)
    add("Reject other passed service", "iam", "PassRole", worker, {"iam:PassedToService": "ec2.amazonaws.com"}, False)
    add("Reject other role", "iam", "PassRole", iam + "role/iris-permission-audit-unrelated", {"iam:PassedToService": "pods.eks.amazonaws.com"}, False)
    add("Reject public endpoint", "eks", "CreateCluster", "*", {**create, "eks:endpointPublicAccess": "true"}, False)
    add("Reject other project", "eks", "CreateCluster", "*", {**create, "aws:RequestTag/Project": c.project + "-other"}, False)
    add("Reject other bridge type", "ec2", "RunInstances", ec2 + "instance/test", {**access, "ec2:InstanceType": "m5.large"}, False)
    add("Reject other AMI owner", "ec2", "RunInstances", f"arn:aws:ec2:{c.region}::image/ami-test", {"ec2:Owner": "123456789012"}, False)
    add("Reject other volume owner", "ec2", "RunInstances", ec2 + "volume/test", {**access, "aws:RequestTag/Project": c.project + "-other"}, False)
    return cases


class Aws:
    def __init__(self, region):
        self.region = region

    def call(self, *args):
        env = dict(os.environ, AWS_PAGER="", AWS_RETRY_MODE="adaptive", AWS_MAX_ATTEMPTS="10")
        try:
            result = subprocess.run(["aws", *args, "--region", self.region, "--output", "json"],
                                    env=env, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired):
            raise AuditError("AWS CLI unavailable or timed out") from None
        if result.returncode:
            # AWS output may contain credentials, policy documents or input values.
            raise AuditError(f"AWS CLI failed: {args[0]}/{args[1]}")
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise AuditError("AWS CLI returned invalid JSON") from None


def document(value):
    try:
        if isinstance(value, str):
            value = json.loads(unquote(value))
        if not isinstance(value, dict) or not value.get("Statement"):
            raise ValueError()
        if not isinstance(value["Statement"], (list, dict)):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise AuditError("Policy document missing, unknown or malformed") from None


def expected_names(c):
    return ({c.stem + "-" + n for n in ("eks-deployment", "runtime-compute", "runtime-iam", "bridge-launch", "foundation-network")},
            {"bootstrap-state-bucket", "foundation-build-resources", "platform-ecr-resources"})


def check_inventory(c, managed, inline):
    required_managed, required_inline = expected_names(c)
    if not required_managed.issubset(managed) or not required_inline.issubset(inline):
        raise AuditError("CI policy inventory incomplete")


def resources(module):
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from resources(child)


def plan_policies(plan, role_arn, c):
    root = plan.get("planned_values", {}).get("root_module")
    if not isinstance(root, dict):
        raise AuditError("Account plan planned_values missing")
    rows = list(resources(root))
    role_name = role_arn.split("/")[-1]
    targets = [r for r in rows if r.get("type") == "aws_iam_role" and r.get("values", {}).get("arn") == role_arn]
    if len(targets) != 1 or targets[0]["values"].get("name") != role_name:
        raise AuditError("Target CI role missing or unknown in account plan")
    # Unknown role on ANY IAM attachment/inline cannot be safely classified.
    related = [r for r in rows if r.get("type") in ("aws_iam_role_policy", "aws_iam_role_policy_attachment")]
    if any(not isinstance(r.get("values", {}).get("role"), str) or not r["values"]["role"] for r in related):
        raise AuditError("Account plan policy role binding unknown")
    attached = [r["values"] for r in related if r["type"] == "aws_iam_role_policy_attachment" and r["values"]["role"] == role_name]
    managed, inline = {}, {}
    for attachment in attached:
        arn = attachment.get("policy_arn")
        if not isinstance(arn, str) or not arn.startswith(f"arn:aws:iam::{c.account}:policy/"):
            raise AuditError("Attached CI policy ARN missing, external or unknown in plan")
        found = [r.get("values", {}) for r in rows if r.get("type") == "aws_iam_policy" and r.get("values", {}).get("arn") == arn]
        if len(found) != 1 or not isinstance(found[0].get("name"), str):
            raise AuditError("Attached CI policy missing or ambiguous in plan")
        name = found[0]["name"]
        if name in managed:
            raise AuditError("Duplicate CI policy binding")
        managed[name] = document(found[0].get("policy"))
    for row in related:
        value = row["values"]
        if row["type"] == "aws_iam_role_policy" and value["role"] == role_name:
            name = value.get("name")
            if not isinstance(name, str) or name in inline:
                raise AuditError("Inline CI policy name unknown or duplicated")
            inline[name] = document(value.get("policy"))
    # aws_iam_role may expose computed copies of separately managed policies.
    # Accept those only when every binding/document is accounted for; never omit
    # an additional embedded policy merely because it is not a standalone row.
    role_values = targets[0]["values"]
    mirror_arns = role_values.get("managed_policy_arns") or []
    bound_arns = {attachment["policy_arn"] for attachment in attached}
    if not isinstance(mirror_arns, list) or not set(mirror_arns).issubset(bound_arns):
        raise AuditError("Embedded managed policy bindings are not accounted for")
    for embedded in role_values.get("inline_policy") or []:
        if embedded.get("name") not in inline or document(embedded.get("policy")) != inline[embedded["name"]]:
            raise AuditError("Embedded inline policies are not accounted for")
    check_inventory(c, managed, inline)
    return managed, [*managed.values(), *inline.values()]


def live_policies(aws, role_arn, c):
    role_name = role_arn.split("/")[-1]
    role = aws.call("iam", "get-role", "--role-name", role_name)["Role"]
    if role.get("Arn") != role_arn:
        raise AuditError("CI role ARN mismatch")
    attached = aws.call("iam", "list-attached-role-policies", "--role-name", role_name)["AttachedPolicies"]
    managed, inline = {}, {}
    for item in attached:
        arn, name = item["PolicyArn"], item["PolicyName"]
        version = aws.call("iam", "get-policy", "--policy-arn", arn)["Policy"]["DefaultVersionId"]
        managed[name] = document(aws.call("iam", "get-policy-version", "--policy-arn", arn, "--version-id", version)["PolicyVersion"]["Document"])
    names = aws.call("iam", "list-role-policies", "--role-name", role_name)["PolicyNames"]
    for name in names:
        inline[name] = document(aws.call("iam", "get-role-policy", "--role-name", role_name, "--policy-name", name)["PolicyDocument"])
    check_inventory(c, managed, inline)
    return managed, [*managed.values(), *inline.values()]


def relevant_context(action, arn, policies):
    keys = set()
    for policy in policies:
        statements = policy["Statement"]
        if isinstance(statements, dict):
            statements = [statements]
        for statement in statements:
            actions = statement.get("Action", [])
            resources = statement.get("Resource", [])
            if isinstance(actions, str):
                actions = [actions]
            if isinstance(resources, str):
                resources = [resources]
            action_match = any(fnmatchcase(action.lower(), a.lower()) for a in actions)
            resource_match = any(fnmatchcase(arn, r) for r in resources)
            # NotAction/NotResource policies are unusual for this CI role; require
            # their condition keys conservatively instead of overlooking them.
            if (action_match or "NotAction" in statement) and (resource_match or "NotResource" in statement):
                for condition in statement.get("Condition", {}).values():
                    keys.update(condition)
    return keys


def decisions(case, response, policy_documents=None):
    expected = {(a, r) for a in case.actions for r in case.resources}
    actual = {}
    results = response.get("EvaluationResults")
    if not isinstance(results, list):
        raise AuditError("Simulation results missing")
    for result in results:
        children = result.get("ResourceSpecificResults")
        if children is None:
            children = [{"EvalResourceName": result.get("EvalResourceName"),
                         "EvalResourceDecision": result.get("EvalDecision"),
                         "MissingContextValues": result.get("MissingContextValues", [])}]
        for child in children:
            pair = (result.get("EvalActionName"), child.get("EvalResourceName"))
            if pair not in expected or pair in actual:
                raise AuditError("Simulation has unexpected or duplicate action/resource results")
            value = child.get("EvalResourceDecision")
            if value not in ("allowed", "implicitDeny", "explicitDeny"):
                raise AuditError("Simulation decision unknown")
            missing = child.get("MissingContextValues", []) + result.get("MissingContextValues", [])
            # Deny results include missing keys from unrelated policies. Positive
            # results must be complete; negatives already supply all relevant keys.
            relevant = set(missing) if policy_documents is None else set(missing) & relevant_context(pair[0], pair[1], policy_documents)
            if case.allow and relevant:
                raise AuditError("Positive simulation has missing relevant context values")
            actual[pair] = value
    if actual.keys() != expected:
        raise AuditError("Simulation action/resource results incomplete (including pagination)")
    return actual


def run_cases(aws, cases, role_arn, policies=None, context_policies=None):
    positive = negative = failures = 0
    if policies is not None and not 1 <= len(policies) <= 10:
        raise AuditError("Custom simulation requires 1..10 policy documents")
    for case in cases:
        source = ["iam", "simulate-principal-policy", "--policy-source-arn", role_arn]
        if policies is not None:
            source = ["iam", "simulate-custom-policy", "--policy-input-list", *[json.dumps(p, separators=(",", ":")) for p in policies]]
        response = aws.call(*source, "--action-names", *case.actions, "--resource-arns", *case.resources,
                            "--context-entries", json.dumps(case.context))
        checked = decisions(case, response, context_policies if context_policies is not None else policies)
        for (action, arn), value in checked.items():
            if case.allow:
                positive += 1
            else:
                negative += 1
            if (value == "allowed") != case.allow:
                failures += 1
                print(f"FAIL {case.label}: {action} {arn} = {value}")
        time.sleep(0.5)  # Batched, sequential IAM requests; CLI retries throttling.
    print(f"Simulation: {positive} allow checks, {negative} deny checks, {failures} failures")
    return failures


def validate_policies(aws, managed, c):
    failures = 0
    for suffix in ("eks-deployment", "runtime-compute", "runtime-iam"):
        policy = managed[c.stem + "-" + suffix]
        if len(json.dumps(policy, separators=(",", ":"))) > 6144:
            raise AuditError("Managed policy exceeds 6144 character limit")
        findings = aws.call("accessanalyzer", "validate-policy", "--policy-type", "IDENTITY_POLICY",
                            "--policy-document", json.dumps(policy)).get("findings")
        if not isinstance(findings, list):
            raise AuditError("Access Analyzer findings missing")
        blocking = [f for f in findings if f.get("findingType") in ("ERROR", "SECURITY_WARNING")]
        failures += len(blocking)
        print(f"Policy validation {suffix}: {len(blocking)} blocking, {len(findings) - len(blocking)} advisory findings")
    return failures


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--account-plan-json", type=Path)
    for name in ("region", "project", "environment", "management-cluster-name", "workload-cluster-name"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    match = re.fullmatch(r"arn:aws:iam::([0-9]{12}):role/([A-Za-z0-9+=,.@_-]+)", args.role_arn)
    if not match or match[1] == "000000000000":
        raise AuditError("An AWS IAM CI role ARN is required; STS session ARNs are not accepted")
    plan = None
    if args.account_plan_json:
        try:
            plan = json.loads(args.account_plan_json.read_text())
        except (OSError, ValueError):
            raise AuditError("Cannot read account plan JSON") from None
        if not isinstance(plan, dict):
            raise AuditError("Account plan JSON must be an object")
    keys = ("aws_region", "project", "environment", "management_cluster_name", "workload_cluster_name")
    supplied = (args.region, args.project, args.environment, args.management_cluster_name, args.workload_cluster_name)
    defaults = ("ap-northeast-2", "iris", "dev", "iris-dev-management", "iris-dev-workload")
    values = []
    for key, flag, default in zip(keys, supplied, defaults):
        if plan is not None:
            value = plan.get("variables", {}).get(key, {}).get("value")
            if not isinstance(value, str) or not value or (flag is not None and flag != value):
                raise AuditError("Plan variable missing, unknown or conflicts with CLI input")
        else:
            value = flag or default
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value):
            raise AuditError("Invalid audit configuration")
        values.append(value)
    c = Config(match[1], *values)
    if c.management == c.workload or match[2] != c.stem + "-github-terraform":
        raise AuditError("Role name or cluster configuration mismatch")
    if plan is not None and plan.get("variables", {}).get("aws_account_id", {}).get("value") != c.account:
        raise AuditError("Plan and role AWS accounts differ")
    return args, c, plan


def main(argv=None):
    try:
        args, c, plan = arguments(argv)
        # Reject bad plan bindings before invoking any AWS tool.
        selected = plan_policies(plan, args.role_arn, c) if plan is not None else None
        aws = Aws(c.region)
        if aws.call("sts", "get-caller-identity").get("Account") != c.account:
            raise AuditError("Local caller AWS account does not match the target role")
        mode = "custom identity policies from account plan" if selected else "live principal policies"
        print(f"Mode: {mode}; no role assumption or AWS changes", flush=True)
        print("Simulation does not verify service requests, CI OIDC, network, quotas or deployment success.", flush=True)
        if selected:
            print("Custom mode excludes permissions boundaries, session policies, resource policies and SCPs.", flush=True)
        managed, context_policies = selected if selected else live_policies(aws, args.role_arn, c)
        policies = context_policies if selected else None
        failures = run_cases(aws, matrix(c), args.role_arn, policies, context_policies)
        failures += validate_policies(aws, managed, c)
        return 1 if failures else 0
    except AuditError as err:
        print(f"Permission audit failed: {err}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, AttributeError):
        print("Permission audit failed: incomplete or malformed AWS/plan response", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
