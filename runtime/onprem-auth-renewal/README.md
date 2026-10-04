# On-prem credential renewal

This GitOps application runs in management EKS, independently of an operator laptop or its AWS profile. It includes the existing service-28/Argo jobs and a staged generic reconciler for the same legacy on-prem cluster. The generic job is suspended until the migration below is explicitly deployed and verified.

| CronJob | Namespace | Schedule (Asia/Seoul) | Updates |
| --- | --- | --- | --- |
| iris-onprem-ecr-renewer-28 | iris-platform | Every hour, at minute 0 | Service 28 ECR pull token and its own 24-hour K3s API token |
| iris-onprem-argocd-token-renewer | argocd | 00:00, 06:00, 12:00, 18:00 | Existing on-prem Argo credential with a fresh 48-hour token |
| iris-onprem-ecr-renewer | iris-platform | Every minute; initially suspended | Pull Secrets/default SA references in all numeric service namespaces, and its own 24-hour K3s API token |

ECR authorization is valid for 12 hours and cannot be extended. Each successful legacy hourly run issues a new token. ECR jobs get short-lived AWS credentials through EKS Pod Identity; no permanent AWS key or Argo admin password is stored in these jobs. The original jobs rotate only their configured Secrets and do not restart application workloads. The generic job's conservative expiration and limited failure-Pod recovery are described below.

## Authority and bootstrap

- Existing IAM role `iris-dev-onprem-ecr-svc-28` retains its service-28-only ECR pull policy. Its added Pod Identity trust is restricted to the management cluster ARN, namespace `iris-platform`, and ServiceAccount `iris-onprem-ecr-renewer-28`.
- Pod Identity association: `a-heyshsqm3rec51pw0` in `iris-dev-management`.
- [On-prem RBAC](../../clusters/onprem-workload/auth-renewal-rbac.yaml) permits the ECR renewer to get/patch only `svc-28/iris-ecr-pull` and request a new token only for itself. The existing `iris-argocd` ServiceAccount can request only its own token. These roles do not add new deployment privileges.
- Management RBAC permits each job to get/patch only its own credential Secret: `iris-platform/iris-onprem-ecr-renew-auth-28` or `argocd/iris-onprem-01-cluster`.
- The initial ECR renewal API token is a 24-hour TokenRequest credential stored in the management Secret. It is renewed each hour. The existing Argo registration supplies its own initial token. Bootstrap values are outside Git.
- `server-ca.crt` is the public K3s server CA, not a private key. TLS verification stays enabled; the client uses the certificate's `kubernetes.default.svc` SAN while connecting through the registered egress Service.
- `iris-onprem-auth-renewal-api` permits the original two job identities and the staged generic identity (namespace plus Pod labels) to reach the API proxy on TCP 6443. Existing controller/server access remains under its original policy.

The legacy hourly configuration remains specific to service 28. `all_services.py` removes that service-specific provisioning requirement after the migration: it discovers `^svc-[1-9][0-9]*$`, including existing services 28/33 and later namespaces. The separate user-registered-server installer/Control API path is not changed.

## Failure handling and inspection

Schedules forbid overlapping scheduled runs. Jobs retry twice and stop after 300 seconds. Secret updates use resourceVersion preconditions, so concurrent operator edits cause a conflict instead of being overwritten. Existing Argo TLS/configuration fields and unrelated registry entries are preserved. Logs contain status and expiration timestamps, never token values or response bodies.

Use the Argo application `iris-onprem-auth-renewal` to inspect its CronJobs and their child Jobs. Operators with the relevant namespace access can also use:

```bash
kubectl -n iris-platform get cronjob iris-onprem-ecr-renewer-28
kubectl -n argocd get cronjob iris-onprem-argocd-token-renewer
kubectl -n iris-platform logs job/<ecr-job-name> -c renew
kubectl -n argocd logs job/<argo-job-name> -c renew
```

Read only the `iris.dev/last-token-renewal`, `iris.dev/api-token-expires-at`, and `iris.dev/ecr-token-expires-at` annotations when inspecting expiration. Do not print complete Secret objects or all annotations. Credential-bearing `kubectl.kubernetes.io/last-applied-configuration` annotations are removed during renewal; bootstrap Secret writes must use create/patch rather than client-side apply.

If jobs cannot run or reach K3s for longer than their current API token lifetime (24 hours for the ECR renewer, 48 hours for Argo), an operator must bootstrap a fresh token. Deleting/recreating the associated on-prem ServiceAccount revokes its old tokens. Rotating the K3s CA also requires updating the public CA here.

## Legacy stop and rollback

1. Set both CronJobs' `spec.suspend` to `true` in `kubernetes.yaml`, commit, and sync this Argo application. A direct live patch alone can be reverted by self-heal.
2. If removing the automation, delete this application's managed resources after schedules are suspended. Bootstrap credential Secrets are not managed by this application and are not pruned with it.
3. Remove association `a-heyshsqm3rec51pw0` and only the IAM trust statement with Sid `EksOnpremEcrRenewal28`. Preserve the existing manual operator trust and ECR pull policy if the app remains deployed.
4. Remove the on-prem renewal RBAC/ServiceAccount and the management ECR bootstrap Secret. Preserve the app's `iris-ecr-pull`, default ServiceAccount pull reference, and Argo cluster registration for manual operation.

## Validation on 2026-10-04

Both jobs were initially suspended, then run manually. ECR renewal completed with a new 12-hour image token and 24-hour API token. Argo renewal completed with a new 48-hour API token and the cluster remained `Successful`.

The initial connection timeout was the existing API-proxy ingress policy, which admitted only the Argo controller/server. A separate, narrow ingress policy now admits the two renewal jobs. Temporary namespace View access used to inspect that policy was removed. The initial ECR bootstrap token whose client-side-apply annotation appeared in a diagnostic response was revoked by replacing its dedicated ServiceAccount, and a fresh token was stored without an annotation copy.

Tests cover preservation of Argo configuration and unrelated registry credentials, wrong AWS role / K3s identity rejection, and resourceVersion conflict handling. The source also passed Kustomize rendering and Python compilation.

## Generic namespace reconciliation

The minute job enumerates namespaces with pagination and skips terminating namespaces.
It creates missing `iris-ecr-pull` Docker Secrets, preserves unrelated registry entries,
and connects the default SA without removing its other pull references. Namespace UID
checks before writes and resourceVersion preconditions reject detected stale objects; creation conflicts and
an SA that is not ready yet are deferred to the next run. Errors in one namespace do
not stop the others; the job exits unsuccessfully if any namespace has an actual error.
Results contain namespace names/status/error types, never credentials or HTTP bodies.

The dedicated issuer role `iris-dev-onprem-ecr-renewer` only assumes
`iris-dev-onprem-ecr-renewal-pull`. For each namespace it supplies an STS session policy
allowing `GetAuthorizationToken` and downloads from its exact `iris/services/{id}`
repository. Only the resulting ECR password reaches that namespace. The role session
is at most one hour; renewal uses the **earlier** ECR/STS expiration with a 15-minute
margin, even if ECR advertises 12 hours. A current Secret is reused only if its recorded
role, service ID and both expiration timestamps match the generic configuration.

Discovery normally starts at the next successful minute run. Scheduler delays,
`Forbid`, retries and API/AWS outages mean this is not an exact one-minute SLA.
Namespace creation remains Argo/admin-owned; the reconciler cannot create namespaces.

When an app Pod was created before its SA acquired the reference, kubelet retries alone
cannot add it to that existing Pod. Recovery replaces only Pending image-pull-failing
Pods with no running containers, using the default SA, the matching service ECR image,
and a verified Pod -> ReplicaSet -> Deployment `app` owner UID chain. Both controller
templates must inherit pull references from the default SA. Unsupported cases are logged
as `UnsupportedWorkload`. Deletion has Pod UID and
resourceVersion preconditions. Running Pods and Pods that already reference the Secret
are left alone; ordinary credential renewal does not restart applications. Custom SAs,
explicit controller pull lists and other controllers are reported for operator action.

## Deploying the generic reconciler (separate authorization)

These are deployment instructions, not evidence that the change is live. Do not push
main, run Terraform apply or change either cluster under code-only approval. Main CI
automatically applies foundation/management inputs. Preserve the manually managed
svc28 IAM role/association for rollback; the two new roles/association are separately
Terraform-owned.

1. On the VM, check `sudo k3s --version` and
   `sudo k3s kubectl api-resources --api-group=admissionregistration.k8s.io`.
   Kubernetes >=1.30 with active ValidatingAdmissionPolicy is required. If unavailable,
   stop before granting the new ClusterRoleBinding; upgrading/restarting K3s is outside
   this change. Verify the configured public `server-ca.crt` and TLS SAN against the
   existing VM registration without printing kubeconfig, token or private keys.
2. An administrator applies account/aws's updated **existing** Control API deployment
   policy first, then foundation's two new IAM roles, then management's dedicated Pod
   Identity association. The runtime is intentionally bound to iris/dev in account
   187069338876, region ap-northeast-2. Inspect plans for unrelated changes and role
   replacement; never copy local state/tfvars/plan into the test or review artifacts.
3. Apply `clusters/onprem-workload/ecr-all-admission.yaml` on the VM. Require all three
   policies and their `validationActions: [Deny]` bindings. Inspect each policy's
   `status.typeChecking.expressionWarnings`; any warning or admission error blocks
   rollout. Apply the Namespace/SA/Role/RoleBinding/ClusterRole documents from
   `ecr-all-rbac.yaml`, holding back its **last ClusterRoleBinding**.
4. Before granting the cluster binding, test admission using temporary narrow
   RoleBindings for `iris-system/iris-ecr-renewer` in a numeric test service namespace
   and a non-service test namespace. Use fake Docker data and `--dry-run=server` with
   impersonation. Prove reserved Secret creation/default-SA reference addition is
   allowed in the numeric namespace; non-service writes, a different Secret name/type,
   removal of existing SA refs, and deletion of running/unowned/already-referenced Pods
   must be denied. DELETE admission uses `oldObject`. A generic RBAC `Forbidden` is not
   proof of the admission guard: check the admission denial identifies the policy.
   Remove only this run's temporary bindings by recorded UID; then grant the last
   ClusterRoleBinding. Admission governs writes only. Named `iris-ecr-pull` reads,
   namespace enumeration and Pod metadata reads still span the cluster under RBAC;
   no unrelated Secret listing/read privilege is granted.
5. Issue a 24-hour API token for the new on-prem SA into a protected file:

   ```bash
   umask 077
   IRIS_RENEW_TOKEN=$(mktemp)
   sudo k3s kubectl -n iris-system create token iris-ecr-renewer \
     --duration=24h > "$IRIS_RENEW_TOKEN"
   ```

   Verify its subject and actual expiration locally without printing the JWT. Transfer
   it through the established protected channel. On the management operator machine,
   use an explicit management kubeconfig and `create` (or guarded patch if the Secret
   already exists), never client-side apply:

   ```bash
   kubectl --kubeconfig "$IRIS_MGMT_KUBECONFIG" -n iris-platform \
     create secret generic iris-onprem-ecr-renew-auth \
     --from-literal=server=https://iris-onprem-api.argocd.svc.cluster.local:6443 \
     --from-file=token="$IRIS_RENEW_TOKEN"
   ```

   The file variable is the protected local copy on each machine. Delete those specific
   transfer files after bootstrap; do not print whole Secrets/all annotations. If an API
   token has already expired, self-renewal cannot recover it; admin bootstrap is required.
6. Sync the GitOps application with the generic CronJob still suspended. Its proxy
   ingress/egress rules admit only the new job identity in addition to the original
   jobs. Change the legacy hourly CronJob's `suspend` to true **in Git**, sync and wait
   until its active child Jobs finish. Suspension does not stop running Jobs. Prevent
   manual legacy executions during migration. Do not run both writers against svc28.
7. Create one explicitly approved manual Job from the generic CronJob, inspect its
   bounded results, and prove actual uncached image pulls for service 33 and a real
   numeric test service/repository. Validate that each issued service token is denied
   when pulling another service repository. Check valid existing svc28 operation,
   failure-Pod replacement, running-Pod preservation, other registry/SA references and
   unchanged Argo TLS/auth behavior. Mock success is not a substitute for these checks.
8. Only after successful pilot checks set the generic `all-services.yaml` CronJob's
   `suspend: false` in Git and sync. Leave the legacy hourly job suspended. Observe
   subsequent scheduled Jobs and Secret expiration annotations; add another service
   namespace to verify automatic discovery without provisioning another IAM role/job.

### Generic rollback

Suspend the generic job through GitOps and wait for its active Jobs to finish. Revoke
the on-prem generic ClusterRoleBinding **before** removing admission guards. Preserve
valid per-service pull Secrets and SA references for manual operation. If re-enabling
the legacy svc28 job, bootstrap its original API credential first if expired, then
unsuspend it through GitOps; it resumes only svc28. The Argo token job remains enabled.
Remove only the new Pod Identity association/roles after the generic job has stopped.

### Local checks

Install `httpx==0.28.1` and `PyYAML==6.0.3` in an isolated test environment, then run:

```bash
python3 -m unittest discover -s runtime/onprem-auth-renewal -p 'test_*.py'
python3 scripts/tests/test-eks-ci-permissions.py
python3 scripts/tests/test-terraform-ci-changes.py
kubectl kustomize runtime/onprem-auth-renewal
```

Kustomize output includes ConfigMap source and public CA, not bootstrap Secrets. Use
backend-disabled copies without real state/tfvars/overrides for account/foundation/
management Terraform validate and mock tests. CI installs the Python dependencies for
runtime-only changes too. These checks do not prove K3s admission support, Pod Identity,
TLS/proxy connectivity or ECR session-policy enforcement on the real clusters.
