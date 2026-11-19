output "webhook_url" {
  value = aws_lambda_function_url.webhook.function_url
}

output "guard_qualified_arn" {
  value = aws_lambda_function.guard.qualified_arn
}

output "sns_topic_arn" {
  value = aws_sns_topic.notifications.arn
}
