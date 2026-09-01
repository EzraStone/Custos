# Optional: run the collector on a schedule as an ECS task.
#
# Off by default. A first scan is run by hand from a laptop, and asking a
# customer to stand up a scheduled task before they have seen a single finding
# is asking for infrastructure review before value — which is the wrong order
# and the one most likely to stall.
#
# Turn this on when they want continuous monitoring, which is the conversation
# where they are already convinced.

variable "enable_scheduled_collection" {
  description = "Run the collector on a schedule in this account."
  type        = bool
  default     = false
}

variable "collector_image" {
  description = "Container image for the collector."
  type        = string
  default     = ""
}

variable "collection_interval" {
  description = <<-EOT
    How often to collect. Hourly is the default.

    The collector tracks a cursor, so a shorter interval never loses data and a
    longer one never gaps — the trade is freshness against the number of runs.
    If the collector reports shortened windows, this is too long for the
    account's traffic volume.
  EOT
  type    = string
  default = "rate(1 hour)"
}

variable "collector_subnet_ids" {
  type    = list(string)
  default = []
}

variable "collector_security_group_ids" {
  type    = list(string)
  default = []
}

variable "custos_endpoint" {
  description = "https URL of the Custos control plane."
  type        = string
  default     = ""
}

variable "custos_token_secret_arn" {
  description = <<-EOT
    Secrets Manager ARN holding the collector's token.

    The token is passed by reference rather than by value. A token in a task
    definition is a token in every `describe-task-definition` call, in
    CloudTrail, and in the Terraform state file — none of which are places a
    credential should be readable.
  EOT
  type    = string
  default = ""
}

data "aws_region" "current" {}

locals {
  scheduled = var.enable_scheduled_collection && var.collector_image != ""
}

# The task role is the discovery role itself. The collector needs exactly the
# read permissions already granted above and nothing more, and a second role
# with the same policy would be one more thing to keep in sync.
resource "aws_ecs_cluster" "collector" {
  count = local.scheduled ? 1 : 0
  name  = "${var.name_prefix}-collector"

  tags = {
    ManagedBy = "custos"
  }
}

resource "aws_cloudwatch_log_group" "collector" {
  count             = local.scheduled ? 1 : 0
  name              = "/custos/collector"
  retention_in_days = var.retention_days

  tags = {
    ManagedBy = "custos"
  }
}

data "aws_iam_policy_document" "task_execution_assume" {
  count = local.scheduled ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pulls the image and writes logs. Deliberately separate from
# the task role, which is what the collector actually runs as — conflating them
# is how a container ends up able to do more than the process inside it needs.
resource "aws_iam_role" "task_execution" {
  count              = local.scheduled ? 1 : 0
  name               = "${var.name_prefix}-collector-execution"
  assume_role_policy = data.aws_iam_policy_document.task_execution_assume[0].json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  count      = local.scheduled ? 1 : 0
  role       = aws_iam_role.task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Reading the token is granted to the EXECUTION role, not the task role. The
# task role is the discovery role, which carries an explicit deny on
# secretsmanager:GetSecretValue — the collector process must not be able to read
# secrets, only to be handed the one it was given.
resource "aws_iam_role_policy" "task_execution_secret" {
  count = local.scheduled && var.custos_token_secret_arn != "" ? 1 : 0
  name  = "${var.name_prefix}-collector-token"
  role  = aws_iam_role.task_execution[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.custos_token_secret_arn]
    }]
  })
}

resource "aws_ecs_task_definition" "collector" {
  count                    = local.scheduled ? 1 : 0
  family                   = "${var.name_prefix}-collector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024" # the batch cap is sized against this
  execution_role_arn       = aws_iam_role.task_execution[0].arn
  task_role_arn            = aws_iam_role.custos.arn

  container_definitions = jsonencode([{
    name      = "collector"
    image     = var.collector_image
    essential = true

    # Read-only root filesystem. The collector writes one cursor file, and that
    # goes to a mounted volume rather than to the image.
    readonlyRootFilesystem = true

    environment = [
      { name = "CUSTOS_ACCOUNT_ID", value = data.aws_caller_identity.current.account_id },
      { name = "CUSTOS_FLOW_LOGS", value = var.flow_log_group },
      { name = "AWS_REGION", value = data.aws_region.current.name },
      { name = "CUSTOS_ENDPOINT", value = var.custos_endpoint },
      { name = "CUSTOS_STATE_PATH", value = "/state/cursor.json" },
      { name = "CUSTOS_DAEMON", value = "0" },
    ]

    # By reference, never by value. A token in a task definition is a token in
    # every describe-task-definition call, in CloudTrail, and in the Terraform
    # state file.
    secrets = var.custos_token_secret_arn == "" ? [] : [{
      name      = "CUSTOS_TOKEN"
      valueFrom = var.custos_token_secret_arn
    }]

    mountPoints = [{
      sourceVolume  = "state"
      containerPath = "/state"
      readOnly      = false
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.collector[0].name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "collector"
      }
    }
  }])

  volume {
    name = "state"
  }

  tags = {
    ManagedBy = "custos"
  }
}

data "aws_iam_policy_document" "events_assume" {
  count = local.scheduled ? 1 : 0
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  count              = local.scheduled ? 1 : 0
  name               = "${var.name_prefix}-collector-scheduler"
  assume_role_policy = data.aws_iam_policy_document.events_assume[0].json
}

resource "aws_iam_role_policy" "scheduler" {
  count = local.scheduled ? 1 : 0
  name  = "${var.name_prefix}-collector-scheduler"
  role  = aws_iam_role.scheduler[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.collector[0].arn]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.custos.arn, aws_iam_role.task_execution[0].arn]
      },
    ]
  })
}

resource "aws_cloudwatch_event_rule" "collector" {
  count               = local.scheduled ? 1 : 0
  name                = "${var.name_prefix}-collector"
  description         = "Custos agent discovery collection"
  schedule_expression = var.collection_interval
}

resource "aws_cloudwatch_event_target" "collector" {
  count     = local.scheduled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.collector[0].name
  arn       = aws_ecs_cluster.collector[0].arn
  role_arn  = aws_iam_role.scheduler[0].arn
  target_id = "collector"

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.collector[0].arn
    launch_type         = "FARGATE"

    network_configuration {
      subnets          = var.collector_subnet_ids
      security_groups  = var.collector_security_group_ids
      assign_public_ip = false
    }
  }
}
