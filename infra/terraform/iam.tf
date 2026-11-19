locals {
  ssm_arn_prefix = "arn:${data.aws_partition.current.partition}:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter"
  schedule_arn   = "arn:${data.aws_partition.current.partition}:scheduler:${var.region}:${data.aws_caller_identity.current.account_id}:schedule/${var.schedule_group}/${var.schedule_name}"

  ssm_all = [
    "${local.ssm_arn_prefix}${var.ssm_telegram_bot_token}",
    "${local.ssm_arn_prefix}${var.ssm_sportrick_username}",
    "${local.ssm_arn_prefix}${var.ssm_sportrick_password}",
    "${local.ssm_arn_prefix}${var.ssm_google_calendar}",
  ]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

# ---------- Webhook ----------

resource "aws_iam_role" "webhook" {
  name               = var.existing.webhook_role_name
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "webhook_logs" {
  role       = aws_iam_role.webhook.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "webhook" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
    resources = [aws_dynamodb_table.bot.arn]
  }
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.worker.arn]
  }
  statement {
    actions   = ["scheduler:GetSchedule", "scheduler:UpdateSchedule"]
    resources = [local.schedule_arn]
  }
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.scheduler.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["scheduler.amazonaws.com"]
    }
  }
  statement {
    actions = ["ssm:GetParameter"]
    resources = [
      "${local.ssm_arn_prefix}${var.ssm_telegram_bot_token}",
      "${local.ssm_arn_prefix}${var.ssm_telegram_webhook_secret}",
      "${local.ssm_arn_prefix}${var.ssm_sportrick_username}",
      "${local.ssm_arn_prefix}${var.ssm_sportrick_password}",
    ]
  }
}

resource "aws_iam_role_policy" "webhook" {
  name   = var.existing.webhook_policy_name
  role   = aws_iam_role.webhook.id
  policy = data.aws_iam_policy_document.webhook.json
}

# ---------- Worker ----------

resource "aws_iam_role" "worker" {
  name               = var.existing.worker_role_name
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "worker_logs" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "worker" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.bot.arn]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = local.ssm_all
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = var.existing.worker_policy_name
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# ---------- Guard ----------

resource "aws_iam_role" "guard" {
  name               = var.existing.guard_role_name
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "guard_logs" {
  role       = aws_iam_role.guard.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "guard" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.bot.arn]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = local.ssm_all
  }
  dynamic "statement" {
    for_each = var.guard_sns_enabled ? [1] : []
    content {
      actions   = ["sns:Publish"]
      resources = [aws_sns_topic.notifications.arn]
    }
  }
}

resource "aws_iam_role_policy" "guard" {
  name   = var.existing.guard_policy_name
  role   = aws_iam_role.guard.id
  policy = data.aws_iam_policy_document.guard.json
}

# ---------- Legacy GymBooking-personale ----------

resource "aws_iam_role" "legacy" {
  name               = var.existing.legacy_role_name
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "legacy_logs" {
  role       = aws_iam_role.legacy.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "legacy" {
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }
}

resource "aws_iam_role_policy" "legacy" {
  name   = var.existing.legacy_policy_name
  role   = aws_iam_role.legacy.id
  policy = data.aws_iam_policy_document.legacy.json
}

# ---------- Scheduler ----------

resource "aws_iam_role" "scheduler" {
  name               = var.scheduler_role_name
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

# Invoca solo la versione fissata del guard (non $LATEST).
data "aws_iam_policy_document" "scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.guard.qualified_arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = var.existing.scheduler_policy_name
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}
