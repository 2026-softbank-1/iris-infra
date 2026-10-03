# On-prem credential renewal

This GitOps application renews credentials for the existing on-prem cluster and service 28. It runs in management EKS, independently of an operator laptop or its AWS profile.

| CronJob | Namespace | Schedule (Asia/Seoul) | Updates |
| --- | --- | --- | --- |
| iris-onprem-ecr-renewer-28 | iris-platform | Every hour, at minute 0 | Service 28 ECR pull token and its own 24-hour K3s API token |
| iris-onprem-argocd-token-renewer | argocd | 00:00, 06:00, 12:00, 18:00 | Existing on-prem Argo credential with a fresh 48-hour token |

ECR authorization is valid for 12 hours and cannot be extended. Each successful hourly run issues a new token. The ECR job gets short-lived AWS credentials through EKS Pod Identity; no permanent AWS key or Argo admin password is stored in either job. The jobs rotate only the configured Secrets and do not restart application workloads.

## Authority and bootstrap

- Existing IAM role `iris-dev-onprem-ecr-svc-28` retains its service-28-only ECR pull policy. Its added Pod Identity trust is restricted to the management cluster ARN, namespace `iris-platform`, and ServiceAccount `iris-onprem-ecr-renewer-28`.
- Pod Identity association: `a-heyshsqm3rec51pw0` in `iris-dev-management`.
- [On-prem RBAC](../../clusters/onprem-workload/auth-renewal-rbac.yaml) permits the ECR renewer to get/patch only `svc-28/iris-ecr-pull` and request a new token only for itself. The existing `iris-argocd` ServiceAccount can request only its own token. These roles do not add new deployment privileges.
- Management RBAC permits each job to get/patch only its own credential Secret: `iris-platform/iris-onprem-ecr-renew-auth-28` or `argocd/iris-onprem-01-cluster`.
- The initial ECR renewal API token is a 24-hour TokenRequest credential stored in the management Secret. It is renewed each hour. The existing Argo registration supplies its own initial token. Bootstrap values are outside Git.
- `server-ca.crt` is the public K3s server CA, not a private key. TLS verification stays enabled; the client uses the certificate's `kubernetes.default.svc` SAN while connecting through the registered egress Service.
- `iris-onprem-auth-renewal-api` permits only these two job identities (namespace plus Pod labels) to reach the API proxy on TCP 6443. Existing controller/server access remains under its original policy.

The ECR configuration is specific to service 28. Adding another service requires its own pull role, narrow on-prem permissions, credential bootstrap, and job configuration.

## Failure handling and inspection

Schedules forbid overlapping scheduled runs. Jobs retry twice and stop after 300 seconds. Secret updates use resourceVersion preconditions, so concurrent operator edits cause a conflict instead of being overwritten. Existing Argo TLS/configuration fields and unrelated registry entries are preserved. Logs contain status and expiration timestamps, never token values or response bodies.

Use the Argo application `iris-onprem-auth-renewal` to inspect both CronJobs and their child Jobs. Operators with the relevant namespace access can also use:

```bash
kubectl -n iris-platform get cronjob iris-onprem-ecr-renewer-28
kubectl -n argocd get cronjob iris-onprem-argocd-token-renewer
kubectl -n iris-platform logs job/<ecr-job-name> -c renew
kubectl -n argocd logs job/<argo-job-name> -c renew
```

Read only the `iris.dev/last-token-renewal`, `iris.dev/api-token-expires-at`, and `iris.dev/ecr-token-expires-at` annotations when inspecting expiration. Do not print complete Secret objects or all annotations. Credential-bearing `kubectl.kubernetes.io/last-applied-configuration` annotations are removed during renewal; bootstrap Secret writes must use create/patch rather than client-side apply.

If jobs cannot run or reach K3s for longer than their current API token lifetime (24 hours for the ECR renewer, 48 hours for Argo), an operator must bootstrap a fresh token. Deleting/recreating the associated on-prem ServiceAccount revokes its old tokens. Rotating the K3s CA also requires updating the public CA here.

## Stop and rollback

1. Set both CronJobs' `spec.suspend` to `true` in `kubernetes.yaml`, commit, and sync this Argo application. A direct live patch alone can be reverted by self-heal.
2. If removing the automation, delete this application's managed resources after schedules are suspended. Bootstrap credential Secrets are not managed by this application and are not pruned with it.
3. Remove association `a-heyshsqm3rec51pw0` and only the IAM trust statement with Sid `EksOnpremEcrRenewal28`. Preserve the existing manual operator trust and ECR pull policy if the app remains deployed.
4. Remove the on-prem renewal RBAC/ServiceAccount and the management ECR bootstrap Secret. Preserve the app's `iris-ecr-pull`, default ServiceAccount pull reference, and Argo cluster registration for manual operation.

## Validation on 2026-10-04

Both jobs were initially suspended, then run manually. ECR renewal completed with a new 12-hour image token and 24-hour API token. Argo renewal completed with a new 48-hour API token and the cluster remained `Successful`.

The initial connection timeout was the existing API-proxy ingress policy, which admitted only the Argo controller/server. A separate, narrow ingress policy now admits the two renewal jobs. Temporary namespace View access used to inspect that policy was removed. The initial ECR bootstrap token whose client-side-apply annotation appeared in a diagnostic response was revoked by replacing its dedicated ServiceAccount, and a fresh token was stored without an annotation copy.

Tests cover preservation of Argo configuration and unrelated registry credentials, wrong AWS role / K3s identity rejection, and resourceVersion conflict handling. The source also passed Kustomize rendering and Python compilation.
