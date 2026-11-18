import datetime
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import boto3.dynamodb.types
import botocore.exceptions

from lambda_function import scheduled_guard as guard


def guard_store(client, table):
    from lambda_function.bot_store import BotStore
    return BotStore(client, table)


ARN = 'arn:aws:scheduler:eu-west-1:123456789012:schedule/default/booking'
NOW = datetime.datetime(2026, 3, 29, 0, 30, tzinfo=datetime.timezone.utc)


class ConditionalFailure(Exception):
    response = {'Error': {'Code': 'ConditionalCheckFailedException'}}


class FakeDynamo:
    def __init__(self):
        self.items = {}
        self.fail_update = False
        self.calls = []

    def put_item(self, *, TableName, Item, ConditionExpression=None):
        self.calls.append('put')
        key = (TableName, Item['pk']['S'])
        if ConditionExpression is None:  # stato semplice (prefs, skip, last_run)
            self.items[key] = dict(Item)
            return
        assert ConditionExpression == 'attribute_not_exists(pk)'
        if key in self.items:
            raise ConditionalFailure()
        self.items[key] = dict(Item)

    def get_item(self, *, TableName, Key, ConsistentRead):
        self.calls.append('get')
        assert ConsistentRead
        return {'Item': self.items.get((TableName, Key['pk']['S']), {})}

    def update_item(self, *, TableName, Key, UpdateExpression, ConditionExpression,
                    ExpressionAttributeNames, ExpressionAttributeValues):
        self.calls.append('update')
        if self.fail_update:
            raise RuntimeError('update failed')
        key = (TableName, Key['pk']['S'])
        assert ConditionExpression == 'attribute_exists(pk)' and key in self.items
        self.items[key]['status'] = ExpressionAttributeValues[':s']


class ScheduledGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeDynamo()
        self.client_factory = Mock(return_value=self.client)
        modules = {'boto3': types.SimpleNamespace(client=self.client_factory),
                   'boto3.dynamodb': types.ModuleType('boto3.dynamodb'),
                   'boto3.dynamodb.types': boto3.dynamodb.types,
                   'botocore': types.ModuleType('botocore'),
                   'botocore.exceptions': botocore.exceptions,
                   'botocore.config': types.SimpleNamespace(Config=Mock(side_effect=lambda **kw: kw))}
        self.enterContext(patch.dict(sys.modules, modules))
        self.enterContext(patch.dict(os.environ, {'BOT_SCHEDULE_ARN': ARN, 'BOT_TABLE_NAME': 'table',
                                                  'BOT_BOOKING_TIME': '18:15',
                                                  'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'pass'}))
        self.clock = self.enterContext(patch.object(guard.datetime, 'datetime', wraps=datetime.datetime))
        self.clock.now.return_value = NOW
        self.booking = self.enterContext(patch.object(guard.app, 'lambda_handler', return_value={'message': 'confirmed'}))
        self.event = {'schedule_arn': ARN, 'scheduled_time': '2026-03-29T00:30:00Z'}

    def call(self, **changes):
        return guard.lambda_handler({**self.event, **changes}, None)

    def test_persistent_claim_duplicate_and_equivalent_timestamp(self):
        self.assertEqual(self.call(), {'message': 'confirmed'})
        self.assertEqual(self.call(scheduled_time='2026-03-29T01:30:00+01:00'),
                         {'status': 'duplicate', 'outcome': 'completed'})
        self.booking.assert_called_once_with({'date': '2026-04-05', 'time': '18:15'}, None)
        claims = [item for (_, pk), item in self.client.items.items() if pk.startswith('schedule#')]
        self.assertEqual(len(claims), 1)
        item = claims[0]
        self.assertEqual(item['started'], {'N': str(int(NOW.timestamp()))})
        self.assertGreaterEqual(int(item['purge_at']['N']), int(NOW.timestamp()) + 7 * 86400)
        self.assertEqual(item['status'], {'S': 'completed'})
        config = self.client_factory.call_args.kwargs['config']
        self.assertEqual(config, {'connect_timeout': 2, 'read_timeout': 3,
                                  'retries': {'total_max_attempts': 1}})
        self.assertNotIn('delete', self.client.calls)

    def set_state(self, key, value, table='table'):
        state = guard_store(self.client, table)
        state.put_state(key, value)

    def test_preferences_give_ordered_fallback_for_the_class_weekday(self):
        # 2026-03-29 00:30 UTC = domenica 02:30 a Roma (ora legale): lezione domenica 2026-04-05
        self.set_state('prefs', {'SUN': [{'activity': 'gym', 'time': '07:00'}, {'activity': 'gym', 'time': '12:30'}]})
        self.call()
        self.booking.assert_called_once_with({'date': '2026-04-05', 'time': '07:00', 'fallback_times': ['12:30']}, None)
        state = guard_store(self.client, 'table')
        self.assertEqual(state.get_state('last_run')['status'], 'completed')

    def test_weekday_without_preference_is_skipped_before_claim(self):
        self.set_state('prefs', {'MON': [{'activity': 'gym', 'time': '07:00'}]})
        self.assertEqual(self.call(), {'status': 'skipped', 'reason': 'no preference for this weekday'})
        self.booking.assert_not_called()
        self.assertFalse([pk for (_, pk) in self.client.items if pk.startswith('schedule#')])

    def test_skipped_date_is_not_booked_and_can_be_resumed(self):
        self.set_state('skip#2026-04-05', {'skipped': True})
        self.assertEqual(self.call(), {'status': 'skipped', 'reason': 'date skipped'})
        self.booking.assert_not_called()
        self.set_state('skip#2026-04-05', {'skipped': False})
        self.assertEqual(self.call(), {'message': 'confirmed'})

    def test_last_run_write_failure_never_changes_the_outcome(self):
        original = self.client.put_item

        def failing(**kwargs):
            if kwargs.get('ConditionExpression') is None:
                raise RuntimeError('state write failed')
            return original(**kwargs)

        self.client.put_item = failing
        self.assertEqual(self.call(), {'message': 'confirmed'})

    def test_crash_and_failed_update_leave_claim(self):
        self.booking.side_effect = RuntimeError('crash')
        self.client.fail_update = True
        with self.assertRaisesRegex(RuntimeError, 'crash'):
            self.call()
        self.assertEqual(self.call(), {'status': 'duplicate', 'outcome': 'verify'})
        self.booking.assert_called_once()
        self.assertEqual(next(iter(self.client.items.values()))['status'], {'S': 'claimed'})
        self.assertNotIn('delete', self.client.calls)

    def test_booking_exception_records_uncertain(self):
        self.booking.side_effect = RuntimeError('crash')
        with self.assertRaises(RuntimeError):
            self.call()
        self.assertEqual(next(iter(self.client.items.values()))['status'], {'S': 'uncertain'})
        self.assertEqual(self.call(), {'status': 'duplicate', 'outcome': 'verify'})
        self.booking.assert_called_once()

    def test_completed_booking_with_failed_update_remains_claimed(self):
        self.client.fail_update = True
        with self.assertRaisesRegex(RuntimeError, 'update failed'):
            self.call()
        self.assertEqual(self.call(), {'status': 'duplicate', 'outcome': 'verify'})
        self.booking.assert_called_once()
        self.assertEqual(len(self.client.items), 1)

    def test_rome_date_and_dst(self):
        for stamp, expected in [('2026-03-28T23:30:00Z', '2026-04-05'),
                                ('2026-10-25T00:30:00Z', '2026-11-01'),
                                ('2026-10-25T01:30:00Z', '2026-11-01')]:
            self.clock.now.return_value = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            self.call(scheduled_time=stamp)
            self.booking.assert_called_with({'date': expected, 'time': '18:15'}, None)

    def test_invalid_events_and_config_fail_closed(self):
        invalid = [None, {}, {**self.event, 'time': '18:15'}, {**self.event, 'action': 'book'},
                   {**self.event, 'schedule_arn': ARN + 'x'},
                   {**self.event, 'scheduled_time': '2026-03-29T00:30:00'},
                   {**self.event, 'scheduled_time': '2026-03-29T00:24:59Z'},
                   {**self.event, 'scheduled_time': '2026-03-29T00:31:01Z'},
                   {**self.event, 'scheduled_time': '2026-02-30T00:30:00Z'}]
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(ValueError):
                guard.lambda_handler(event, None)
        with patch.dict(os.environ, {'BOT_BOOKING_TIME': '24:00'}), self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.client.calls)
        self.booking.assert_not_called()

    def test_unavailable_credentials_fail_before_claim(self):
        with patch.object(guard.app, 'get_secret', side_effect=RuntimeError('ssm down')):
            with self.assertRaises(RuntimeError):
                guard.lambda_handler(self.event, None)
        self.assertFalse(self.client.calls)
        self.booking.assert_not_called()

    def test_insufficient_remaining_time_fails_before_claim(self):
        context = types.SimpleNamespace(get_remaining_time_in_millis=lambda: 119999)
        with self.assertRaises(RuntimeError):
            guard.lambda_handler(self.event, context)
        self.assertFalse(self.client.calls)
        self.booking.assert_not_called()
        context.get_remaining_time_in_millis = lambda: 120000
        guard.lambda_handler(self.event, context)
        self.booking.assert_called_once()

    def test_state_read_failure_aborts_before_claim_and_booking(self):
        original = self.client.get_item

        def failing(**kwargs):
            raise RuntimeError('dynamodb unavailable')

        self.client.get_item = failing
        with self.assertRaisesRegex(RuntimeError, 'dynamodb unavailable'):
            self.call()
        self.client.get_item = original
        self.booking.assert_not_called()
        self.assertFalse([pk for (_, pk) in self.client.items if pk.startswith('schedule#')])

    def test_zip_root_handler_runs_end_to_end_with_state_lookup(self):
        """Percorso reale di Lambda: moduli alla radice, niente pacchetto, import di bot_store e timetable."""
        source = Path(__file__).resolve().parents[1] / 'lambda_function'
        script = (
            'import sys, datetime; sys.path.insert(0, sys.argv[1])\n'
            'import boto3\n'
            'class Fake:\n'
            '    def get_item(self, **kw):\n'
            '        key = kw["Key"]["pk"]["S"]\n'
            '        if key.startswith("state#skip#"):\n'
            '            return {"Item": {"pk": {"S": key}, "value": {"M": {"skipped": {"BOOL": True}}}}}\n'
            '        return {}\n'
            '    def put_item(self, **kw): raise SystemExit("claim must not happen")\n'
            'boto3.client = lambda *a, **k: Fake()\n'
            'import scheduled_guard\n'
            'now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")\n'
            'print(scheduled_guard.lambda_handler({"schedule_arn": sys.argv[2], "scheduled_time": now}, None))\n')
        env = {**os.environ, 'BOT_SCHEDULE_ARN': ARN, 'BOT_TABLE_NAME': 'table', 'BOT_BOOKING_TIME': '18:15',
               'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'pass'}
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'function.zip'
            with ZipFile(archive_path, 'w') as archive:
                for filename in ('scheduled_guard.py', 'app.py', 'sportrick.py', 'ssm_config.py', 'timetable.py',
                                 'bot_store.py', 'store_conflict.py'):
                    archive.write(source / filename, filename)
            result = subprocess.run([sys.executable, '-B', '-c', script, str(archive_path), ARN],
                                    cwd=directory, capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("'status': 'skipped'", result.stdout)
        self.assertIn('date skipped', result.stdout)

    def test_import_from_zip_root_and_package(self):
        source = Path(__file__).resolve().parents[1] / 'lambda_function'
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / 'function.zip'
            with ZipFile(archive_path, 'w') as archive:
                for filename in ('scheduled_guard.py', 'app.py', 'sportrick.py', 'ssm_config.py', 'timetable.py'):
                    archive.write(source / filename, filename)
            result = subprocess.run([sys.executable, '-B', '-c',
                                     'import sys; sys.path.insert(0, sys.argv[1]); '
                                     'import scheduled_guard; assert callable(scheduled_guard.lambda_handler)',
                                     str(archive_path)], cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(callable(guard.lambda_handler))


if __name__ == '__main__':
    unittest.main()
