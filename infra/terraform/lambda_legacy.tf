# Lambda legacy non idempotente: ogni invocazione con payload di prenotazione prenota davvero.
# Terraform non deve mai toccarne codice o variabili (contengono segreti in chiaro, vedi
# docs/how-to/terraform.md): solo configurazione statica.

resource "aws_lambda_function" "legacy" {
  function_name = var.legacy_function_name
  role          = aws_iam_role.legacy.arn
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "app.lambda_handler"
  memory_size   = 128
  timeout       = 60

  filename         = data.archive_file.legacy.output_path
  source_code_hash = data.archive_file.legacy.output_base64sha256

  lifecycle {
    ignore_changes = [filename, source_code_hash, environment]
  }
}
