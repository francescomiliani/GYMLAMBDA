"""Offline structural checks on infra/terraform (no terraform binary needed)."""

import re
import unittest
from pathlib import Path

TF_DIR = Path(__file__).resolve().parents[1] / 'infra' / 'terraform'


def read(name):
    return (TF_DIR / name).read_text()


ALL = '\n'.join(p.read_text() for p in sorted(TF_DIR.glob('*.tf')))


def block(text, header):
    """Return the body of the top-level block starting with header (brace matching)."""
    start = text.index(header)
    depth, i = 0, text.index('{', start)
    begin = i
    while True:
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[begin:i + 1]
        i += 1


class TerraformTests(unittest.TestCase):
    def test_no_plaintext_secrets(self):
        for name in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET', 'SPORTRICK_USERNAME', 'SPORTRICK_PASSWORD'):
            self.assertNotRegex(ALL, rf'(?m)^\s+{name}\s*=')
        self.assertNotIn('aws_ssm_parameter', ALL)  # ADR 0001: SecureString creati fuori da IaC
        self.assertNotRegex(ALL, r'(?i)sk-|[0-9]{8,}:[A-Za-z0-9_-]{30,}')  # token-like strings

    def test_ssm_env_and_exact_iam(self):
        self.assertNotIn('ssm:*', ALL)
        self.assertNotIn('kms:', ALL)
        self.assertNotRegex(ALL, r'resources\s*=\s*\[\s*"\*"')
        webhook = block(read('iam.tf'), 'data "aws_iam_policy_document" "webhook"')
        worker = block(read('iam.tf'), 'data "aws_iam_policy_document" "worker"')
        guard = block(read('iam.tf'), 'data "aws_iam_policy_document" "guard"')
        self.assertIn('ssm_telegram_webhook_secret', webhook)
        for body in (worker, guard):
            self.assertNotIn('webhook_secret', body)
            self.assertIn('local.ssm_all', body)
        self.assertNotIn('google_calendar', webhook)  # il webhook non scrive sul calendario

    def test_functions(self):
        lam = read('lambda_bot.tf')
        webhook = block(lam, 'resource "aws_lambda_function" "webhook"')
        worker = block(lam, 'resource "aws_lambda_function" "worker"')
        guard = block(lam, 'resource "aws_lambda_function" "guard"')
        for body in (webhook, worker, guard):
            self.assertIn('runtime       = "python3.12"', body)
            self.assertIn('memory_size   = 128', body)
        self.assertIn('telegram_bot.lambda_handler', webhook)
        self.assertIn('telegram_worker.lambda_handler', worker)
        self.assertIn('scheduled_guard.lambda_handler', guard)
        self.assertIn('timeout       = 180', worker)
        self.assertIn('timeout       = 180', guard)
        self.assertIn('publish       = false', guard)  # migrazione: niente nuova versione, vedi commento nel .tf
        self.assertIn('ignore_changes = [filename, source_code_hash, publish]', guard)
        self.assertIn('authorization_type = "NONE"', lam)
        self.assertNotIn('reserved_concurrent_executions', ALL)  # limite account

    def test_guard_sns_off_by_default(self):
        self.assertRegex(read('variables.tf'), r'variable "guard_sns_enabled" \{\s+type\s+= bool\s+default\s+= false')
        self.assertIn('var.guard_sns_enabled ? aws_sns_topic.notifications.arn : ""', read('lambda_bot.tf'))
        self.assertIn('for_each = var.guard_sns_enabled', read('iam.tf'))

    def test_public_invoke_permission_only_via_function_url(self):
        body = block(read('lambda_bot.tf'), 'resource "aws_lambda_permission" "webhook_invoke_public"')
        self.assertIn('invoked_via_function_url = true', body)

    def test_async_retries_are_zero(self):
        for name in ('worker', 'guard_latest', 'guard_version'):
            body = block(read('lambda_bot.tf'), f'resource "aws_lambda_function_event_invoke_config" "{name}"')
            self.assertIn('maximum_retry_attempts       = 0', body)
            self.assertIn('maximum_event_age_in_seconds = 60', body)

    def test_schedule_targets_pinned_version_with_no_retries(self):
        sched = read('scheduler.tf')
        self.assertIn('aws_lambda_function.guard.qualified_arn', sched)
        self.assertNotIn('$LATEST', sched)
        self.assertIn('maximum_retry_attempts       = 0', sched)
        self.assertIn('"Europe/Rome"', sched)
        self.assertIn('mode = "OFF"', sched)
        # il bot (ADR 0002) governa stato ed espressione: Terraform non li riscrive
        self.assertIn('ignore_changes = [state, schedule_expression, target[0].input]', sched)
        scheduler_policy = block(read('iam.tf'), 'data "aws_iam_policy_document" "scheduler"')
        self.assertIn('aws_lambda_function.guard.qualified_arn', scheduler_policy)

    def test_legacy_is_never_modified(self):
        legacy = read('lambda_legacy.tf')
        self.assertIn('ignore_changes = [filename, source_code_hash, environment]', legacy)
        self.assertNotRegex(legacy, r'(?m)^\s*environment\s*\{')

    def test_table_and_logs(self):
        table = read('dynamodb.tf')
        for value in ('billing_mode = "PROVISIONED"', 'table_class  = "STANDARD"', 'read_capacity  = 1',
                      'write_capacity = 1', 'hash_key = "pk"', 'attribute_name = "purge_at"', 'enabled        = true'):
            self.assertIn(value, table)
        self.assertEqual(len(re.findall(r'resource "aws_cloudwatch_log_group"', read('logs.tf'))), 3)
        self.assertRegex(read('variables.tf'), r'variable "log_retention_days" \{\s+type\s+= number\s+default\s+= 7')

    def test_waitlist_watch_is_read_only_and_scoped(self):
        text = read('waitlist_watch.tf')
        function = block(text, 'resource "aws_lambda_function" "waitlist_watch"')
        self.assertIn('waitlist_watch.lambda_handler', function)
        self.assertIn('runtime       = "python3.12"', function)
        self.assertIn('timeout       = 60', function)
        policy = block(text, 'data "aws_iam_policy_document" "waitlist_watch"')
        self.assertIn('local.ssm_all', policy)
        self.assertNotIn('UpdateItem', policy)  # sola lettura/scrittura dello stato, niente conferme
        self.assertNotIn('sns:', policy)
        self.assertNotIn('scheduler:', policy)  # non tocca la schedule di prenotazione
        scheduler_policy = block(text, 'data "aws_iam_policy_document" "waitlist_scheduler"')
        self.assertIn('aws_lambda_function.waitlist_watch.arn', scheduler_policy)
        self.assertNotIn('guard', scheduler_policy)
        invoke = block(text, 'resource "aws_lambda_function_event_invoke_config" "waitlist_watch"')
        self.assertIn('maximum_retry_attempts       = 0', invoke)
        schedule = block(text, 'resource "aws_scheduler_schedule" "waitlist_watch"')
        self.assertIn('"Europe/Rome"', schedule)
        self.assertIn('mode = "OFF"', schedule)
        self.assertIn('maximum_retry_attempts       = 0', schedule)
        self.assertRegex(read('variables.tf'), r'variable "waitlist_watch_schedule" \{\s+type\s+= string\s+default\s+= "cron\(0/30 6-22 \* \* \? \*\)"')
        self.assertNotIn('reserved_concurrent_executions', text)

    def test_backend_and_state_hygiene(self):
        backend = read('backend.tf')
        self.assertIn('use_lockfile = true', backend)
        self.assertIn('encrypt      = true', backend)
        gitignore = (TF_DIR.parents[1] / '.gitignore').read_text()
        for pattern in ('.terraform/', '*.tfstate', '*.tfvars', '!*.tfvars.example'):
            self.assertIn(pattern, gitignore)

    def test_every_import_has_a_resource(self):
        imports = read('imports.tf')
        resources = set(re.findall(r'(?m)^resource "(\w+)" "(\w+)"', ALL))
        targets = re.findall(r'to = (\w+)\.(\w+)', imports)
        self.assertGreaterEqual(len(targets), 30)
        for kind, name in targets:
            self.assertIn((kind, name), resources, f'{kind}.{name}')


if __name__ == '__main__':
    unittest.main()
