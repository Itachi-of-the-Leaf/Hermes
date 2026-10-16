# One ALB, same origin for the dashboard and its API (no CORS, the login cookie covers both).
#   /api/slack/*, /api/teams/*, /api/meetings/webhooks/*  -> api   (public: each verifies its own signature)
#   /api/*                                                -> api   (behind Cognito login)
#   everything else                                       -> web   (behind Cognito login)

resource "aws_lb" "main" {
  name                       = local.name
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  idle_timeout               = 300 # live dashboard streams (SSE) send a keep-alive every 15 s
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "api" {
  name                 = "${local.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 30
  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group" "web" {
  name                 = "${local.name}-web"
  port                 = 80
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
}

# ---------------------------------------------------------------- TLS + DNS

resource "aws_acm_certificate" "main" {
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "cert" {
  for_each = var.domain_name != "" && var.hosted_zone_id != "" ? {
    for o in aws_acm_certificate.main[0].domain_validation_options : o.domain_name => o
  } : {}
  zone_id = var.hosted_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "main" {
  count                   = var.domain_name != "" && var.hosted_zone_id != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.main[0].arn
  validation_record_fqdns = [for r in aws_route53_record.cert : r.fqdn]
}

resource "aws_route53_record" "app" {
  count   = var.domain_name != "" && var.hosted_zone_id != "" ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

locals {
  https    = var.domain_name != ""
  auth_on  = var.enable_auth && local.https # ALB authentication requires HTTPS
  listener = local.https ? aws_lb_listener.https[0].arn : aws_lb_listener.http_forward[0].arn
}

# ---------------------------------------------------------------- listeners

resource "aws_lb_listener" "http_redirect" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "http_forward" {
  count             = local.https ? 0 : 1
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener" "https" {
  count             = local.https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.main[0].arn
  depends_on        = [aws_acm_certificate_validation.main]

  dynamic "default_action" {
    for_each = local.auth_on ? [1] : []
    content {
      type  = "authenticate-cognito"
      order = 1
      authenticate_cognito {
        user_pool_arn       = aws_cognito_user_pool.main[0].arn
        user_pool_client_id = aws_cognito_user_pool_client.main[0].id
        user_pool_domain    = aws_cognito_user_pool_domain.main[0].domain
      }
    }
  }
  default_action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "webhooks" {
  listener_arn = local.listener
  priority     = 10
  condition {
    path_pattern { values = ["/api/slack/*", "/api/teams/*", "/api/meetings/webhooks/*"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = local.listener
  priority     = 20
  condition {
    path_pattern { values = ["/api/*"] }
  }
  dynamic "action" {
    for_each = local.auth_on ? [1] : []
    content {
      type  = "authenticate-cognito"
      order = 1
      authenticate_cognito {
        user_pool_arn              = aws_cognito_user_pool.main[0].arn
        user_pool_client_id        = aws_cognito_user_pool_client.main[0].id
        user_pool_domain           = aws_cognito_user_pool_domain.main[0].domain
        on_unauthenticated_request = "deny" # API calls get 401 instead of a login redirect
      }
    }
  }
  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ---------------------------------------------------------------- Cognito login for the dashboard

resource "aws_cognito_user_pool" "main" {
  count                    = local.auth_on ? 1 : 0
  name                     = local.name
  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]
  admin_create_user_config { allow_admin_create_user_only = true } # the lead invites the team
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  count        = local.auth_on ? 1 : 0
  domain       = "${local.name}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.main[0].id
}

resource "aws_cognito_user_pool_client" "main" {
  count                                = local.auth_on ? 1 : 0
  name                                 = "alb"
  user_pool_id                         = aws_cognito_user_pool.main[0].id
  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["https://${var.domain_name}/oauth2/idpresponse"]
}
