# Optional: create flow logs in the format Custos reads.
#
# The format string is the one the collector parses, and it is duplicated in
# collector/internal/flowlogs/parse.go as LogFormat. A test in that package
# compares the two so they cannot drift — a mismatch here would produce a scan
# that silently found nothing.

locals {
  # Keep in sync with flowlogs.LogFormat.
  log_format = join(" ", [
    "$${version}", "$${account-id}", "$${interface-id}", "$${srcaddr}", "$${dstaddr}",
    "$${srcport}", "$${dstport}", "$${protocol}", "$${packets}", "$${bytes}",
    "$${start}", "$${end}", "$${action}", "$${log-status}", "$${vpc-id}",
    "$${subnet-id}", "$${flow-direction}", "$${pkt-dst-aws-service}", "$${tcp-flags}",
  ])
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  count             = var.create_flow_logs ? 1 : 0
  name              = var.flow_log_group
  retention_in_days = var.retention_days

  tags = {
    ManagedBy = "custos"
  }
}

data "aws_iam_policy_document" "flow_log_assume" {
  count = var.create_flow_logs ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  count              = var.create_flow_logs ? 1 : 0
  name               = "${var.name_prefix}-flow-logs-delivery"
  assume_role_policy = data.aws_iam_policy_document.flow_log_assume[0].json
}

resource "aws_iam_role_policy" "flow_logs" {
  count = var.create_flow_logs ? 1 : 0
  name  = "${var.name_prefix}-flow-logs-delivery"
  role  = aws_iam_role.flow_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams",
      ]
      Resource = "${aws_cloudwatch_log_group.flow_logs[0].arn}:*"
    }]
  })
}

resource "aws_flow_log" "custos" {
  for_each = var.create_flow_logs ? toset(var.vpc_ids) : toset([])

  vpc_id                   = each.value
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[0].arn
  iam_role_arn             = aws_iam_role.flow_logs[0].arn
  log_format               = local.log_format
  max_aggregation_interval = 60

  tags = {
    ManagedBy = "custos"
  }
}
