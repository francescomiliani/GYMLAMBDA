# Controllo della lista d'attesa: avvisa quando una voce diventa una prenotazione.
# Sola lettura sul portale. Risorse NUOVE (non importate): il primo apply le crea e carica il
# pacchetto bot corrente (costruirlo con --with-deps, vedi package.tf). Poi, come le altre
# funzioni, il codice non viene piu' ridistribuito da Terraform (ignore_changes).

resource "aws_cloudwatch_log_group" "waitlist_watch" {
  name              = "/aws/lambda/${var.waitlist_watch_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "waitlist_watch" {
  name               = "${var.waitlist_watch_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

resource "aws_iam_role_policy_attachment" "waitlist_watch_logs" {
  role       = aws_iam_role.waitlist_watch.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "waitlist_watch" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.bot.arn]
  }
  statement {
    actions   = ["ssm:GetParameter"]
    resources = local.ssm_all
  }
}

resource "aws_iam_role_policy" "waitlist_watch" {
  name   = "${var.waitlist_watch_name}-policy"
  role   = aws_iam_role.waitlist_watch.id
  policy = data.aws_iam_policy_document.waitlist_watch.json
}

resource "aws_lambda_function" "waitlist_watch" {
  function_name = var.waitlist_watch_name
  role          = aws_iam_role.waitlist_watch.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "waitlist_watch.lambda_handler"
  memory_size   = 128
  timeout       = 60

  filename         = data.archive_file.bot.output_path
  source_code_hash = data.archive_file.bot.output_base64sha256

  environment {
    variables = {
      SSM_TELEGRAM_BOT_TOKEN = var.ssm_telegram_bot_token
      TELEGRAM_CHAT_ID       = var.telegram_allowed_user_id
      SSM_SPORTRICK_USERNAME = var.ssm_sportrick_username
      SSM_SPORTRICK_PASSWORD = var.ssm_sportrick_password
      SSM_GOOGLE_CALENDAR    = var.ssm_google_calendar
      BOT_TABLE_NAME         = aws_dynamodb_table.bot.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.waitlist_watch]

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_lambda_function_event_invoke_config" "waitlist_watch" {
  function_name                = aws_lambda_function.waitlist_watch.function_name
  qualifier                    = "$LATEST"
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}

# Ruolo dello scheduler dedicato: puo' invocare solo questa funzione.
resource "aws_iam_role" "waitlist_scheduler" {
  name               = "${var.waitlist_watch_name}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "waitlist_scheduler" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.waitlist_watch.arn]
  }
}

resource "aws_iam_role_policy" "waitlist_scheduler" {
  name   = "${var.waitlist_watch_name}-scheduler-policy"
  role   = aws_iam_role.waitlist_scheduler.id
  policy = data.aws_iam_policy_document.waitlist_scheduler.json
}

resource "aws_scheduler_schedule" "waitlist_watch" {
  name                         = var.waitlist_watch_name
  group_name                   = var.schedule_group
  schedule_expression_timezone = "Europe/Rome"
  schedule_expression          = var.waitlist_watch_schedule
  state                        = var.waitlist_watch_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.waitlist_watch.arn
    role_arn = aws_iam_role.waitlist_scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts       = 0
      maximum_event_age_in_seconds = 60
    }
  }
}
