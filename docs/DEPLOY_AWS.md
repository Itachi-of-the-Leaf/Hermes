# Deploying to AWS

Containers on ECS Fargate behind an Application Load Balancer, Postgres on RDS, Redis on ElastiCache, secrets in Secrets Manager, logs/metrics in CloudWatch, traces in X-Ray. Everything is in `deploy/terraform`; deploys run from GitHub Actions.

## What gets created

| Piece | Service | Notes |
| --- | --- | --- |
| Network | VPC, 2 public + 2 private subnets, 1 NAT gateway | Tasks and databases have no public IPs |
| Entry | ALB with HTTPS (ACM), HTTP→HTTPS redirect | Same origin for app and API; 300 s idle timeout for live updates |
| Login | Cognito user pool on the ALB | Dashboard and `/api/*` need login; Slack/Teams/Recall webhooks don't (they verify signatures) |
| Compute | ECS Fargate, ARM64 (Graviton) | api (2–6, autoscaled), worker (exactly 1), web (2) |
| Data | RDS Postgres 16 (encrypted, 7-day backups, deletion protection) | Password managed by RDS in Secrets Manager |
| Cache/queue | ElastiCache Redis 7 (TLS, encrypted) | Live-update fan-out and background jobs |
| Images | ECR (immutable tags, scan on push, keep 30) | Tag = git SHA |
| Secrets | Secrets Manager `coordinator-prod/app` | Bot tokens and API keys |
| Observability | CloudWatch logs (JSON), metric filters, alarms → SNS email, dashboard; X-Ray via ADOT sidecar | See OBSERVABILITY.md |
| CI/CD | GitHub OIDC role | No AWS keys in GitHub |

Rough monthly cost at demo size (2 api + 1 worker + 2 web on 0.25–0.5 vCPU, t4g.micro RDS and Redis, 1 NAT, 1 ALB): roughly USD 90–130, most of it NAT gateway, ALB and Fargate. Check the AWS Pricing Calculator for your region before relying on it.

## First deploy

Prerequisites: AWS account, Terraform ≥ 1.6, AWS CLI, Docker with buildx, a domain in Route 53 (or a certificate for one).

```bash
# 1. state bucket (once)
aws s3 mb s3://<your-tf-state-bucket> --region ap-south-1

# 2. infrastructure
cd deploy/terraform
cat > prod.tfvars <<EOF
domain_name    = "coordinator.example.com"
hosted_zone_id = "Z0123456789ABC"
alarm_email    = "you@example.com"
github_repo    = "your-org/coordinator"
EOF
terraform init -backend-config="bucket=<your-tf-state-bucket>" \
  -backend-config="key=coordinator/prod.tfstate" -backend-config="region=ap-south-1" \
  -backend-config="use_lockfile=true"
terraform apply -var-file=prod.tfvars
```

The first apply creates services that point at the `latest` tag, which doesn't exist yet; they stay unhealthy until step 4.

```bash
# 3. secrets: fill in the tokens you use (empty = that integration is off)
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw app_secret_arn)" \
  --secret-string file://secrets.json      # {"TELEGRAM_BOT_TOKEN": "...", "SLACK_BOT_TOKEN": "...", ...}
```

Keep `WEBHOOK_TOKEN` (Terraform generated it) when you overwrite the secret: copy it from the current value first.

```bash
# 4. GitHub: Settings → Secrets and variables → Actions → Variables
#    AWS_DEPLOY_ROLE_ARN = terraform output deploy_role_arn
#    AWS_REGION, ECS_CLUSTER, PRIVATE_SUBNETS, APP_SG = terraform output deploy_config
# then push to main (or run the Deploy workflow by hand)

# 5. log in to the dashboard
aws cognito-idp admin-create-user --user-pool-id "$(terraform output -raw cognito_user_pool_id)" \
  --username lead@example.edu --user-attributes Name=email,Value=lead@example.edu
```

Finally paste `terraform output webhook_urls` into the Slack app and the Azure Bot messaging endpoint. Recall needs nothing: the app passes its webhook URL each time it sends a bot into a call.

## Every deploy (GitHub Actions)

```
push to main
  CI:      ruff + pytest + alembic upgrade/check on Postgres + npm build + terraform validate
  Deploy:  build arm64 images (cached) → push to ECR with the commit SHA
           register task definitions with the new image
           run the migrate task; stop here if it fails (services untouched)
           update api, worker, web → ECS rolls tasks behind the ALB
           wait for services-stable; the circuit breaker rolls back a failing release
```

- **Zero downtime:** api and web keep old tasks until new ones pass ALB health checks.
- **Worker:** deploys stop-then-start (min 0 %, max 100 %) so two Telegram pollers never overlap; expect a few seconds of delay in replies.
- **Migrations:** expand-then-contract. Add columns as nullable first, ship code that uses them, drop old columns in a later release. Old and new tasks run side by side during a rollout.
- **Rollback:** redeploy the previous commit (`workflow_dispatch` on that SHA) or `aws ecs update-service --task-definition <previous revision>`.
- **Gating:** the `production` GitHub environment can require a reviewer before deploys.

## Operating it

```bash
# shell into a running API task (ECS Exec is enabled)
aws ecs execute-command --cluster coordinator-prod --task <task-id> --container api --interactive --command "/bin/sh"

# send check-ins on demand, from a one-off task
aws ecs run-task ... --overrides '{"containerOverrides":[{"name":"migrate","command":["python","-m","app.cli","checkin"]}]}'

# tail logs
aws logs tail /ecs/coordinator-prod/api --follow --format short
```

## Production hardening still worth doing

- One NAT gateway per AZ, and `db_multi_az = true` (also gives Redis a replica with failover).
- AWS WAF on the ALB with the managed common rule set and rate limiting on webhook paths.
- Pin the ADOT collector image to a version instead of `latest`.
- Secrets rotation for the RDS password (managed by RDS) needs a task restart to pick up; schedule one after rotations.
- Backups: RDS keeps 7 days; add AWS Backup for longer retention if grades depend on the data.
