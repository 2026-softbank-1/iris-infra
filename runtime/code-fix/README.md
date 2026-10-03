# Code Fix runtime

Apply `argocd.yaml` once after merging this runtime into iris-infra main. It creates a dedicated Argo Application using the existing infra repository credentials, scoped to iris-platform. It reads only kubernetes.yaml. The source image is iris-code-fix-agent cfcbd86, manually pushed to the immutable ECR repository iris/code-fix-agent, and pinned by digest. ECR currently remains operator-managed rather than Terraform-managed. Updating the image requires building/pushing an immutable tag and committing its digest here.

The candidate service owns no GitHub or AWS credentials and has no Kubernetes API token. It receives an operator-created `iris-code-fix-agent` Secret containing `API_KEY` (random, at least 32 ASCII characters) and `OPENAI_API_KEY`. The model/rates/limits and exact source hosts are in its ConfigMap. No Secret data enters this repository.

The service is private on port 8000. Network policy permits WAS API ingress, DNS and public HTTPS egress. It uses one Recreate replica and an existing gp3 StorageClass/PVC so SQLite request receipts and candidate artifacts survive replacement. Argo never prunes the data PVC. Both the image and settings are declarative; normal WAS or baseline upgrades do not own these resources.

Set these keys in the operator-owned `iris-platform-was-env` Secret:

- `REPAIR_AGENT_URL=http://iris-platform-code-fix-agent.iris-platform.svc.cluster.local:8000`
- `REPAIR_AGENT_API_KEY`: same bytes as the candidate service API_KEY
- `REPAIR_AGENT_SOURCE_HOSTS`: same exact source host allowlist as the candidate ConfigMap

Restart the WAS API after updating the Secret. Existing model credentials and WAS authentication remain separate. Do not put the GitHub installation token in either model or generation service. The model rates ($2 input, $0.10 cached input, $10 output per million tokens) match [GPT-6.1 Sol standard short-context pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol). USD 1, 4096 output tokens, 120-second model timeout and 180-second graceful termination are configured.

Verify server dry-run of both manifests, Argo health, PVC binding, candidate healthz 200, WAS readyz 204, and an owned persisted repair attempt. PR publication/merge are explicit subsequent WAS actions. A candidate is not a successful build or deployment.
