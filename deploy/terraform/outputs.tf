output "url" {
  value = local.url
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_api" {
  value = aws_ecr_repository.app["api"].repository_url
}

output "ecr_web" {
  value = aws_ecr_repository.app["web"].repository_url
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "app_secret_arn" {
  description = "Put real tokens here (see docs/DEPLOY_AWS.md)."
  value       = aws_secretsmanager_secret.app.arn
}

output "webhook_urls" {
  description = "Paste these into Slack, Azure Bot and Recall."
  value = {
    slack_events  = "${local.url}/api/slack/events"
    teams_message = "${local.url}/api/teams/messages"
    recall        = "${local.url}/api/meetings/webhooks/recall"
  }
}

output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN variable in GitHub."
  value       = try(aws_iam_role.deploy[0].arn, null)
}

# Everything the deploy script needs to run the one-off migration task.
output "deploy_config" {
  value = {
    region          = var.region
    cluster         = aws_ecs_cluster.main.name
    private_subnets = join(",", aws_subnet.private[*].id)
    app_sg          = aws_security_group.app.id
    migrate_family  = aws_ecs_task_definition.migrate.family
    api_family      = aws_ecs_task_definition.api.family
    worker_family   = aws_ecs_task_definition.worker.family
    web_family      = aws_ecs_task_definition.web.family
  }
}

output "cognito_user_pool_id" {
  description = "Invite the team with: aws cognito-idp admin-create-user --user-pool-id <id> --username <email>"
  value       = try(aws_cognito_user_pool.main[0].id, null)
}
