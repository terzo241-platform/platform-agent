# Incident Response Runbook

## Cloud Run Service Down

1. Check service status: `gcloud run services describe SERVICE --region REGION`
2. Check recent revisions: `gcloud run revisions list --service SERVICE`
3. Check logs: `gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=SERVICE" --limit 50`
4. If OOM: Increase memory limit in Terraform config, redeploy
5. If crash loop: Rollback to last known good revision

## ArgoCD Out of Sync

1. Check sync status via agent: "get status of APP_NAME"
2. Compare desired vs live state in ArgoCD UI
3. If intentional drift: sync manually with `--prune=false`
4. If unintentional: investigate who changed the live state
5. Never auto-prune production — always manual review

## Pipeline Stuck

1. Check if the runner/node has capacity
2. Check for resource locks (Terraform state lock, Atlantis lock)
3. Cancel and re-trigger if no side effects
4. If Tekton: check PipelineRun conditions for timeout
5. If GHA: check runner group quotas
