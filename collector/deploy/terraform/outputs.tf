output "role_arn" {
  description = "Send this to Custos. It is the only thing Custos needs from you."
  value       = aws_iam_role.custos.arn
}

output "flow_log_group" {
  description = "Log group Custos will read."
  value       = var.create_flow_logs ? aws_cloudwatch_log_group.flow_logs[0].name : var.flow_log_group
}

output "granted_actions_are_read_only" {
  description = "Verify with: aws iam get-role-policy --role-name <role> --policy-name <policy>"
  value       = true
}
