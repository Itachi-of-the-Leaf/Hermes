# ---------------------------------------------------------------- Postgres (RDS)

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                      = local.name
  engine                          = "postgres"
  engine_version                  = "16"
  instance_class                  = var.db_instance_class
  allocated_storage               = 20
  max_allocated_storage           = 100
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "coordinator"
  username                        = "coordinator"
  manage_master_user_password     = true # password lives in an RDS-managed Secrets Manager secret
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.db.id]
  multi_az                        = var.db_multi_az
  publicly_accessible             = false
  backup_retention_period         = 7
  copy_tags_to_snapshot           = true
  auto_minor_version_upgrade      = true
  deletion_protection             = var.environment == "prod"
  skip_final_snapshot             = var.environment != "prod"
  final_snapshot_identifier       = "${local.name}-final"
  enabled_cloudwatch_logs_exports = ["postgresql"]
}

# ---------------------------------------------------------------- Redis (ElastiCache)
# Event bus for live dashboard updates across API tasks, and the background job queue.

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = local.name
  description                = "Coordinator event bus and job queue"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  port                       = 6379
  num_cache_clusters         = var.db_multi_az ? 2 : 1
  automatic_failover_enabled = var.db_multi_az
  multi_az_enabled           = var.db_multi_az
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true # app connects with rediss://
}

# ---------------------------------------------------------------- container images

resource "aws_ecr_repository" "app" {
  for_each             = toset(["api", "web"])
  name                 = "${local.name}-${each.key}"
  image_tag_mutability = "IMMUTABLE" # every deploy is a new git-SHA tag; rollbacks are exact
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 30 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 30 }
      action       = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------- app secrets
# Terraform creates the secret with placeholders. Put real values in with:
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string file://secrets.json
# Terraform ignores later changes, so tokens never live in state after that.

resource "random_password" "webhook" {
  length  = 40
  special = false
}

locals {
  secret_keys = [
    "TELEGRAM_BOT_TOKEN", "LEAD_TELEGRAM_ID",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET",
    "TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_TENANT_ID",
    "RECALL_API_KEY", "WEBHOOK_TOKEN",
    "OPENROUTER_API_KEY", "LLM_API_KEY",
  ]
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(merge(
    { for k in local.secret_keys : k => "" },
    { WEBHOOK_TOKEN = random_password.webhook.result },
  ))
  lifecycle { ignore_changes = [secret_string] }
}
