# Blocchi import per la migrazione da SAM/CLI. Da rimuovere dopo il primo apply riuscito.
# Gli ID fisici arrivano da var.existing (vedi infra/scripts/inventory.sh).

import {
  to = aws_dynamodb_table.bot
  id = var.existing.table_name
}

import {
  to = aws_iam_role.webhook
  id = var.existing.webhook_role_name
}
import {
  to = aws_iam_role.worker
  id = var.existing.worker_role_name
}
import {
  to = aws_iam_role.guard
  id = var.existing.guard_role_name
}
import {
  to = aws_iam_role.legacy
  id = var.existing.legacy_role_name
}
import {
  to = aws_iam_role.scheduler
  id = var.scheduler_role_name
}

import {
  to = aws_iam_role_policy.webhook
  id = "${var.existing.webhook_role_name}:${var.existing.webhook_policy_name}"
}
import {
  to = aws_iam_role_policy.worker
  id = "${var.existing.worker_role_name}:${var.existing.worker_policy_name}"
}
import {
  to = aws_iam_role_policy.guard
  id = "${var.existing.guard_role_name}:${var.existing.guard_policy_name}"
}
import {
  to = aws_iam_role_policy.legacy
  id = "${var.existing.legacy_role_name}:${var.existing.legacy_policy_name}"
}
import {
  to = aws_iam_role_policy.scheduler
  id = "${var.scheduler_role_name}:${var.existing.scheduler_policy_name}"
}

import {
  to = aws_iam_role_policy_attachment.webhook_logs
  id = "${var.existing.webhook_role_name}/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
import {
  to = aws_iam_role_policy_attachment.worker_logs
  id = "${var.existing.worker_role_name}/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
import {
  to = aws_iam_role_policy_attachment.guard_logs
  id = "${var.existing.guard_role_name}/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
import {
  to = aws_iam_role_policy_attachment.legacy_logs
  id = "${var.existing.legacy_role_name}/arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

import {
  to = aws_lambda_function.webhook
  id = var.existing.webhook_function_name
}
import {
  to = aws_lambda_function.worker
  id = var.existing.worker_function_name
}
import {
  to = aws_lambda_function.guard
  id = var.existing.guard_function_name
}
import {
  to = aws_lambda_function.legacy
  id = var.legacy_function_name
}

import {
  to = aws_lambda_function_url.webhook
  id = var.existing.webhook_function_name
}
import {
  to = aws_lambda_permission.webhook_url_public
  id = "${var.existing.webhook_function_name}/${var.existing.webhook_url_permission_id}"
}

import {
  to = aws_lambda_permission.webhook_invoke_public
  id = "${var.existing.webhook_function_name}/${var.existing.webhook_invoke_permission_id}"
}

import {
  to = aws_lambda_function_event_invoke_config.worker
  id = "${var.existing.worker_function_name}:$LATEST"
}
import {
  to = aws_lambda_function_event_invoke_config.guard_latest
  id = "${var.existing.guard_function_name}:$LATEST"
}

import {
  to = aws_cloudwatch_log_group.webhook
  id = "/aws/lambda/${var.existing.webhook_function_name}"
}
import {
  to = aws_cloudwatch_log_group.worker
  id = "/aws/lambda/${var.existing.worker_function_name}"
}
import {
  to = aws_cloudwatch_log_group.guard
  id = "/aws/lambda/${var.existing.guard_function_name}"
}

import {
  to = aws_sns_topic.notifications
  id = "arn:aws:sns:${var.region}:${data.aws_caller_identity.current.account_id}:${var.sns_topic_name}"
}
import {
  to = aws_sns_topic_subscription.email
  id = var.existing.subscription_arn
}

import {
  to = aws_scheduler_schedule.booking
  id = "${var.schedule_group}/${var.schedule_name}"
}
