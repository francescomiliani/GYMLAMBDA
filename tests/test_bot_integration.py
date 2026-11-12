import json
import time
import unittest
from unittest.mock import Mock, patch

from lambda_function import telegram_bot, telegram_worker
from lambda_function.bot_store import BotStore
from test_bot_store import FakeDynamo


class BotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.db = FakeDynamo()
        self.store = BotStore(self.db, 'table', lambda: self.now)
        self.queue = []
        self.messages = []
        self.fail_dispatch = False
        self.client = Mock()
        self.client.book.return_value = {'status': 'confirmed'}
        self.env = patch.dict('os.environ', {
            'TELEGRAM_WEBHOOK_SECRET': 'secret', 'TELEGRAM_ALLOWED_USER_ID': '42',
            'TELEGRAM_BOT_TOKEN': 'token', 'TELEGRAM_CHAT_ID': '42',
            'BOT_TABLE_NAME': 'table', 'BOT_WORKER_FUNCTION': 'worker',
            'BOT_ASYNC_READS': '1',
            'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'password',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        for target, replacement in [
            ('lambda_function.telegram_bot._store', lambda: self.store),
            ('lambda_function.telegram_bot.time.time', lambda: self.now),
            ('lambda_function.telegram_worker.SportrickClient', lambda session: self.client),
            ('requests.post', self.post),
            ('boto3.client', self.aws_client),
        ]:
            p = patch(target, replacement)
            p.start()
            self.addCleanup(p.stop)

    def post(self, url, json, timeout):
        self.messages.append((url.rsplit('/', 1)[-1], json))
        response = Mock()
        response.json.return_value = {'ok': True}
        return response

    def aws_client(self, service, **kwargs):
        if service == 'dynamodb':
            return self.db
        if service == 'lambda':
            return self
        raise AssertionError(service)

    def invoke(self, **kwargs):
        self.assertEqual(kwargs['InvocationType'], 'Event')
        if self.fail_dispatch:
            raise RuntimeError('dispatch failed')
        self.queue.append(json.loads(kwargs['Payload']))
        return {'StatusCode': 202}

    def webhook(self, update):
        return telegram_bot.lambda_handler({
            'headers': {'X-Telegram-Bot-Api-Secret-Token': 'secret'},
            'requestContext': {'http': {'method': 'POST'}},
            'body': json.dumps(update),
        }, None)

    def confirmation(self):
        response = self.webhook({'update_id': 1, 'message': {
            'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'},
            'text': '/book 10 2026-11-20 20',
        }})
        self.assertEqual(response['statusCode'], 200)
        return self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']

    def callback(self, token, update_id=2, user=42, message_id=10):
        return self.webhook({'update_id': update_id, 'callback_query': {
            'id': 'callback', 'from': {'id': user}, 'data': token,
            'message': {'message_id': message_id, 'chat': {'id': user, 'type': 'private'}},
        }})

    def test_async_guided_booking_flow(self):
        self.client.activities.return_value = [{'id': '10', 'name': 'Yoga'}]
        self.client.schedule.return_value = [{
            'AppointmentId': '20', 'Type': 'class', 'Status': 'Available',
            'Notes': 'Prenota', 'EventName': 'Yoga', 'TimeStart': '09:00',
        }]
        response = self.webhook({'update_id': 11, 'message': {
            'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}, 'text': '/activities',
        }})
        self.assertEqual(response['statusCode'], 200)
        self.assertEqual(self.queue[-1], {'read': {'action': 'activities'}, 'chat_id': '42',
                                          'ui': {'kind': 'activities'}})
        self.client.activities.assert_not_called()
        self.assertEqual(telegram_worker.lambda_handler(self.queue[-1], None), {'status': 'completed'})
        self.client.activities.assert_called_once_with()
        self.assertEqual(self.messages[-1][1]['chat_id'], '42')
        activity = self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(activity, 'fa:10')
        self.assertEqual(self.callback(activity, 12)['statusCode'], 200)
        day = self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertTrue(day.startswith('fd:10:'))
        date = telegram_bot._today().isoformat()
        self.assertEqual(self.callback(day, 13)['statusCode'], 200)
        day_event = self.queue[-1]
        self.assertEqual(day_event['read'], {'action': 'schedule', 'activity_id': '10', 'date': date})
        self.assertEqual(day_event['chat_id'], '42')
        self.assertEqual({key: day_event['ui'][key] for key in ('kind', 'activity_id', 'date')},
                         {'kind': 'day', 'activity_id': '10', 'date': date})
        self.assertTrue(day_event['ui']['sid'])
        self.assertEqual(telegram_worker.lambda_handler(day_event, None), {'status': 'completed'})
        self.client.schedule.assert_called_once_with('10', date)
        slot = self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(telegram_worker.lambda_handler(day_event, None), {'status': 'completed'})
        slot_copy = self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(slot_copy, slot)
        self.assertEqual(self.callback(slot, 14, message_id=123)['statusCode'], 200)
        event = self.queue[-1]
        self.assertEqual(event['read'], {'action': 'schedule', 'activity_id': '10', 'date': date})
        self.assertEqual(event['chat_id'], '42')
        self.assertEqual(event['ui']['kind'], 'slot')
        self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'completed'})
        confirmation = self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'completed'})
        self.assertEqual(self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data'], confirmation)
        self.assertEqual(self.callback(slot_copy, 15, message_id=456)['statusCode'], 200)
        self.assertEqual(telegram_worker.lambda_handler(self.queue[-1], None), {'status': 'completed'})
        self.assertEqual(self.messages[-1][1]['reply_markup']['inline_keyboard'][0][0]['callback_data'], confirmation)
        self.assertEqual(self.callback(confirmation, 16)['statusCode'], 200)
        mutation = self.queue[-1]
        self.assertEqual(mutation, {'job_id': confirmation})
        self.assertEqual(telegram_worker.lambda_handler(mutation, None), {'status': 'confirmed'})
        self.client.book.assert_called_once_with('10', date, '20')
        queued = len(self.queue)
        self.assertEqual(self.callback(confirmation, 17)['statusCode'], 200)
        self.assertEqual(len(self.queue), queued)
        self.assertEqual(telegram_worker.lambda_handler(mutation, None), {'status': 'already_claimed'})
        self.client.book.assert_called_once()

    def test_async_read_rejects_wrong_chat_without_read_or_send(self):
        event = {'read': {'action': 'activities'}, 'chat_id': '99', 'ui': {'kind': 'activities'}}
        self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'unauthorized'})
        self.client.activities.assert_not_called()
        self.assertEqual(self.messages, [])

    def test_confirmation_worker_notification_and_duplicates(self):
        token = self.confirmation()
        self.assertEqual(self.callback(token)['statusCode'], 200)
        self.assertEqual(self.queue, [{'job_id': token}])
        self.assertEqual(telegram_worker.lambda_handler(self.queue[0], None), {'status': 'confirmed'})
        self.client.book.assert_called_once_with('10', '2026-11-20', '20')
        self.assertTrue(any(payload.get('text') == 'Esito operazione: confermato'
                            for _, payload in self.messages))
        self.assertEqual(self.callback(token)['statusCode'], 200)
        self.assertEqual(self.callback(token, update_id=3)['statusCode'], 200)
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(telegram_worker.lambda_handler(self.queue[0], None), {'status': 'already_claimed'})
        self.client.book.assert_called_once()
        self.assertEqual(self.store.get_job(token)['status'], 'completed')

    def test_expired_confirmation_and_wrong_user_do_not_execute(self):
        token = self.confirmation()
        self.assertEqual(self.callback(token, user=99)['statusCode'], 403)
        self.now += 300
        self.assertEqual(self.callback(token)['statusCode'], 200)
        self.assertTrue(any('scaduta' in payload.get('text', '') for _, payload in self.messages))
        self.assertIsNone(self.store.get_job(token))
        self.assertEqual(self.queue, [])
        self.client.book.assert_not_called()

    def test_failed_dispatch_redelivery_only_while_pending(self):
        token = self.confirmation()
        self.fail_dispatch = True
        self.assertEqual(self.callback(token)['statusCode'], 502)
        self.assertEqual(self.store.get_job(token)['status'], 'pending')
        self.fail_dispatch = False
        self.assertEqual(self.callback(token)['statusCode'], 200)
        self.assertEqual(len(self.queue), 1)
        telegram_worker.lambda_handler(self.queue[0], None)
        self.callback(token)
        self.assertEqual(len(self.queue), 1)
        self.client.book.assert_called_once()

    def test_timeout_is_uncertain_and_never_retried(self):
        self.client.book.side_effect = TimeoutError('ambiguous')
        token = self.confirmation()
        self.callback(token)
        self.assertEqual(telegram_worker.lambda_handler(self.queue[0], None), {'status': 'uncertain'})
        job = self.store.get_job(token)
        self.assertEqual(job['status'], 'uncertain')
        self.assertEqual(job['result'], {'status': 'uncertain'})
        self.assertTrue(any(payload.get('text') == 'Esito operazione: incerto'
                            for _, payload in self.messages))
        self.callback(token)
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(telegram_worker.lambda_handler(self.queue[0], None), {'status': 'already_claimed'})
        self.client.book.assert_called_once()


if __name__ == '__main__':
    unittest.main()
