"""Offline structural checks without requiring a YAML parser."""

import re
import unittest
from pathlib import Path


TEMPLATE = (Path(__file__).resolve().parents[1] / 'bot-template.yaml').read_text()


SSM_PARAMETERS = {
    'TelegramBotTokenParameterName': '/gymbot/telegram-bot-token',
    'TelegramWebhookSecretParameterName': '/gymbot/telegram-webhook-secret',
    'SportrickUsernameParameterName': '/gymbot/sportrick-username',
    'SportrickPasswordParameterName': '/gymbot/sportrick-password',
}


def section(start, end):
    return TEMPLATE.split(start, 1)[1].split(end, 1)[0]


class BotTemplateTests(unittest.TestCase):
    def test_no_plaintext_secret_parameters(self):
        for parameter in ('TelegramBotToken', 'TelegramWebhookSecret', 'SportrickUsername', 'SportrickPassword'):
            self.assertNotRegex(TEMPLATE, rf'(?m)^  {parameter}:')
            self.assertNotIn(f'!Ref {parameter}\n', TEMPLATE)
        for name in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET', 'SPORTRICK_USERNAME', 'SPORTRICK_PASSWORD'):
            self.assertNotRegex(TEMPLATE, rf'(?m)^          {name}:')
        for parameter, default in SSM_PARAMETERS.items():
            self.assertRegex(TEMPLATE, rf'(?m)^  {parameter}:\n    Type: String\n    Default: {default}\n')
        self.assertNotIn('NoEcho', TEMPLATE)

    def test_ssm_env_and_exact_iam(self):
        webhook = section('  WebhookFunction:', '  WorkerFunction:')
        worker = section('  WorkerFunction:', '  ScheduledGuardFunction:')
        guard = section('  ScheduledGuardFunction:', '  WebhookLogGroup:')
        expected = {
            webhook: ('TelegramBotTokenParameterName', 'TelegramWebhookSecretParameterName',
                      'SportrickUsernameParameterName', 'SportrickPasswordParameterName'),
            worker: ('TelegramBotTokenParameterName', 'SportrickUsernameParameterName',
                     'SportrickPasswordParameterName'),
            guard: ('TelegramBotTokenParameterName', 'SportrickUsernameParameterName',
                    'SportrickPasswordParameterName'),
        }
        env_names = {
            'TelegramBotTokenParameterName': 'SSM_TELEGRAM_BOT_TOKEN',
            'TelegramWebhookSecretParameterName': 'SSM_TELEGRAM_WEBHOOK_SECRET',
            'SportrickUsernameParameterName': 'SSM_SPORTRICK_USERNAME',
            'SportrickPasswordParameterName': 'SSM_SPORTRICK_PASSWORD',
        }
        arn = 'arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter'
        for body, params in expected.items():
            self.assertIn('Action: ssm:GetParameter', body)
            for parameter, env in env_names.items():
                env_line = f'{env}: !Ref {parameter}'
                arn_line = f"!Sub '{arn}${{{parameter}}}'"
                self.assertEqual(env_line in body, parameter in params, (env, parameter))
                self.assertEqual(arn_line in body, parameter in params, (arn, parameter))
        for body in (worker, guard):
            self.assertNotIn('TELEGRAM_WEBHOOK_SECRET', body)
            self.assertNotIn('TelegramWebhookSecretParameterName', body)
        self.assertNotIn('ssm:*', TEMPLATE)
        self.assertNotIn('kms:', TEMPLATE)
        self.assertNotIn("Resource: '*'", TEMPLATE)

    def test_shared_runtime(self):
        self.assertRegex(TEMPLATE, r'(?m)^  TelegramAllowedUserId:\n    Type: String\n')
        for value in ('Runtime: python3.12', 'CodeUri: build/bot-package/', 'MemorySize: 128'):
            self.assertIn(value, section('Globals:', 'Resources:'))

    def test_table_and_functions(self):
        table = section('  BotTable:', '  WebhookFunction:')
        for value in ('BillingMode: PROVISIONED', 'TableClass: STANDARD', 'AttributeName: pk',
                      'ReadCapacityUnits: 1', 'WriteCapacityUnits: 1', 'AttributeName: purge_at', 'Enabled: true'):
            self.assertIn(value, table)
        webhook = section('  WebhookFunction:', '  WorkerFunction:')
        worker = section('  WorkerFunction:', '  ScheduledGuardFunction:')
        self.assertIn('Handler: telegram_bot.lambda_handler', webhook)
        self.assertIn('AuthType: NONE', webhook)
        self.assertIn('Handler: telegram_worker.lambda_handler', worker)
        self.assertIn('Timeout: 180', worker)
        self.assertNotIn('FunctionUrlConfig', worker)
        self.assertIn('MaximumRetryAttempts: 0', worker)
        self.assertIn('MaximumEventAgeInSeconds: 60', worker)
        for name in ('SSM_TELEGRAM_BOT_TOKEN', 'SSM_SPORTRICK_USERNAME', 'SSM_SPORTRICK_PASSWORD', 'BOT_TABLE_NAME'):
            self.assertRegex(webhook, rf'(?m)^          {name}:')
            self.assertRegex(worker, rf'(?m)^          {name}:')
        for name in ('TELEGRAM_ALLOWED_USER_ID', 'SSM_TELEGRAM_WEBHOOK_SECRET', 'BOT_SCHEDULE_NAME',
                     'BOT_SCHEDULE_GROUP', 'BOT_WORKER_FUNCTION'):
            self.assertRegex(webhook, rf'(?m)^          {name}:')
        self.assertIn('TELEGRAM_CHAT_ID: !Ref TelegramAllowedUserId', worker)
        self.assertNotIn('ReservedConcurrentExecutions', webhook)  # account limit too low for reserved concurrency

    def test_scoped_permissions_and_logs(self):
        webhook = section('  WebhookFunction:', '  WorkerFunction:')
        worker = section('  WorkerFunction:', '  ScheduledGuardFunction:')
        for action in ('dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem',
                       'lambda:InvokeFunction', 'scheduler:GetSchedule', 'scheduler:UpdateSchedule', 'iam:PassRole'):
            self.assertIn(action, webhook)
        self.assertIn('Resource: !GetAtt BotTable.Arn', webhook)
        self.assertIn('Resource: !GetAtt WorkerFunction.Arn', webhook)
        self.assertIn('schedule/${ScheduleGroup}/${ScheduleName}', webhook)
        self.assertIn('Resource: !Ref ScheduleRoleArn', webhook)
        self.assertIn('iam:PassedToService: scheduler.amazonaws.com', webhook)
        self.assertIn('dynamodb:UpdateItem', worker)
        self.assertIn('Resource: !GetAtt BotTable.Arn', worker)
        self.assertNotIn('Resource: "*"', TEMPLATE)
        self.assertNotIn('AWS::Scheduler::Schedule', TEMPLATE)
        self.assertEqual(len(re.findall(r'(?m)^      RetentionInDays: 7$', TEMPLATE)), 3)

    def test_scheduled_guard(self):
        guard = section('  ScheduledGuardFunction:', '  WebhookLogGroup:')
        self.assertIn("Default: '07:00'", section('  BookingTime:', '  SnsTopicArn:'))
        self.assertIn("Default: ''", section('  SnsTopicArn:', 'Conditions:'))
        self.assertIn("HasSnsTopic: !Not [!Equals [!Ref SnsTopicArn, '']]", TEMPLATE)
        for value in (
            'Type: AWS::Serverless::Function', 'Handler: scheduled_guard.lambda_handler',
            'Timeout: 180', 'MaximumRetryAttempts: 0', 'MaximumEventAgeInSeconds: 60',
            'BOT_SCHEDULE_ARN: !Sub', 'schedule/${ScheduleGroup}/${ScheduleName}',
            'BOT_TABLE_NAME: !Ref BotTable', 'BOT_BOOKING_TIME: !Ref BookingTime',
            'SSM_SPORTRICK_USERNAME: !Ref SportrickUsernameParameterName',
            'SSM_SPORTRICK_PASSWORD: !Ref SportrickPasswordParameterName',
            'SSM_TELEGRAM_BOT_TOKEN: !Ref TelegramBotTokenParameterName', 'TELEGRAM_CHAT_ID: !Ref TelegramAllowedUserId',
            'SNS_TOPIC_ARN: !Ref SnsTopicArn',
            'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem',
            'Resource: !GetAtt BotTable.Arn',
            '- !If\n              - HasSnsTopic', 'Action: sns:Publish',
            'Resource: !Ref SnsTopicArn', '- !Ref AWS::NoValue',
        ):
            self.assertIn(value, guard)
        self.assertNotIn('FunctionUrlConfig', guard)
        self.assertNotIn('iam:PassRole', guard)
        log_group = section('  ScheduledGuardLogGroup:', 'Outputs:')
        self.assertIn("LogGroupName: !Sub '/aws/lambda/${ScheduledGuardFunction}'", log_group)
        self.assertIn('RetentionInDays: 7', log_group)
        self.assertIn('Value: !GetAtt ScheduledGuardFunction.Arn', section('  ScheduledGuardArn:', '\n\n'))


if __name__ == '__main__':
    unittest.main()
