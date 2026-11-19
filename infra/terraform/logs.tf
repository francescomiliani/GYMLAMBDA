resource "aws_cloudwatch_log_group" "webhook" {
  name              = "/aws/lambda/${var.existing.webhook_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${var.existing.worker_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "guard" {
  name              = "/aws/lambda/${var.existing.guard_function_name}"
  retention_in_days = var.log_retention_days
}
