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
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeInstances",
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
      "ecs:DescribeTasks",
      "ecs:DescribeTaskDefinition",
      "lambda:GetFunctionConfiguration",
    ]
    resources = ["*"]
  }

  # Log objects in S3. Two destinations exist for flow logs and the cheaper one
  # is S3, so a cost-conscious platform team — which is the target profile — is
  # more likely to have this than CloudWatch. Load balancer access logs are
  # always in S3.
  #
  # Scoped to named buckets. Granting s3:GetObject on "*" to a role a customer
  # created on our word would be the single worst line in this file, and the
  # extra variable is a small price for not writing it.
  dynamic "statement" {
    for_each = length(var.log_buckets) > 0 ? [1] : []
    content {
      sid    = "ReadLogObjects"
      effect = "Allow"
      actions = [
        "s3:ListBucket",
        "s3:GetObject",
      ]
      resources = concat(
        [for b in var.log_buckets : "arn:aws:s3:::${b}"],
        [for b in var.log_buckets : "arn:aws:s3:::${b}/*"],
      )
    }
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

# Nothing above is granted because it might be useful later. Thirteen actions
# were removed the day a test started comparing this file against the operations
# the collector can actually perform — among them iam:ListRoles and
# ec2:DescribeTags on "*", which are exactly the lines a security reviewer
# circles and which nothing has ever called. A permission that has to be
# defended is a permission that should be earning its place.

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
