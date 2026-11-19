# Il bot puo' disattivare la schedule e cambiare giorni/ora (ADR 0002) e l'accensione e' una
# decisione amministrativa: Terraform non gestisce state ed espressione.

resource "aws_scheduler_schedule" "booking" {
  name                         = var.schedule_name
  group_name                   = var.schedule_group
  schedule_expression_timezone = "Europe/Rome"
  schedule_expression          = "cron(0 0 ? * MON *)"
  state                        = "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.guard.qualified_arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      schedule_arn   = local.schedule_arn
      scheduled_time = "<aws.scheduler.scheduled-time>"
    })

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 60
    }
  }

  lifecycle {
    ignore_changes = [state, schedule_expression, target[0].input]
  }
}
