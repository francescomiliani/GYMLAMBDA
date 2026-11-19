# Fase di migrazione: filename e source_code_hash sono ignorati, cosi' il primo apply
# non distribuisce il codice locale (puo' differire da quello in esercizio).
# Per abilitare i deploy da Terraform: rimuovere ignore_changes e fare un plan mirato.

resource "aws_lambda_function" "webhook" {
  function_name = var.existing.webhook_function_name
  role          = aws_iam_role.webhook.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "telegram_bot.lambda_handler"
  memory_size   = 128
  timeout       = 60

  filename         = data.archive_file.bot.output_path
  source_code_hash = data.archive_file.bot.output_base64sha256

  environment {
    variables = {
      SSM_TELEGRAM_BOT_TOKEN      = var.ssm_telegram_bot_token
      TELEGRAM_ALLOWED_USER_ID    = var.telegram_allowed_user_id
      SSM_TELEGRAM_WEBHOOK_SECRET = var.ssm_telegram_webhook_secret
      SSM_SPORTRICK_USERNAME      = var.ssm_sportrick_username
      SSM_SPORTRICK_PASSWORD      = var.ssm_sportrick_password
      BOT_TABLE_NAME              = aws_dynamodb_table.bot.name
      BOT_SCHEDULE_NAME           = var.schedule_name
      BOT_SCHEDULE_GROUP          = var.schedule_group
      BOT_WORKER_FUNCTION         = aws_lambda_function.worker.function_name
      BOT_ASYNC_READS             = "1"
    }
  }

  # Niente reserved concurrency: il limite dell'account la rifiuta.
  depends_on = [aws_cloudwatch_log_group.webhook]

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_lambda_function_url" "webhook" {
  function_name      = aws_lambda_function.webhook.function_name
  authorization_type = "NONE"
}

# Permesso pubblico della Function URL: il webhook verifica da solo secret e utente.
resource "aws_lambda_permission" "webhook_url_public" {
  statement_id           = var.existing.webhook_url_permission_id
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.webhook.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# Secondo permesso creato da SAM: InvokeFunction pubblico.
resource "aws_lambda_permission" "webhook_invoke_public" {
  statement_id  = var.existing.webhook_invoke_permission_id
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.webhook.function_name
  principal     = "*"

  # Come l'AWS reale: vale solo per chiamate arrivate dalla Function URL.
  invoked_via_function_url = true
}

resource "aws_lambda_function" "worker" {
  function_name = var.existing.worker_function_name
  role          = aws_iam_role.worker.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "telegram_worker.lambda_handler"
  memory_size   = 128
  timeout       = 180

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

  depends_on = [aws_cloudwatch_log_group.worker]

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_lambda_function_event_invoke_config" "worker" {
  function_name                = aws_lambda_function.worker.function_name
  qualifier                    = "$LATEST"
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}

# La schedule punta a una versione immutabile (ADR 0002), mai a $LATEST.
# Migrazione: publish = false e ignore_changes su publish, cosi' l'apply non pubblica
# una nuova versione e non sposta il target della schedule attiva (oggi versione 2).
# Per distribuire nuovo codice del guard: rimuovere publish dall'ignore_changes e usare publish = true.
resource "aws_lambda_function" "guard" {
  function_name = var.existing.guard_function_name
  role          = aws_iam_role.guard.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "scheduled_guard.lambda_handler"
  memory_size   = 128
  timeout       = 180
  publish       = false

  filename         = data.archive_file.bot.output_path
  source_code_hash = data.archive_file.bot.output_base64sha256

  environment {
    variables = {
      BOT_SCHEDULE_ARN       = local.schedule_arn
      BOT_TABLE_NAME         = aws_dynamodb_table.bot.name
      BOT_BOOKING_TIME       = var.booking_time
      SSM_SPORTRICK_USERNAME = var.ssm_sportrick_username
      SSM_SPORTRICK_PASSWORD = var.ssm_sportrick_password
      SSM_TELEGRAM_BOT_TOKEN = var.ssm_telegram_bot_token
      TELEGRAM_CHAT_ID       = var.telegram_allowed_user_id
      SNS_TOPIC_ARN          = var.guard_sns_enabled ? aws_sns_topic.notifications.arn : ""
      SSM_GOOGLE_CALENDAR    = var.ssm_google_calendar
    }
  }

  depends_on = [aws_cloudwatch_log_group.guard]

  lifecycle {
    ignore_changes = [filename, source_code_hash, publish]
  }
}

resource "aws_lambda_function_event_invoke_config" "guard_latest" {
  function_name                = aws_lambda_function.guard.function_name
  qualifier                    = "$LATEST"
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}

# Retry 0 anche sulla versione invocata dalla schedule.
resource "aws_lambda_function_event_invoke_config" "guard_version" {
  function_name                = aws_lambda_function.guard.function_name
  qualifier                    = aws_lambda_function.guard.version
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 60
}
