import tempfile
import time
import unittest
from pathlib import Path

from lambda_function.local_store import LocalStore, StoreConflict


class LocalStoreTests(unittest.TestCase):
    def test_confirmation_and_claim_survive_restart_and_double_click(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.db'
            store = LocalStore(path)
            token = store.create_confirmation_once('42', {'action': 'cancel', 'booking_id': '7'}, int(time.time()) + 300, '1')
            job = store.consume_confirmation(token, '42', '2')
            self.assertTrue(store.should_dispatch(job))
            store.claim_job(token)
            store.close()
            store = LocalStore(path)
            self.assertEqual(store.consume_confirmation(token, '42', '3')['status'], 'running')
            with self.assertRaises(StoreConflict):
                store.claim_job(token)
            store.close()

class SchedulerTests(unittest.TestCase):
    def test_schedule_only_runs_after_start_in_matching_minute_and_once(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from lambda_function.local_bot import LocalScheduler
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / 'bot.db')
            scheduler = LocalScheduler(store)
            snapshot = scheduler.get_schedule()
            scheduler.set_weekly_time(snapshot, ['MON'], 8, 30)
            scheduler.enable(scheduler.get_schedule())
            rome = ZoneInfo('Europe/Rome')
            start = datetime(2026, 11, 9, 8, 29, 59, tzinfo=rome)
            due = datetime(2026, 11, 9, 8, 30, 1, tzinfo=rome)
            self.assertFalse(scheduler.reserve_due(due, due))
            self.assertTrue(scheduler.reserve_due(due, start))
            self.assertFalse(scheduler.reserve_due(due, start))
            store.close()

    def test_disabling_schedule_prevents_execution(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from lambda_function.local_bot import LocalScheduler
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / 'bot.db')
            scheduler = LocalScheduler(store)
            scheduler.set_weekly_time(scheduler.get_schedule(), ['MON'], 8, 30)
            scheduler.enable(scheduler.get_schedule())
            scheduler.disable(scheduler.get_schedule(), confirmed=True)
            now = datetime(2026, 11, 9, 8, 30, tzinfo=ZoneInfo('Europe/Rome'))
            self.assertFalse(scheduler.reserve_due(now, now.replace(hour=7)))
            store.close()

class LocalBotTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from lambda_function import telegram_bot, telegram_worker
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalStore(Path(self.tmp.name) / 'bot.db')
        self.addCleanup(self.store.close)
        for module, names in ((telegram_bot, ('_store', '_scheduler', '_dispatch', '_cancel_confirmation', '_async_reads')),
                              (telegram_worker, ('get_secret',))):
            for name in names:
                patcher = patch.object(module, name, getattr(module, name))
                patcher.start()
                self.addCleanup(patcher.stop)
        from lambda_function.local_bot import LocalBot
        self.bot = LocalBot({'telegram_token': 'token', 'allowed_user_id': '42', 'allowed_chat_id': '42',
                             'sportrick_username': 'user', 'sportrick_password': 'password'}, self.store)

    def test_unauthorized_update_is_ignored_and_offset_persists(self):
        from unittest.mock import patch
        update = {'update_id': 10, 'message': {'text': '/bookings', 'from': {'id': 99}, 'chat': {'id': 42, 'type': 'private'}}}
        with patch.object(self.bot, 'api', return_value=[update]), patch('lambda_function.telegram_bot._run_ui') as read:
            self.bot.poll_once()
            read.assert_not_called()
        self.assertEqual(self.store.get('offset'), 11)

    def test_webhook_prevents_startup_without_removing_it(self):
        from unittest.mock import patch
        with patch.object(self.bot, 'api', return_value={'url': 'https://example.test/hook'}) as api:
            with self.assertRaisesRegex(RuntimeError, 'Webhook attivo'):
                self.bot.check_webhook()
            self.assertEqual([call.args[0] for call in api.call_args_list], ['getWebhookInfo'])

    def test_network_failure_does_not_advance_offset(self):
        from unittest.mock import patch
        with patch.object(self.bot, 'api', side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):
                self.bot.poll_once()
        self.assertIsNone(self.store.get('offset'))

    def test_scheduled_timeout_and_delivery_failure_do_not_repeat(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from unittest.mock import patch
        from lambda_function import telegram_bot
        scheduler = self.bot.scheduler
        scheduler.set_weekly_time(scheduler.get_schedule(), ['MON'], 8, 30)
        scheduler.enable(scheduler.get_schedule())
        rome = ZoneInfo('Europe/Rome')
        self.bot.started = datetime(2026, 11, 9, 8, 0, tzinfo=rome)
        now = datetime(2026, 11, 9, 8, 30, tzinfo=rome)
        with patch('lambda_function.app.book', side_effect=TimeoutError) as book, \
             patch('lambda_function.telegram_bot._send', side_effect=telegram_bot.TelegramError):
            self.bot.run_scheduled(now)
            self.bot.run_scheduled(now)
            self.assertEqual(book.call_count, 1)
        self.assertEqual(self.store.get('scheduled#2026-11-09')['status'], 'uncertain')

    def test_restart_warns_about_unfinished_scheduled_run(self):
        from unittest.mock import patch
        with self.store.db:
            self.store.put('scheduled#2026-11-09', {'status': 'running'})
        with patch('lambda_function.telegram_bot._send') as send:
            self.bot.warn_unfinished()
        self.assertIn('verifica', send.call_args.args[2].lower())

    def test_database_is_locked_against_second_instance(self):
        with self.assertRaises(RuntimeError):
            LocalStore(Path(self.tmp.name) / 'bot.db')

    def test_confirmation_double_click_executes_portal_only_once(self):
        from unittest.mock import patch
        from lambda_function import telegram_bot
        message = {'update_id': 20, 'message': {'text': '/cancel 7', 'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}}}
        with patch('lambda_function.telegram_bot._send'), patch('lambda_function.telegram_bot._telegram'), \
             patch('lambda_function.telegram_worker.SportrickClient') as client:
            client.return_value.cancel.return_value = {'status': 'confirmed'}
            self.bot.handle(message)
            token = self.store.get_update('20')['token']
            callback = {'id': 'cb', 'data': token, 'from': {'id': 42}, 'message': {'chat': {'id': 42, 'type': 'private'}}}
            self.bot.handle({'update_id': 21, 'callback_query': callback})
            self.bot.handle({'update_id': 22, 'callback_query': callback})
            self.assertEqual(client.return_value.cancel.call_count, 1)
            self.assertEqual(self.store.get_job(token)['status'], 'completed')

    def test_time_change_and_missed_execution(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        scheduler = self.bot.scheduler
        scheduler.set_weekly_time(scheduler.get_schedule(), ['MON'], 9, 0)
        scheduler.enable(scheduler.get_schedule())
        rome = ZoneInfo('Europe/Rome')
        started = datetime(2026, 11, 9, 9, 1, tzinfo=rome)
        self.assertFalse(scheduler.reserve_due(started, started))
        scheduler.disable(scheduler.get_schedule(), confirmed=True)
        scheduler.set_weekly_time(scheduler.get_schedule(), ['MON'], 10, 0)
        scheduler.enable(scheduler.get_schedule())
        self.assertTrue(scheduler.reserve_due(datetime(2026, 11, 9, 10, 0, tzinfo=rome), started))

    def test_expired_or_other_owner_cannot_confirm(self):
        now = [1000]
        self.store.clock = lambda: now[0]
        token = self.store.create_confirmation_once('42', {'action': 'cancel', 'booking_id': '7'}, 1001, '3')
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(token, '99', '4')
        now[0] = 1001
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(token, '42', '5')
        self.assertIsNone(self.store.get_job(token))
