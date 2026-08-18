# Custos discovery — read-only access to one AWS account.
#
# This module grants Custos permission to read network and identity metadata.
# It grants no write permission anywhere, creates no compute, and installs no
# agent on any host.
#
# Target: applied by a platform engineer in under 30 minutes from the README,
# without a meeting.
#
#   terraform init
#   terraform apply -var="external_id=<the value Custos gave you>"
#
# Then send the role ARN in the output back to Custos.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

variable "external_id" {
  description = "Value supplied by Custos. Prevents the confused deputy problem."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.external_id) >= 16
    error_message = "The external ID must be at least 16 characters."
  }
}

variable "custos_account_id" {
  description = "AWS account Custos assumes this role from."
  type        = string
  default     = "000000000000" # replaced at onboarding
}

variable "name_prefix" {
  type    = string
  default = "custos"
}

variable "flow_log_group" {
  description = "CloudWatch Logs group holding VPC Flow Logs. Created if create_flow_logs is true."
  type        = string
  default     = "/aws/vpc/custos-flowlogs"
}

variable "create_flow_logs" {
  description = <<-EOT
    Whether to create a flow log with the format Custos requires.

    Set false if you already have flow logs; Custos reads whatever you have,
    but coverage of the tcp-flags and pkt-dst-aws-service fields improves both
    accuracy and the ability to attribute a finding.
  EOT
  type        = bool
  default     = false
}

variable "vpc_ids" {
  description = "VPCs to enable flow logs on. Only used when create_flow_logs is true."
  type        = list(string)
  default     = []
}

variable "retention_days" {
  type    = number
  default = 14
}

data "aws_caller_identity" "current" {}
