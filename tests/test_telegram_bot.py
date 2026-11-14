import base64
import datetime
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lambda_function'))
import telegram_bot


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'TELEGRAM_WEBHOOK_SECRET': 'secret',
            'TELEGRAM_ALLOWED_USER_ID': '123',
            'TELEGRAM_BOT_TOKEN': 'token',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.post = patch.object(telegram_bot.requests, 'post').start()
        self.addCleanup(patch.stopall)
        self.post.return_value.json.return_value = {'ok': True}
        self.message = {'chat': {'id': 123, 'type': 'private'},
                        'from': {'id': 123}, 'text': '/start'}
        patch.object(telegram_bot, '_today', return_value=datetime.date(2026, 11, 12)).start()

    def invoke(self, message=None, **changes):
        event = {'headers': {'X-Telegram-Bot-Api-Secret-Token': 'secret'},
                 'requestContext': {'http': {'method': 'POST'}},
                 'body': json.dumps({'update_id': 42, 'message': self.message if message is None else message})}
        event.update(changes)
        return telegram_bot.lambda_handler(event, None)['statusCode']

    def test_commands_and_base64(self):
        for command in ('/start', '/help'):
            with self.subTest(command=command):
                self.message['text'] = command
                self.assertEqual(self.invoke(), 200)
        body = base64.b64encode(json.dumps({'message': self.message}).encode()).decode()
        self.assertEqual(self.invoke(body=body, isBase64Encoded=True), 200)
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual(self.post.call_args.kwargs['json']['chat_id'], 123)

    def test_rejects_invalid_requests_without_network(self):
        for changes, expected in [
            ({'headers': {}}, 403),
            ({'headers': {'x-telegram-bot-api-secret-token': 'wrong'}}, 403),
            ({'headers': None}, 400),
            ({'body': '{'}, 400),
            ({'body': '!!!', 'isBase64Encoded': True}, 400),
            ({'body': '[]'}, 400),
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(self.invoke(**changes), expected)
        for message in [
            {'chat': {'id': 123, 'type': 'group'}, 'from': {'id': 123}},
            {'chat': {'id': 124, 'type': 'private'}, 'from': {'id': 124}},
            {'chat': {'id': 124, 'type': 'private'}, 'from': {'id': 123}},
            {'chat': {'id': 123, 'type': 'private'}, 'from': {'id': True}},
        ]:
            self.assertEqual(self.invoke(message), 403)
        self.post.assert_not_called()

    def test_missing_configuration_fails_closed(self):
        for key in ('TELEGRAM_WEBHOOK_SECRET', 'TELEGRAM_ALLOWED_USER_ID', 'TELEGRAM_BOT_TOKEN'):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                self.assertEqual(self.invoke(), 503)
        self.post.assert_not_called()

    def test_unknown_command_is_ignored(self):
        self.message['text'] = '/unknown'
        self.assertEqual(self.invoke(), 200)
        self.post.assert_not_called()

    def test_non_message_update_is_ignored_after_secret_check(self):
        body = json.dumps({'update_id': 1, 'edited_message': {'message_id': 1}})
        self.assertEqual(self.invoke(body=body), 200)
        self.assertEqual(self.invoke(body=body, headers={}), 403)
        self.post.assert_not_called()

    def test_negative_or_malformed_api_response(self):
        for result in ({'ok': False, 'description': 'token'}, {}, [], {'ok': 1}):
            with self.subTest(result=result):
                self.post.return_value.json.return_value = result
                self.assertEqual(self.invoke(), 502)
        self.post.return_value.json.side_effect = ValueError('token')
        self.assertEqual(self.invoke(), 502)

    def test_network_failure_is_not_exposed(self):
        self.post.side_effect = telegram_bot.requests.RequestException('token')
        result = telegram_bot.lambda_handler({
            'headers': {'x-telegram-bot-api-secret-token': 'secret'},
            'requestContext': {'http': {'method': 'POST'}},
            'body': json.dumps({'message': self.message}),
        }, None)
        self.assertEqual(result, {'statusCode': 502, 'body': 'Request failed'})

    def callback(self, data='opaque', sender=123, chat=123, update_id=1, message_id=1000):
        message = {'chat': {'id': chat, 'type': 'private'}}
        if message_id is not None:
            message['message_id'] = message_id
        return self.invoke(body=json.dumps({'update_id': update_id, 'callback_query': {
            'id': 'callback', 'data': data, 'from': {'id': sender}, 'message': message,
        }}))

    def test_reads_and_confirmation(self):
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value={'bookings': []}) as read:
            self.message['text'] = '/bookings'
            self.assertEqual(self.invoke(), 200)
            read.assert_called_once_with({'action': 'bookings'})
        with patch.object(telegram_bot, '_store') as factory:
            factory.return_value.create_confirmation_once.return_value = 'opaque'
            self.message['text'] = '/book 12 2026-11-13 34'
            self.assertEqual(self.invoke(), 200)
            args = factory.return_value.create_confirmation_once.call_args.args
            self.assertEqual(args[0], '123')
            self.assertEqual(args[1], {'action': 'book', 'activity_id': '12', 'date': '2026-11-13', 'appointment_id': '34'})
            self.assertEqual(args[3], '42')
            self.assertEqual(self.post.call_args.kwargs['json']['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'opaque')

    def test_callback_duplicate_and_dispatch_error(self):
        with patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot, '_dispatch') as dispatch:
            store = factory.return_value
            store.consume_confirmation.return_value = {'status': 'pending', 'payload': {'action': 'cancel'}}
            store.should_dispatch.side_effect = [True, False]
            self.assertEqual(self.callback(), 200)
            self.assertEqual(self.callback(update_id=2), 200)
            dispatch.assert_called_once_with('opaque')
            self.assertEqual(store.consume_confirmation.call_args.args, ('opaque', '123', 2))
            self.assertEqual(self.post.call_args_list[0].args[0].split('/')[-1], 'answerCallbackQuery')
            store.should_dispatch.side_effect = None
            store.should_dispatch.return_value = True
            dispatch.side_effect = RuntimeError('token')
            self.assertEqual(self.callback(update_id=3), 502)
            self.assertEqual(self.post.call_args.args[0].split('/')[-1], 'answerCallbackQuery')

    def test_post_and_body_size_limits(self):
        self.assertEqual(self.invoke(requestContext={'http': {'method': 'GET'}}), 405)
        self.assertEqual(self.invoke(requestContext={}), 405)
        self.assertEqual(self.invoke(body='a' * (telegram_bot.MAX_BODY_BYTES + 1)), 413)
        self.assertEqual(self.invoke(body='é' * (telegram_bot.MAX_BODY_BYTES // 2 + 1)), 413)
        self.assertEqual(self.invoke(body='A' * (telegram_bot.MAX_BODY_BYTES + 1), isBase64Encoded=True), 413)
        self.post.assert_not_called()

    def test_pending_retry_and_finished_jobs(self):
        with patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot, '_dispatch') as dispatch:
            store = factory.return_value
            store.consume_confirmation.return_value = {'status': 'pending', 'payload': {'action': 'book'}}
            store.should_dispatch.return_value = True
            dispatch.side_effect = [RuntimeError('secret token'), None]
            self.assertEqual(self.callback(), 502)
            self.assertEqual(self.callback(), 200)
            self.assertEqual(dispatch.call_count, 2)
            for status in ('running', 'completed'):
                store.consume_confirmation.return_value = {'status': status, 'payload': {'action': 'book'}}
                store.should_dispatch.return_value = False
                self.assertEqual(self.callback(), 200)
                if status == 'running':
                    self.assertIn('in corso o esito incerto; verifica impegni', self.post.call_args.kwargs['json']['text'])
                    self.assertNotIn('null', self.post.call_args.kwargs['json']['text'])
            self.assertEqual(dispatch.call_count, 2)

    def test_callback_authorization_and_expiry(self):
        with patch.object(telegram_bot, '_store') as factory:
            self.assertEqual(self.callback(sender=124), 403)
            self.assertEqual(self.callback(chat=124), 403)
            factory.assert_not_called()
            factory.return_value.consume_confirmation.side_effect = telegram_bot.StoreConflict('expired')
            self.assertEqual(self.callback(), 200)
            self.assertIn('scaduta', self.post.call_args.kwargs['json']['text'])

    def test_schedule_confirmation_and_disable(self):
        snapshot = {'State': 'ENABLED', 'ScheduleExpression': 'old'}
        with patch.object(telegram_bot, '_scheduler') as scheduler, patch.object(telegram_bot, '_store') as factory:
            scheduler.return_value.get_schedule.return_value = snapshot
            store = factory.return_value
            store.get_update.return_value = None
            store.create_confirmation_once.return_value = 'opaque'
            self.message['text'] = '/weekly MON,WED 08:30'
            self.assertEqual(self.invoke(), 200)
            payload = store.create_confirmation_once.call_args.args[1]
            store.consume_confirmation.return_value = {'status': 'pending', 'payload': payload}
            store.should_dispatch.return_value = True
            store.claim_job.return_value = {'payload': payload}
            self.assertEqual(self.callback(), 200)
            scheduler.return_value.set_weekly_time.assert_called_once_with(snapshot, ['MON', 'WED'], 8, 30)
            self.message['text'] = '/disable'
            self.assertEqual(self.invoke(), 200)
            payload = store.create_confirmation_once.call_args.args[1]
            store.consume_confirmation.return_value = {'status': 'pending', 'payload': payload}
            store.claim_job.return_value = {'payload': payload}
            self.assertEqual(self.callback(update_id=2), 200)
            scheduler.return_value.disable.assert_called_once_with(snapshot, confirmed=True)

    def test_async_dispatch_contract_and_no_sdk_retry(self):
        with patch('boto3.client') as client, patch.dict(os.environ, {'BOT_WORKER_FUNCTION': 'worker'}):
            client.return_value.invoke.return_value = {'StatusCode': 202}
            telegram_bot._dispatch('opaque')
            self.assertEqual(client.call_args.args, ('lambda',))
            self.assertEqual(client.call_args.kwargs['config'].retries['total_max_attempts'], 1)
            self.assertEqual(client.return_value.invoke.call_args.kwargs, {
                'FunctionName': 'worker', 'InvocationType': 'Event', 'Payload': b'{"job_id": "opaque"}',
            })
            for response in ({}, {'StatusCode': 500}):
                client.return_value.invoke.return_value = response
                with self.assertRaises(RuntimeError):
                    telegram_bot._dispatch('opaque')

    def test_read_formatting_and_splitting(self):
        cases = [
            ('bookings', {'id': '123', 'description': 'Yoga', 'isOnWaitlist': False}, ['ID impegno: 123', 'Descrizione: Yoga', 'In lista d’attesa: False']),
            ('activities', {'id': '456', 'name': 'Pilates'}, ['ID attività: 456', 'Attività: Pilates']),
            ('schedule', {'AppointmentId': '789', 'TimeStart': '18:00', 'Notes': 'Prenota'}, ['ID appuntamento: 789', 'Ora: 18:00', 'Note: Prenota']),
        ]
        for action, item, expected in cases:
            with self.subTest(action=action):
                text = telegram_bot._format_read(action, {action: [item]})
                for part in expected:
                    self.assertIn(part, text)
                self.assertIn('nessun risultato', telegram_bot._format_read(action, {action: []}))
        text = telegram_bot._format_read('activities', {'activities': [{'name': '🧘' * 2500}]})
        telegram_bot._send('token', 123, text)
        chunks = [call.kwargs['json']['text'] for call in self.post.call_args_list]
        self.assertEqual(''.join(chunks), text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))

    def test_menu_callbacks_and_cancel_confirmation(self):
        self.assertEqual(self.invoke(), 200)
        keyboard = self.post.call_args.kwargs['json']['reply_markup']['inline_keyboard']
        self.assertEqual([b['callback_data'] for row in keyboard for b in row],
                         ['read:bookings', 'read:activities', 'menu:automation', 'menu:help'])
        with patch.object(telegram_bot, '_scheduler') as scheduler:
            scheduler.return_value.get_schedule.return_value = {'State': 'DISABLED'}
            self.assertEqual(self.callback('menu:automation'), 200)
            self.assertIn('DISABLED', self.post.call_args.kwargs['json']['text'])
        self.assertEqual(self.callback('menu:help'), 200)
        self.assertIn('servono gli ID', self.post.call_args.kwargs['json']['text'])
        with patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot, '_dispatch') as dispatch:
            store = factory.return_value
            store.create_confirmation_once.return_value = 'opaque'
            store._encode.side_effect = lambda x: x
            self.message['text'] = '/cancel 123'
            self.assertEqual(self.invoke(), 200)
            payload = self.post.call_args.kwargs['json']
            self.assertIn('Cancellazione', payload['text'])
            self.assertIn('ID impegno: 123', payload['text'])
            self.assertNotIn('fingerprint', payload['text'])
            self.assertEqual(payload['reply_markup']['inline_keyboard'][0][1]['callback_data'], 'cancel:opaque')
            self.assertEqual(self.callback('cancel:opaque'), 200)
            self.assertEqual(store.client.delete_item.call_args.kwargs['Key'], {'pk': 'confirmation#opaque'})
            self.assertIn('attribute_not_exists(job_id)', store.client.delete_item.call_args.kwargs['ConditionExpression'])
            self.assertEqual(store.client.delete_item.call_args.kwargs['ExpressionAttributeValues'], {':owner': '123'})
            store.consume_confirmation.assert_not_called()
            dispatch.assert_not_called()
            self.assertEqual(self.callback('cancel:opaque', sender=124), 403)
            self.assertEqual(store.client.delete_item.call_count, 1)

    def test_command_redelivery_and_collision(self):
        records = {}
        with patch.object(telegram_bot, '_store') as factory:
            store = factory.return_value
            def create(owner, payload, expires_at, update_id):
                if update_id in records and records[update_id] != (owner, payload):
                    raise telegram_bot.StoreConflict('collision')
                records[update_id] = (owner, payload.copy())
                return 'opaque'
            store.create_confirmation_once.side_effect = create
            self.message['text'] = '/cancel 123'
            self.assertEqual(self.invoke(), 200)
            store.consume_confirmation.return_value = {'status': 'completed', 'result': {'status': 'confirmed'}}
            store.should_dispatch.return_value = False
            self.assertEqual(self.callback(), 200)
            self.assertEqual(self.invoke(), 200)
            self.assertEqual(len(records), 1)
            self.assertEqual(store.create_confirmation_once.call_args.args[3], '42')
            self.assertEqual(self.post.call_args.kwargs['json']['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'opaque')
            self.message['text'] = '/cancel 456'
            self.assertEqual(self.invoke(), 400)
            self.assertEqual(len(records), 1)

    def test_missing_or_invalid_command_update_id_has_no_effect(self):
        with patch.object(telegram_bot, '_store') as store, patch.object(telegram_bot, '_scheduler') as scheduler:
            for command in ('/cancel 123', '/weekly MON 08:30', '/disable'):
                self.message['text'] = command
                for update in ({}, {'update_id': None}, {'update_id': True}, {'update_id': -1}, {'update_id': '42'}):
                    body = json.dumps({**update, 'message': self.message})
                    self.assertEqual(self.invoke(body=body), 400)
            store.assert_not_called()
            scheduler.assert_not_called()
            self.post.assert_not_called()

    def test_schedule_redelivery_reuses_original_snapshot_even_after_change(self):
        with patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot, '_scheduler') as scheduler:
            store = factory.return_value
            scheduler.return_value.get_schedule.return_value = {'State': 'ENABLED'}
            store.get_update.return_value = None
            store.create_confirmation_once.return_value = 'opaque'
            self.message['text'] = '/weekly MON 08:30'
            self.assertEqual(self.invoke(), 200)
            payload = store.create_confirmation_once.call_args.args[1].copy()
            store.get_update.return_value = {'kind': 'confirmation', 'owner': '123', 'payload': payload, 'token': 'opaque'}
            scheduler.return_value.get_schedule.return_value = {'State': 'DISABLED'}
            self.assertEqual(self.invoke(), 200)
            self.assertEqual(store.create_confirmation_once.call_args.args[1], payload)
            self.assertEqual(scheduler.return_value.get_schedule.call_count, 1)
            self.message['text'] = '/weekly TUE 08:30'
            self.assertEqual(self.invoke(), 400)
            self.assertEqual(store.create_confirmation_once.call_count, 2)

    # --- guided flow -------------------------------------------------------
    def last(self):
        return self.post.call_args.kwargs['json']

    def buttons(self):
        return [b for row in self.last()['reply_markup']['inline_keyboard'] for b in row]

    def data(self):
        return [b['callback_data'] for b in self.buttons()]

    @staticmethod
    def cb(kind, activity_id, date, appointment_id, sid):
        pack = telegram_bot._pack_id
        return f'g{kind}:{pack(activity_id)}:{date}:{pack(appointment_id)}:{sid}'

    def sid(self, update_id):
        return telegram_bot._screen_id('123', update_id)

    def schedule(self, **changes):
        slot = {'AppointmentId': '34', 'TimeStart': '18:00', 'EventName': 'Yoga', 'Type': 'class',
                'Status': 'Available', 'Notes': 'Prenota'}
        slot.update(changes)
        return {'schedule': [slot,
                             {'AppointmentId': '35', 'TimeStart': '19:00', 'EventName': 'Full', 'Type': 'class',
                              'Status': 'Full', 'Notes': telegram_bot.WAITLIST_NOTE},
                             {'AppointmentId': '36', 'TimeStart': '20:00', 'Type': 'service', 'Notes': 'Prenota'}]}

    def bookings(self):
        return {'bookings': [
            {'id': '1', 'description': 'Yoga', 'DateStart': '2026-11-13', 'TimeStart': '18:00',
             'isOnWaitlist': False, 'canDelete': True},
            {'id': '2', 'description': 'Pilates', 'isOnWaitlist': True, 'canDeleteFromWaitlist': True,
             'canMoveToAttendants': True},
            {'id': '3', 'description': 'Locked', 'isOnWaitlist': False, 'canDelete': False},
            {'id': '4', 'description': 'Service', 'isService': True, 'canDelete': True},
            {'id': 'x', 'canDelete': True},
        ]}

    def test_activities_offer_flow_buttons(self):
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value={'activities': [
                {'id': '12', 'name': 'Yoga'}, {'id': 'bad', 'name': 'X'}]}):
            self.assertEqual(self.callback('read:activities'), 200)
        self.assertEqual(self.data(), ['fa:12'])

    def test_activity_shows_next_seven_days(self):
        self.assertEqual(self.callback('fa:12'), 200)
        data = [d for d in self.data() if d.startswith('fd:')]
        self.assertEqual(data, ['fd:12:' + d for d in (
            '20261112', '20261113', '20261114', '20261115', '20261116', '20261117', '20261118')])
        self.assertTrue(all(len(d.encode()) <= 64 for d in self.data()))
        self.assertEqual(self.callback('fa:x'), 400)

    def test_slots_carry_screen_id_and_stay_within_limit(self):
        sid = self.sid(7)
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.schedule()) as read:
            self.assertEqual(self.callback('fd:12:20261113', update_id=7), 200)
            read.assert_called_once_with({'action': 'schedule', 'activity_id': '12', 'date': '2026-11-13'})
            self.assertEqual(self.data(), [self.cb('s', '12', '20261113', '34', sid), self.cb('w', '12', '20261113', '35', sid), 'fa:12'])
            self.assertTrue(all(len(d.encode()) <= 64 for d in self.data()))
            for bad in ('fd:12:20261119', 'fd:12:20261111', 'fd:12:2026-11-13', 'fd:12:20261313'):
                self.assertEqual(self.callback(bad), 400)
            self.assertEqual(read.call_count, 1)
            # the id strings are preserved verbatim, including leading zeros and long values
            self.assertEqual(self.callback('fd:0012:20261113', update_id=8), 200)
            self.assertEqual(read.call_args.args[0]['activity_id'], '0012')

    def test_screens_need_valid_update_id(self):
        with patch.object(telegram_bot.telegram_worker, 'read_action') as read:
            for update_id in (None, True, -1, '5'):
                body = {'callback_query': {'id': 'c', 'data': 'fd:12:20261113', 'from': {'id': 123},
                                           'message': {'chat': {'id': 123, 'type': 'private'}}}}
                if update_id is not None:
                    body['update_id'] = update_id
                with self.subTest(update_id=update_id):
                    self.assertEqual(self.invoke(body=json.dumps(body)), 400)
            read.assert_not_called()

    def test_slot_identity_is_per_screen_not_per_message_or_time(self):
        sid = self.sid(7)
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.schedule()), \
                patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot, '_dispatch') as dispatch, \
                patch.object(telegram_bot.time, 'time') as clock:
            create = factory.return_value.create_confirmation_once
            create.return_value = 'opaque'
            clock.return_value = 1_000_000
            button = self.cb('s', '12', '20261113', '34', sid)
            # clicks on copies of the same screen: other message ids, updates, redeliveries, much later
            for update_id, message_id, now in ((5, 1000, 1_000_000), (6, 1001, 1_000_299), (6, 1001, 1_000_301),
                                               (7, 2000, 9_000_000), (8, None, 9_000_001)):
                clock.return_value = now
                self.assertEqual(self.callback(button, update_id=update_id, message_id=message_id), 200)
            ids = {c.args[3] for c in create.call_args_list}
            self.assertEqual(len(ids), 1)
            self.assertRegex(ids.pop(), r'^flow-[0-9a-f]{40}$')
            first = create.call_args_list[0]
            self.assertEqual(first.args[:2], ('123', {'action': 'book', 'activity_id': '12',
                                                      'date': '2026-11-13', 'appointment_id': '34'}))
            self.assertEqual(first.args[2], 1_000_300)
            self.assertIn('Yoga 18:00', self.last()['text'])
            self.assertEqual(self.data(), ['opaque', 'cancel:opaque'])
            base = first.args[3]
            # a new screen (new originating update), another slot or mode: new identities
            self.assertEqual(self.callback(self.cb('s', '12', '20261113', '34', self.sid(99))), 200)
            self.assertEqual(self.callback(self.cb('w', '12', '20261113', '35', sid)), 200)
            self.assertEqual(create.call_args.args[1]['action'], 'waitlist_join')
            newer = [c.args[3] for c in create.call_args_list[-2:]]
            self.assertEqual(len({base, *newer}), 3)
            factory.return_value.consume_confirmation.assert_not_called()
            dispatch.assert_not_called()

    def test_store_keeps_original_token_and_expiry_across_copies(self):
        records = {}

        def create(owner, payload, expires_at, update_id):
            return records.setdefault(update_id, ('tok%d' % len(records), expires_at))[0]

        button = self.cb('s', '12', '20261113', '34', self.sid(7))
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.schedule()), \
                patch.object(telegram_bot, '_store') as factory, patch.object(telegram_bot.time, 'time') as clock:
            factory.return_value.create_confirmation_once.side_effect = create
            clock.return_value = 1000
            self.assertEqual(self.callback(button), 200)
            first = self.data()[0]
            clock.return_value = 100_000
            self.assertEqual(self.callback(button, update_id=2, message_id=555), 200)
            self.assertEqual(self.data()[0], first)
            self.assertEqual(list(records.values())[0][1], 1300)
            self.assertEqual(self.callback(self.cb('s', '12', '20261113', '34', self.sid(8))), 200)
            self.assertNotEqual(self.data()[0], first)

    def test_stale_slot_creates_no_confirmation(self):
        sid = self.sid(7)
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.schedule(Notes='Esaurito')), \
                patch.object(telegram_bot, '_store') as factory:
            for data in (self.cb('s', '12', '20261113', '34', sid), self.cb('w', '12', '20261113', '34', sid), self.cb('s', '12', '20261113', '99', sid)):
                self.assertEqual(self.callback(data), 200)
            factory.assert_not_called()
            self.assertIn('non più disponibile', self.last()['text'])

    def test_legacy_or_malformed_flow_callbacks_do_nothing(self):
        with patch.object(telegram_bot.telegram_worker, 'read_action') as read, patch.object(telegram_bot, '_store') as factory:
            for data in ('fs:12:20261113:34', f'fs:12:20261113:34:{self.sid(1)}', 'gs:A:20261113:A:' + self.sid(1),
                         'gs:12:20261113:34:' + self.sid(1), 'bc:1', 'bc:1:zz', f'bc:1:{self.sid(1)}:x'):
                with self.subTest(data=data):
                    self.assertIn(self.callback(data), (200, 400))
            read.assert_not_called()
            factory.assert_not_called()
        self.callback('bc:1')
        self.assertIn('obsoleta', self.last()['text'])

    def test_bookings_buttons_follow_portal_flags(self):
        sid = self.sid(42)
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.bookings()):
            self.message['text'] = '/bookings'
            self.assertEqual(self.invoke(), 200)
        self.assertEqual(self.data(), [f'bc:1:{sid}', f'bw:2:{sid}', f'bm:2:{sid}'])
        self.assertTrue(all(len(d.encode()) <= 64 for d in self.data()))

    def test_booking_actions_need_store_confirmation_and_current_flags(self):
        sid = self.sid(5)
        with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=self.bookings()), \
                patch.object(telegram_bot, '_store') as factory:
            create = factory.return_value.create_confirmation_once
            create.return_value = 'opaque'
            for data, action, booking_id in ((f'bc:1:{sid}', 'cancel', '1'), (f'bw:2:{sid}', 'waitlist_leave', '2'),
                                             (f'bm:2:{sid}', 'waitlist_confirm', '2')):
                self.assertEqual(self.callback(data), 200)
                self.assertEqual(create.call_args.args[1], {'action': action, 'booking_id': booking_id})
            for data in ('bw:1', 'bc:3', 'bc:4', 'bc:2', 'bc:9'):
                self.assertEqual(self.callback(f'{data}:{sid}'), 200)
            self.assertEqual(create.call_count, 3)
            self.assertIn('non consentita', self.last()['text'])
            factory.return_value.consume_confirmation.assert_not_called()

    def test_flow_callbacks_require_authorization_and_valid_ids(self):
        sid = self.sid(1)
        with patch.object(telegram_bot.telegram_worker, 'read_action') as read:
            self.assertEqual(self.callback('fa:12', sender=124), 403)
            self.assertEqual(self.callback(f'bc:1:{sid}', chat=124), 403)
            self.assertEqual(self.callback(f'bc:1x:{sid}'), 400)
            self.assertEqual(self.callback(f'gs:{telegram_bot._pack_id("1")}:20261113:!:{sid}'), 400)
            read.assert_not_called()

    def test_callback_limit_helper(self):
        self.assertIsNone(telegram_bot._button('x', 'a' * 65))
        self.assertIsNotNone(telegram_bot._button('x', 'a' * 64))

    def test_async_reads_queue_ui_and_never_read_in_webhook(self):
        env = {'BOT_ASYNC_READS': '1', 'BOT_WORKER_FUNCTION': 'worker'}
        sid = self.sid(7)
        with patch.dict(os.environ, env), patch('boto3.client') as client, \
                patch.object(telegram_bot.telegram_worker, 'read_action') as read, \
                patch.object(telegram_bot, '_store') as factory:
            client.return_value.invoke.return_value = {'StatusCode': 202}
            self.assertEqual(self.callback(self.cb('s', '12', '20261113', '34', sid)), 200)
            self.assertEqual(self.callback(f'bc:1:{sid}'), 200)
            self.assertEqual(self.callback('fd:12:20261113', update_id=7), 200)
            self.assertEqual(self.callback('read:bookings', update_id=7), 200)
            self.message['text'] = '/schedule 12 2026-11-13'
            self.assertEqual(self.invoke(), 200)
            read.assert_not_called()
            factory.assert_not_called()
            events = [json.loads(c.kwargs['Payload']) for c in client.return_value.invoke.call_args_list]
            self.assertTrue(all(c.kwargs['InvocationType'] == 'Event' and c.kwargs['FunctionName'] == 'worker'
                                for c in client.return_value.invoke.call_args_list))
            slot, cancel, day, bookings, schedule = events
            self.assertEqual(slot['read'], {'action': 'schedule', 'activity_id': '12', 'date': '2026-11-13'})
            self.assertEqual(slot['chat_id'], '123')
            self.assertEqual(slot['ui']['kind'], 'slot')
            self.assertRegex(slot['ui']['sel'], r'^flow-[0-9a-f]{40}$')
            self.assertEqual(cancel['read'], {'action': 'bookings'})
            self.assertEqual(cancel['ui']['action'], 'cancel')
            self.assertEqual(day['ui'], {'kind': 'day', 'activity_id': '12', 'date': '2026-11-13', 'sid': sid})
            self.assertEqual(bookings['ui'], {'kind': 'bookings', 'sid': sid})
            self.assertEqual(schedule['ui']['kind'], 'schedule')
            self.assertIn('recuperando', self.last()['text'])
            client.return_value.invoke.return_value = {'StatusCode': 500}
            self.assertEqual(self.callback('read:activities'), 502)
        with patch.dict(os.environ, {'BOT_ASYNC_READS': '1'}), \
                patch.object(telegram_bot.telegram_worker, 'read_action', return_value={'activities': []}) as read:
            self.assertEqual(self.callback('read:activities'), 200)  # no worker configured: stays synchronous
            read.assert_called_once()

    def test_render_ui_contract_for_worker(self):
        sid = self.sid(7)
        sel = telegram_bot._selection_id('123', sid, self.cb('s', '12', '20261113', '34', sid).rsplit(':', 1)[0])
        store = unittest.mock.Mock()
        store.create_confirmation_once.return_value = 'opaque'
        slot_ui = {'kind': 'slot', 'action': 'book', 'sel': sel, 'activity_id': '12',
                   'date': '2026-11-13', 'appointment_id': '34'}
        text, keyboard = telegram_bot.render_ui(slot_ui, self.schedule(), '123', store)
        self.assertIn('Confermi: Prenotazione', text)
        self.assertEqual([b['callback_data'] for b in keyboard[0]], ['opaque', 'cancel:opaque'])
        self.assertEqual(store.create_confirmation_once.call_args.args[3], sel)
        day_ui = {'kind': 'day', 'activity_id': '12', 'date': '2026-11-13', 'sid': sid}
        text, keyboard = telegram_bot.render_ui(day_ui, self.schedule(), '123')
        self.assertEqual([b['callback_data'] for row in keyboard for b in row],
                         [self.cb('s', '12', '20261113', '34', sid), self.cb('w', '12', '20261113', '35', sid), 'fa:12'])
        text, keyboard = telegram_bot.render_ui({'kind': 'bookings', 'sid': sid}, self.bookings(), '123')
        self.assertEqual([b['callback_data'] for row in keyboard for b in row],
                         [f'bc:1:{sid}', f'bw:2:{sid}', f'bm:2:{sid}'])
        store.reset_mock()
        stale = telegram_bot.render_ui(slot_ui, self.schedule(Notes='Esaurito'), '123', store)
        self.assertIsNone(stale[1])
        store.create_confirmation_once.assert_not_called()
        for bad in ({**slot_ui, 'sel': 'x'}, {**slot_ui, 'sel': None}, {**slot_ui, 'activity_id': '1x'},
                    {'kind': 'unknown'}, {'kind': 'booking', 'action': 'cancel', 'booking_id': '1', 'sel': 'bad'},
                    {**day_ui, 'sid': 'nope'}, {k: v for k, v in day_ui.items() if k != 'sid'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                telegram_bot.render_ui(bad, self.schedule(), '123', store)
        store.create_confirmation_once.assert_not_called()


    LONG_IDS = ('123456789012345678', '9' * 19, '00123456789012345678', '1' * 20, '0' * 20, '98765432109876543210')

    def test_id_packing_round_trips_exactly(self):
        for value in ('0', '00', '7', '12', '000', *self.LONG_IDS, '0' * 33, '9' * 33):
            with self.subTest(value=value):
                packed = telegram_bot._pack_id(value)
                self.assertEqual(telegram_bot._unpack_id(packed), value)
                self.assertNotIn(':', packed)
        for bad in ('', 'A', 'AA', 'B', '!', '1:', 'a' * 25, None, 7):  # non-canonical or malformed: never guessed
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                telegram_bot._unpack_id(bad)
        for bad in ('', '1x', ' 1', '١٢', None):
            with self.subTest(unpackable=bad), self.assertRaises(ValueError):
                telegram_bot._pack_id(bad)

    def test_long_ids_round_trip_through_both_directions_within_callback_limit(self):
        sid = self.sid(7)
        for activity_id in self.LONG_IDS:
            for appointment_id in self.LONG_IDS:
                with self.subTest(activity=activity_id, appointment=appointment_id):
                    schedule = {'schedule': [
                        {'AppointmentId': appointment_id, 'TimeStart': '18:00', 'EventName': 'Yoga', 'Type': 'class',
                         'Status': 'Available', 'Notes': 'Prenota'},
                        {'AppointmentId': appointment_id[::-1], 'TimeStart': '19:00', 'EventName': 'Full', 'Type': 'class',
                         'Status': 'Full', 'Notes': telegram_bot.WAITLIST_NOTE}]}
                    day_ui = {'kind': 'day', 'activity_id': activity_id, 'date': '2026-11-13', 'sid': sid}
                    # direction 1: ids -> buttons (must exist and fit) -> parsed ui carries identical ids
                    _, keyboard = telegram_bot.render_ui(day_ui, schedule, '123')
                    data = [b['callback_data'] for row in keyboard for b in row]
                    self.assertEqual(len(data), 3)
                    self.assertTrue(all(len(d.encode()) <= 64 for d in data))
                    book, wait = telegram_bot._parse_ui(data[0], '123', 1), telegram_bot._parse_ui(data[1], '123', 1)
                    self.assertEqual((book['activity_id'], book['appointment_id']), (activity_id, appointment_id))
                    self.assertEqual((wait['activity_id'], wait['appointment_id']), (activity_id, appointment_id[::-1]))
                    self.assertEqual((book['action'], wait['action']), ('book', 'waitlist_join'))
                    # direction 2: ui -> confirmation payload keeps the exact strings
                    store = unittest.mock.Mock()
                    store.create_confirmation_once.return_value = 'opaque'
                    telegram_bot.render_ui(book, schedule, '123', store)
                    self.assertEqual(store.create_confirmation_once.call_args.args[1], {
                        'action': 'book', 'activity_id': activity_id, 'date': '2026-11-13',
                        'appointment_id': appointment_id})
                    # end to end through the webhook
                    with patch.object(telegram_bot.telegram_worker, 'read_action', return_value=schedule) as read, \
                            patch.object(telegram_bot, '_store') as factory:
                        factory.return_value.create_confirmation_once.return_value = 'opaque'
                        self.assertEqual(self.callback(data[0]), 200)
                        self.assertEqual(factory.return_value.create_confirmation_once.call_args.args[1]['appointment_id'],
                                         appointment_id)
                        self.assertEqual(self.callback(f'fd:{activity_id}:20261113', update_id=7), 200)
                        read.assert_called_with({'action': 'schedule', 'activity_id': activity_id, 'date': '2026-11-13'})

    def test_other_callbacks_with_long_ids_fit_and_booking_ids_are_verbatim(self):
        sid = self.sid(5)
        for value in self.LONG_IDS:
            bookings = {'bookings': [{'id': value, 'description': 'Yoga', 'isOnWaitlist': False, 'canDelete': True}]}
            with self.subTest(value=value), patch.object(telegram_bot.telegram_worker, 'read_action', return_value=bookings), \
                    patch.object(telegram_bot, '_store') as factory:
                _, keyboard = telegram_bot.render_ui({'kind': 'bookings', 'sid': sid}, bookings, '123')
                data = keyboard[0][0]['callback_data']
                self.assertEqual(data, f'bc:{value}:{sid}')
                self.assertLessEqual(len(data.encode()), 64)
                factory.return_value.create_confirmation_once.return_value = 'opaque'
                self.assertEqual(self.callback(data), 200)
                self.assertEqual(factory.return_value.create_confirmation_once.call_args.args[1],
                                 {'action': 'cancel', 'booking_id': value})
        activities = {'activities': [{'id': self.LONG_IDS[2], 'name': 'Yoga'}]}
        _, keyboard = telegram_bot.render_ui({'kind': 'activities'}, activities, '123')
        self.assertEqual(keyboard[0][0]['callback_data'], 'fa:' + self.LONG_IDS[2])
        self.assertEqual(self.callback('fa:' + self.LONG_IDS[2]), 200)
        self.assertIn('fd:' + self.LONG_IDS[2] + ':20261112', self.data())
        self.assertTrue(all(len(d.encode()) <= 64 for d in self.data()))

    def test_supported_id_length_limit_is_explicit_not_silent(self):
        sid = self.sid(7)

        def day(length):
            ident = '9' * length
            schedule = {'schedule': [{'AppointmentId': ident, 'TimeStart': '18:00', 'EventName': 'Yoga',
                                      'Type': 'class', 'Status': 'Available', 'Notes': 'Prenota'}]}
            return telegram_bot.render_ui({'kind': 'day', 'activity_id': ident, 'date': '2026-11-13', 'sid': sid},
                                          schedule, '123')

        limit = max(n for n in range(1, 60) if 'troppo lunghi' not in day(n)[0])
        self.assertGreaterEqual(limit, 30)
        text, keyboard = day(limit)
        self.assertLessEqual(max(len(b['callback_data'].encode()) for row in keyboard for b in row), 64)
        self.assertEqual(len(keyboard), 2)
        text, keyboard = day(limit + 1)
        self.assertIn('/book', text)
        self.assertEqual(keyboard, [[{'text': '⬅️ Indietro', 'callback_data': 'fa:' + '9' * (limit + 1)}]])
        self.assertEqual(limit, 34)


class DuplicateScreenIntegrationTests(unittest.TestCase):
    """Webhook + worker-style rendering over the real BotStore with an in-memory DynamoDB."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_bot_store import FakeDynamo
        import bot_store
        self.now = 1_000_000
        self.store = bot_store.BotStore(FakeDynamo(), 'table', lambda: self.now)
        self.events, self.dispatched, self.sent = [], [], []
        env = patch.dict(os.environ, {
            'TELEGRAM_WEBHOOK_SECRET': 'secret', 'TELEGRAM_ALLOWED_USER_ID': '42', 'TELEGRAM_BOT_TOKEN': 'token',
            'BOT_ASYNC_READS': '1', 'BOT_WORKER_FUNCTION': 'worker'}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        for target, replacement in (
                ('telegram_bot._store', lambda: self.store), ('telegram_bot.time.time', lambda: self.now),
                ('telegram_bot._today', lambda: datetime.date(2026, 11, 12)),
                ('telegram_bot._dispatch', lambda job_id: self.dispatched.append(job_id)),
                ('telegram_bot._dispatch_read', lambda request, ui, chat_id: self.events.append(
                    {'read': request, 'chat_id': str(chat_id), 'ui': ui})),
                ('telegram_bot._send', lambda token, chat_id, text, keyboard=None: self.sent.append((text, keyboard))),
                ('telegram_bot.telegram_worker.read_action', self.read_action)):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def read_action(self, request):
        if request['action'] == 'schedule':
            return {'schedule': [{'AppointmentId': '34', 'TimeStart': '18:00', 'EventName': 'Yoga',
                                  'Type': 'class', 'Status': 'Available', 'Notes': 'Prenota'}]}
        return {'bookings': [{'id': '1', 'description': 'Yoga', 'isOnWaitlist': False, 'canDelete': True}]}

    def click(self, data, update_id, message_id):
        update = {'update_id': update_id, 'callback_query': {'id': 'c', 'data': data, 'from': {'id': 42}, 'message': {
            'message_id': message_id, 'chat': {'id': 42, 'type': 'private'}}}}
        with patch.object(telegram_bot, '_telegram'):
            return telegram_bot.lambda_handler({
                'headers': {'x-telegram-bot-api-secret-token': 'secret'},
                'requestContext': {'http': {'method': 'POST'}}, 'body': json.dumps(update)}, None)['statusCode']

    def worker_render(self, event):
        """What the worker does: re-read, render with chat_id as owner, send."""
        result = self.read_action(event['read'])
        return telegram_bot.render_ui(event['ui'], result, event['chat_id'])

    def copies(self, event, times=2):
        screens = [self.worker_render(event) for _ in range(times)]
        self.assertEqual(screens[0], screens[1])
        return screens[0]

    def test_duplicate_copies_share_one_confirmation_and_one_job(self):
        self.assertEqual(self.click('fd:10:20261113', 11, 1), 200)
        _, keyboard = self.copies(self.events[-1])  # worker retried: two copies of the day screen
        slot = keyboard[0][0]['callback_data']
        self.assertLessEqual(len(slot.encode()), 64)
        self.assertEqual(self.click(slot, 12, 100), 200)  # click on copy A
        self.assertEqual(self.click(slot, 13, 101), 200)  # click on copy B (other message id)
        self.assertEqual(self.click(slot, 13, 101), 200)  # redelivery
        confirmations = [self.worker_render(e) for e in self.events[1:]]
        tokens = {kb[0][0]['callback_data'] for _, kb in confirmations}
        self.assertEqual(len(tokens), 1)
        token = tokens.pop()
        pks = [k for k in self.store.client.items if k.startswith('confirmation#')]
        self.assertEqual(len(pks), 1)
        self.assertEqual(self.dispatched, [])  # nothing is mutated without the confirm click
        self.assertEqual(self.click(token, 14, 102), 200)
        self.assertEqual(self.dispatched, [token])
        self.store.claim_job(token)  # the worker claims the job: later taps on any copy cannot re-dispatch
        self.assertEqual(self.click(token, 15, 103), 200)
        self.assertEqual(self.dispatched, [token])
        self.assertEqual(len([k for k in self.store.client.items if k.startswith('job#')]), 1)

    def test_expiry_is_original_and_new_screen_gets_new_token(self):
        self.assertEqual(self.click('fd:10:20261113', 11, 1), 200)
        _, keyboard = self.worker_render(self.events[-1])
        slot = keyboard[0][0]['callback_data']
        self.click(slot, 12, 100)
        token = self.worker_render(self.events[-1])[1][0][0]['callback_data']
        self.now += 301  # past the original expiry; a late click on another copy must not extend it
        self.click(slot, 13, 101)
        late = self.worker_render(self.events[-1])[1][0][0]['callback_data']
        self.assertEqual(late, token)
        self.assertEqual(self.click(token, 14, 102), 200)
        self.assertEqual(self.dispatched, [])
        self.assertIsNone(self.store.get_job(token))
        # a new request for the day is a new screen: new sid, new token, can be confirmed
        self.assertEqual(self.click('fd:10:20261113', 20, 2), 200)
        _, keyboard = self.worker_render(self.events[-1])
        fresh_slot = keyboard[0][0]['callback_data']
        self.assertNotEqual(fresh_slot, slot)
        self.click(fresh_slot, 21, 200)
        fresh = self.worker_render(self.events[-1])[1][0][0]['callback_data']
        self.assertNotEqual(fresh, token)
        self.assertEqual(self.click(fresh, 22, 201), 200)
        self.assertEqual(self.dispatched, [fresh])

    def test_booking_cancel_copies_share_one_confirmation(self):
        message = {'message_id': 1, 'chat': {'id': 42, 'type': 'private'}, 'from': {'id': 42}, 'text': '/bookings'}
        with patch.object(telegram_bot, '_telegram'):
            telegram_bot._handle_message(message, 'token', '42', 30)
        _, keyboard = self.copies(self.events[-1], 3)
        cancel = keyboard[0][0]['callback_data']
        self.assertTrue(cancel.startswith('bc:1:'))
        self.click(cancel, 31, 10)
        self.click(cancel, 32, 11)
        tokens = {self.worker_render(e)[1][0][0]['callback_data'] for e in self.events[1:]}
        self.assertEqual(len(tokens), 1)
        self.assertEqual(self.dispatched, [])

if __name__ == '__main__':
    unittest.main()
