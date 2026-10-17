variable "project" {
  type    = string
  default = "coordinator"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  description = "Mumbai keeps data in India (DPDP Act) and latency low for Indian users."
  type        = string
  default     = "ap-south-1"
}

variable "domain_name" {
  description = "e.g. coordinator.example.com. Leave empty to use the ALB's DNS name over HTTP (testing only)."
  type        = string
  default     = ""
}

variable "hosted_zone_id" {
  description = "Route 53 zone for domain_name; used for the certificate and the DNS record."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Image tag deployed by Terraform on first apply. CI/CD updates services after that."
  type        = string
  default     = "latest"
}

variable "enable_auth" {
  description = "Put the dashboard behind a Cognito login at the load balancer. Webhooks stay public (they verify signatures)."
  type        = bool
  default     = true
}

variable "alarm_email" {
  description = "Where CloudWatch alarms are sent."
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "owner/repo allowed to deploy through GitHub Actions OIDC. Empty to skip."
  type        = string
  default     = ""
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_multi_az" {
  description = "Standby in a second AZ. Turn on for real production; off keeps a demo cheap."
  type        = bool
  default     = false
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "api_desired_count" {
  type    = number
  default = 2
}

variable "api_max_count" {
  type    = number
  default = 6
}

variable "web_desired_count" {
  type    = number
  default = 2
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "app_env" {
  description = "Non-secret app settings passed to the API and worker."
  type        = map(string)
  default = {
    MODE                  = "auto"
    AUTO_APPLY_CONFIDENCE = "0.75"
    COMPLETE_CONFIDENCE   = "0.85"
    FOLLOWUP_INTERVAL_MIN = "120"
    STALE_AFTER_MIN       = "240"
    JEV_MODEL             = "typesafe/jev-1.14"
    LLM_BASE_URL          = "https://opencode.ai/zen/v1"
    LLM_MODEL             = ""
    SLACK_PROJECT_CHANNEL = ""
    SLACK_SUMMARY_CHANNEL = ""
    RECALL_REGION         = "us-east-1"
  }
}
