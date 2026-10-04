# On-prem credential renewal

Reuse the working renewal setup: **keep the existing credentials → extend on-prem
permissions once → wait for Terraform success → sync the existing Argo application**.
No new ServiceAccount, initial API token, management credential Secret, AWS role or
Pod Identity association is required when the existing svc28 renewer is healthy.

| Existing CronJob | Namespace | Schedule (Asia/Seoul) | Updates |
| --- | --- | --- | --- |
| iris-onprem-ecr-renewer-28 | iris-platform | Every minute | Pull Secrets/default SA references in every numeric service namespace; its existing 24-hour K3s API token |
| iris-onprem-argocd-token-renewer | argocd | Every six hours | Existing Argo registration's 48-hour API token |

The ECR job retains its historic `-28` name, but discovers `^svc-[1-9][0-9]*$`,
including svc28, svc33 and new service namespaces. It runs in **management EKS** and
calls the on-prem API through the existing verified TLS/Tailscale proxy connection.
The separate user-registered-server installer path remains under the Control API.

## Existing authentication is reused

- Management identity: `iris-platform/iris-onprem-ecr-renewer-28`, existing Pod Identity
  association `a-heyshsqm3rec51pw0`, AWS role `iris-dev-onprem-ecr-svc-28`.
- Existing protected credential: `iris-platform/iris-onprem-ecr-renew-auth-28`.
  The same on-prem `svc-28/iris-ecr-renewer` SA requests its own fresh API token and
  patches this same Secret. The original SA/self-token Role, role trust, manual
  `PullServiceImage` policy and association are preserved; they are not imported.
- Terraform adds only an `assume-scoped-image-pull` inline policy on the existing
  issuer. It reuses `iris-dev-onprem-ecr-pull` with an additional, separate trust
  statement restricted to the existing issuer's management Pod Identity tags.
  The original Control API trust statement and pull permissions remain intact.
- Every service uses a separate STS session policy scoped to `iris/services/{id}`.
  Only its ECR password reaches the workload namespace, never AWS credentials.
  Renewal uses the earlier ECR/STS expiration minus 15 minutes: role chaining lasts
  at most one hour even when ECR reports a 12-hour token lifetime.
- Management RBAC, NetworkPolicies, proxy, public CA and Argo token job are reused.
  No new bootstrap token is needed while the original renewal remains healthy.

## One-time permission extension and deployment

These instructions describe deployment; local mock/render success does not prove
that IAM, K3s admission, proxy connectivity or real image pulls work. Code approval
is not main-push, Terraform apply, cluster mutation or workflow authorization.

1. **Before changing anything live**, check whether the previous v2 resources were
   ever deployed. Its separate CronJob must stay suspended with no active Jobs.
   If the new IAM roles/association exist in state, removing them can produce deletes:
   inspect the actual plans and obtain deployment/removal authorization before main
   merge or CI execution. Do not delete the working svc28 identity or credentials.
2. On the VM, use the merged repository files and check `sudo k3s --version` and
   `sudo k3s kubectl api-resources --api-group=admissionregistration.k8s.io`.
   Kubernetes >=1.30 with active ValidatingAdmissionPolicy is required. Compare the
   existing public server CA/TLS SAN with `server-ca.crt`. Unsupported K3s upgrades
   are outside this change; do not grant the cluster binding on an unsupported server.
3. Apply the admission guards first:

   ```bash
   sudo k3s kubectl apply -f clusters/onprem-workload/ecr-all-admission.yaml
   ```

   Require all three Fail policies, their Deny bindings, completed type checking and
   no `status.typeChecking.expressionWarnings`. Install only the ClusterRole first:

   ```bash
   awk 'BEGIN {RS="---"; ORS="\n---\n"} $0 !~ /kind: ClusterRoleBinding/' \
     clusters/onprem-workload/ecr-all-rbac.yaml | sudo k3s kubectl apply -f -
   ```

   With temporary namespace-scoped bindings for the **existing** `svc-28/iris-ecr-renewer`
   identity, verify server dry-runs: reserved Docker Secret creation and default-SA
   reference addition are allowed in a numeric test namespace; non-service writes,
   different Secret names/types, removal of old SA refs, and deletion of running,
   unowned or already-referenced Pods are denied by the named admission policy.
   Generic RBAC `Forbidden` alone is not evidence of admission protection. Remove
   only the temporary bindings created by this check, identified by recorded UID.
   After these checks, grant the existing identity the extension:

   ```bash
   sudo k3s kubectl apply -f clusters/onprem-workload/ecr-all-rbac.yaml
   ```

   The existing Argo deployment SA cannot write RBAC or Secrets, so an on-prem
   administrator must perform this extension once. Reads still span the cluster:
   namespace/Pod metadata and named `iris-ecr-pull` reads; no arbitrary Secret listing.
4. Apply foundation's existing-role trust/inline-policy changes, then sync the
   existing `iris-onprem-auth-renewal` Argo application to this revision. Keep its
   prior revision while permissions are being prepared, so automatic sync cannot
   switch the job ahead of IAM/RBAC readiness. Main Terraform CI applies foundation;
   it never applies account. With AdministratorAccess already on the actual CI role,
   account permission updates are not a prerequisite; scoped CI requires the updated
   account policy first. No management association change or credential transfer is needed.
5. Observe the next successful existing `iris-onprem-ecr-renewer-28` Job, or create
   one manually **only after its active Jobs finish**. Prove uncached image pulls for
   svc33 and a real new numeric test service, cross-service repository denial, svc28
   continuity, other registry/SA reference preservation and unchanged Argo behavior.
   Do not run old and new manual Jobs concurrently. No second CronJob needs activation.

## Behavior, inspection and recovery

Namespace discovery is paginated. Terminating/deleted/recreated namespaces, missing
SAs and creation races defer to the next successful run. The default SA's other pull
references and unrelated registry entries are preserved. Namespace UID checks before
writes and resourceVersion preconditions reject detected stale objects. Per-service
errors do not stop other services but make the Job fail. Logs contain bounded statuses
and error types, never tokens or response bodies.

Recovery deletes only Pending image-pull-failing Pods with no running containers,
matching this service's ECR image, missing the pull reference, and a verified Pod →
ReplicaSet → Deployment `app` owner UID chain. Both templates must inherit the default
SA's references. Pod deletion uses UID/resourceVersion preconditions. Running Pods and
Pods already referencing the Secret are not replaced. Unsupported controllers/custom
SAs/explicit reference lists report `UnsupportedWorkload` for operator action.

Inspect the existing Job logs and only `iris.dev/*` expiration/renewal annotations;
never print whole Secrets or all annotations. Schedules use `Forbid`, two retries and
300-second Job deadlines. Discovery is normally the next successful minute run, not
an exact latency guarantee. The original identity still lives in `svc-28`: deleting
that namespace revokes its credentials and breaks renewal. Preserve that namespace/SA.

If API credentials expire after a prolonged outage, bootstrap the **original**
credential using its existing recovery procedure. Removing/recreating its SA revokes
old tokens. No bootstrap is needed for the normal upgrade. To roll back, restore the
existing Job to `MODE=ecr`, hourly schedule, preserving its SA/Secret/association and
app pull Secrets. Wait for active Jobs before manual runs or permission removal.
Restore admission/write scope together; never remove guards while broad write access
remains granted. Leave the Argo token renewal job running.

## Local validation

In an isolated Python environment with `httpx==0.28.1` and `PyYAML==6.0.3`:

```bash
python3 -m unittest discover -s runtime/onprem-auth-renewal -p 'test_*.py'
python3 scripts/tests/test-eks-ci-permissions.py
python3 scripts/tests/test-terraform-ci-changes.py
kubectl kustomize runtime/onprem-auth-renewal
```

Use backend-disabled copies without real state/tfvars/overrides for account/foundation/
management Terraform validate and mock tests. GitOps rendering contains source and
public CA, not bootstrap Secrets. Actual cluster/IAM/ECR checks remain deployment gates.

## Original deployment record: 2026-10-04

The original svc28 ECR and Argo jobs were manually verified: 12-hour ECR auth, 24-hour
renewal API token, 48-hour Argo token, and a Successful registered cluster. The narrow
proxy ingress was corrected and a credential-bearing client-side-apply annotation was
removed by replacing the original dedicated SA and bootstrapping its token at that
initial deployment. This history is not evidence that the later all-service extension
has been applied or that another SA replacement is needed now.
