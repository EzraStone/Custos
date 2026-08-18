# The role Custos assumes. Read-only, external-ID protected, and small enough
# that a security reviewer can read the whole policy in under a minute.
#
# Note what is absent: no s3:PutObject, no logs:StartQuery (which creates a
# billable resource), no iam:PassRole, no wildcard on any action. Every
# statement names its actions explicitly.

data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.custos_account_id}:root"]
    }

    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [var.external_id]
    }
  }
}

data "aws_iam_policy_document" "read_only" {
  # Network metadata: the flow logs themselves and the interfaces they describe.
  statement {
    sid    = "ReadNetworkMetadata"
    effect = "Allow"
    actions = [
      "ec2:DescribeFlowLogs",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeInstances",
      "ec2:DescribeSubnets",
      "ec2:DescribeVpcs",
      "ec2:DescribeTags",
    ]
    resources = ["*"] # these Describe calls do not support resource scoping
  }

  # The log data. GetLogEvents and FilterLogEvents only: StartQuery would
  # create a billable Logs Insights query in the customer's account, which is a
  # write however read-only it sounds.
  statement {
    sid    = "ReadLogData"
    effect = "Allow"
    actions = [
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
    ]
    resources = ["arn:aws:logs:*:${data.aws_caller_identity.current.account_id}:log-group:*"]
  }

  # Identity and attribution: which principal, owned by whom, permitted to do
  # what. The policy read is what lets a finding say "can write to billing"
  # rather than only "talked to billing".
  statement {
    sid    = "ReadIdentityMetadata"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:ListRoles",
      "iam:ListRoleTags",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:GetRolePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
    ]
    resources = ["*"]
  }

  # Compute attribution: which task or function is behind an interface.
  statement {
    sid    = "ReadComputeMetadata"
    effect = "Allow"
    actions = [
      "ecs:ListClusters",
      "ecs:ListServices",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "lambda:ListFunctions",
      "lambda:GetFunctionConfiguration",
      "eks:ListClusters",
      "eks:DescribeCluster",
    ]
    resources = ["*"]
  }

  # CloudTrail, for principal-to-interface correlation.
  statement {
    sid       = "ReadCloudTrail"
    effect    = "Allow"
    actions   = ["cloudtrail:LookupEvents"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "custos" {
  name                 = "${var.name_prefix}-discovery"
  description          = "Read-only access for Custos agent discovery. Grants no write permission."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600

  tags = {
    ManagedBy = "custos"
    Purpose   = "agent-discovery-read-only"
  }
}

resource "aws_iam_role_policy" "custos" {
  name   = "${var.name_prefix}-discovery-read-only"
  role   = aws_iam_role.custos.id
  policy = data.aws_iam_policy_document.read_only.json
}

# Explicitly deny every mutating action, belt and braces. The policy above
# grants none of these, so this changes nothing today — it exists so that a
# future widening of the grant, by us or by a well-meaning engineer here, still
# cannot write. SEC-16 in the account itself rather than only in our code.
data "aws_iam_policy_document" "deny_writes" {
  statement {
    sid    = "DenyAllMutation"
    effect = "Deny"
    actions = [
      "ec2:Create*", "ec2:Delete*", "ec2:Modify*", "ec2:Terminate*", "ec2:Run*",
      "iam:Create*", "iam:Delete*", "iam:Update*", "iam:Put*", "iam:Attach*", "iam:PassRole",
      "s3:Put*", "s3:Delete*",
      "logs:Create*", "logs:Delete*", "logs:Put*", "logs:StartQuery",
      "lambda:Create*", "lambda:Update*", "lambda:Delete*", "lambda:Invoke*",
      "ecs:Create*", "ecs:Update*", "ecs:Delete*", "ecs:Run*",
      "rds:Create*", "rds:Delete*", "rds:Modify*",
      "kms:Decrypt", "secretsmanager:GetSecretValue",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deny_writes" {
  name   = "${var.name_prefix}-discovery-deny-writes"
  role   = aws_iam_role.custos.id
  policy = data.aws_iam_policy_document.deny_writes.json
}
