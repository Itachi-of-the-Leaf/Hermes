# Alarms -> SNS -> email. Logs are JSON, so log-based metrics need no extra agent.

resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alb_dim = { LoadBalancer = aws_lb.main.arn_suffix }
  ns      = "Coordinator/${var.environment}"
}

# ---------------------------------------------------------------- metrics from JSON logs

resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each       = toset(["api", "worker"])
  name           = "${local.name}-${each.key}-errors"
  log_group_name = aws_cloudwatch_log_group.app[each.key].name
  pattern        = "{ $.level = \"ERROR\" }"
  metric_transformation {
    name          = "AppErrors"
    namespace     = local.ns
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "model_failures" {
  for_each       = toset(["api", "worker"])
  name           = "${local.name}-${each.key}-model-failures"
  log_group_name = aws_cloudwatch_log_group.app[each.key].name
  pattern        = "{ ($.logger = \"jev\" || $.logger = \"llm\") && $.level = \"WARNING\" }"
  metric_transformation {
    name          = "ModelFailures"
    namespace     = local.ns
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "send_failures" {
  for_each       = toset(["api", "worker"])
  name           = "${local.name}-${each.key}-send-failures"
  log_group_name = aws_cloudwatch_log_group.app[each.key].name
  pattern        = "{ $.logger = \"channels\" && $.level = \"WARNING\" }"
  metric_transformation {
    name          = "MessageSendFailures"
    namespace     = local.ns
    value         = "1"
    default_value = "0"
  }
}

# ---------------------------------------------------------------- alarms

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  alarm_description   = "API returning server errors"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = merge(local.alb_dim, { TargetGroup = aws_lb_target_group.api.arn_suffix })
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "${local.name}-api-p95-latency"
  alarm_description   = "Dashboard API p95 above 1.5 s"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = merge(local.alb_dim, { TargetGroup = aws_lb_target_group.api.arn_suffix })
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1.5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy" {
  for_each            = { api = aws_lb_target_group.api.arn_suffix, web = aws_lb_target_group.web.arn_suffix }
  alarm_name          = "${local.name}-${each.key}-unhealthy-targets"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = merge(local.alb_dim, { TargetGroup = each.value })
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_down" {
  alarm_name          = "${local.name}-worker-not-running"
  alarm_description   = "No worker: Telegram replies, check-ins and meeting summaries have stopped"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.main.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "app_errors" {
  alarm_name          = "${local.name}-app-errors"
  namespace           = local.ns
  metric_name         = "AppErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "model_failures" {
  alarm_name          = "${local.name}-model-failures"
  alarm_description   = "Jev or the LLM keep failing; replies are falling back to rules"
  namespace           = local.ns
  metric_name         = "ModelFailures"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name}-rds-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.name}-rds-free-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648 # 2 GB
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  alarm_name          = "${local.name}-redis-memory"
  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  dimensions          = { ReplicationGroupId = aws_elasticache_replication_group.main.id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# ---------------------------------------------------------------- dashboard

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "API requests and errors", region = var.region, stat = "Sum", period = 60
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.api.arn_suffix],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", ".", "."],
            [".", "HTTPCode_Target_4XX_Count", ".", ".", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "API latency (p50 / p95)", region = var.region, period = 60
          metrics = [
            ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.main.arn_suffix, "TargetGroup", aws_lb_target_group.api.arn_suffix, { stat = "p50" }],
            ["...", { stat = "p95" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title   = "App errors, model failures, send failures", region = var.region, stat = "Sum", period = 300
          metrics = [[local.ns, "AppErrors"], [".", "ModelFailures"], [".", "MessageSendFailures"]]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title = "ECS CPU %", region = var.region, stat = "Average", period = 60
          metrics = [
            ["ECS/ContainerInsights", "CpuUtilized", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", "api"],
            ["...", "worker"],
            ["...", "web"],
          ]
        }
      },
      {
        type = "log", x = 0, y = 12, width = 24, height = 8
        properties = {
          title  = "Recent errors (all services)"
          region = var.region
          query  = "SOURCE '${aws_cloudwatch_log_group.app["api"].name}' | SOURCE '${aws_cloudwatch_log_group.app["worker"].name}' | fields ts, role, logger, msg, trace_id | filter level = 'ERROR' or level = 'WARNING' | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}
