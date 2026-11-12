import os
import unittest
from unittest.mock import Mock, patch

from lambda_function import telegram_worker
from lambda_function.bot_store import StoreConflict


class TelegramWorkerTests(unittest.TestCase):
    def test_read_actions(self):
        for action, fields, args in (
            ('activities', {}, ()),
            ('bookings', {}, ()),
            ('schedule', {'activity_id': '123', 'date': '2026-11-12'}, ('123', '2026-11-12')),
        ):
            with self.subTest(action=action), patch.dict(os.environ, {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'secret'}), patch.object(telegram_worker.requests, 'Session') as session, patch.object(telegram_worker, 'SportrickClient') as factory:
                client = factory.return_value
                result = [{'id': '123'}]
                getattr(client, action).return_value = result
                self.assertEqual(telegram_worker.read_action({'action': action, **fields}), {action: result})
                factory.assert_called_once_with(session.return_value.__enter__.return_value)
                client.login.assert_called_once_with('user', 'secret')
                getattr(client, action).assert_called_once_with(*args)

    def test_invalid_input_never_opens_session(self):
        events = (None, {}, {'action': []}, {'action': 'book'}, {'action': 'cancel'},
                  {'action': 'waitlist_join'}, {'action': 'unknown'},
                  {'action': 'schedule', 'activity_id': 123, 'date': '2026-11-12'},
                  {'action': 'schedule', 'activity_id': '123', 'date': '2026-02-30'})
        with patch.object(telegram_worker.requests, 'Session') as session:
            for event in events:
                with self.subTest(event=event), self.assertRaises(ValueError):
                    telegram_worker.read_action(event)
            session.assert_not_called()

    def test_missing_credentials_never_opens_session(self):
        for env in ({}, {'SPORTRICK_USERNAME': 'user'}, {'SPORTRICK_PASSWORD': 'secret'}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch.object(telegram_worker.requests, 'Session') as session:
                with self.assertRaisesRegex(RuntimeError, 'credentials are not configured'):
                    telegram_worker.read_action({'action': 'activities'})
                session.assert_not_called()

    def test_client_errors_do_not_expose_secrets(self):
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'secret'}), patch.object(telegram_worker.requests, 'Session'), patch.object(telegram_worker, 'SportrickClient') as factory:
            factory.return_value.login.side_effect = ValueError('secret')
            with self.assertRaisesRegex(RuntimeError, '^Sportrick request or response failed$') as caught:
                telegram_worker.read_action({'action': 'activities'})
            self.assertTrue(caught.exception.__suppress_context__)


class TelegramJobTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'secret'})
        self.session = patch.object(telegram_worker.requests, 'Session')
        self.factory = patch.object(telegram_worker, 'SportrickClient')
        self.env.start()
        self.session_mock = self.session.start()
        self.client = self.factory.start().return_value
        self.addCleanup(self.env.stop)
        self.addCleanup(self.session.stop)
        self.addCleanup(self.factory.stop)
        self.store = Mock()
        self.payload = {'activity_id': '123', 'date': '2026-11-12', 'appointment_id': '456', 'booking_id': '789'}
        self.client.schedule.return_value = [{'AppointmentId': '456', 'Type': 'class', 'Notes': "Lezione al completo. Vuoi metterti in lista d'attesa?"}]
        self.client.bookings.return_value = [{'id': '789', 'isOnWaitlist': True, 'canDeleteFromWaitlist': True, 'canMoveToAttendants': True}]

    def run_action(self, action, **fields):
        self.store.claim_job.return_value = {'payload': {**self.payload, 'action': action, **fields}}
        return telegram_worker.run_job('job', self.store)

    def test_each_action_claims_then_saves_result(self):
        for action, args in (('book', ('123', '2026-11-12', '456')), ('cancel', ('789',)),
                             ('waitlist_join', ('456',)), ('waitlist_leave', ('789',)),
                             ('waitlist_confirm', ('789',))):
            with self.subTest(action=action):
                self.store.reset_mock()
                self.client.reset_mock()
                result = {'status': 'confirmed'}
                getattr(self.client, action).return_value = result
                self.assertEqual(self.run_action(action), result)
                self.assertEqual(self.store.method_calls, [unittest.mock.call.claim_job('job'), unittest.mock.call.finish_job('job', 'completed', result)])
                getattr(self.client, action).assert_called_once_with(*args)

    def test_duplicate_claim_has_no_network(self):
        self.store.claim_job.side_effect = StoreConflict('already claimed')
        with self.assertRaises(StoreConflict):
            telegram_worker.run_job('job', self.store)
        self.session_mock.assert_not_called()
        self.store.finish_job.assert_not_called()

    def test_invalid_inputs_are_saved_uncertain_without_network(self):
        for action, fields in (('book', {'activity_id': 123}), ('book', {'date': 'invalid'}),
                               ('waitlist_join', {'appointment_id': ''}), ('cancel', {'booking_id': 'x'}),
                               ('waitlist_leave', {'booking_id': None}), ('waitlist_confirm', {'booking_id': 42}),
                               ('activities', {})):
            with self.subTest(action=action, fields=fields):
                self.store.reset_mock()
                self.assertEqual(self.run_action(action, **fields), {'status': 'uncertain'})
                self.store.finish_job.assert_called_once_with('job', 'uncertain', {'status': 'uncertain'})
        self.session_mock.assert_not_called()

    def test_waitlist_preconditions_fail_closed(self):
        for action, booking in (('waitlist_leave', {'id': '789', 'isOnWaitlist': True, 'canDeleteFromWaitlist': False}),
                                ('waitlist_confirm', {'id': '789', 'isOnWaitlist': False, 'canMoveToAttendants': True})):
            with self.subTest(action=action):
                self.client.bookings.return_value = [booking]
                self.assertEqual(self.run_action(action), {'status': 'uncertain'})
                getattr(self.client, action).assert_not_called()
        for slot in ({'AppointmentId': '456', 'Type': 'service', 'Notes': "Lezione al completo. Vuoi metterti in lista d'attesa?"},
                     {'AppointmentId': '456', 'Type': 'class', 'Notes': 'Prenota'},
                     {'AppointmentId': '999', 'Type': 'class', 'Notes': "Lezione al completo. Vuoi metterti in lista d'attesa?"}):
            with self.subTest(slot=slot):
                self.client.schedule.return_value = [slot]
                self.assertEqual(self.run_action('waitlist_join'), {'status': 'uncertain'})
                self.client.waitlist_join.assert_not_called()

    def test_error_and_uncertain_are_saved_without_retry(self):
        self.client.book.side_effect = RuntimeError('secret')
        self.assertEqual(self.run_action('book'), {'status': 'uncertain'})
        self.client.book.assert_called_once()
        self.store.finish_job.assert_called_once_with('job', 'uncertain', {'status': 'uncertain'})
        self.client.book.side_effect = None
        self.client.book.return_value = {'status': 'uncertain'}
        self.store.reset_mock()
        self.run_action('book')
        self.store.finish_job.assert_called_once_with('job', 'uncertain', {'status': 'uncertain'})

    def test_finish_failure_is_not_retried_or_leaked(self):
        self.client.cancel.return_value = {'status': 'confirmed'}
        self.store.finish_job.side_effect = RuntimeError('secret')
        with self.assertRaisesRegex(RuntimeError, '^Could not persist job outcome$'):
            self.run_action('cancel')
        self.store.finish_job.assert_called_once()
        self.client.cancel.assert_called_once()


class TelegramHandlerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'BOT_TABLE_NAME': 'jobs', 'TELEGRAM_BOT_TOKEN': 'token-secret',
                                            'TELEGRAM_CHAT_ID': 'chat', 'SPORTRICK_USERNAME': 'user',
                                            'SPORTRICK_PASSWORD': 'password'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_outcomes_are_notified_only_after_persisting(self):
        for status, label in (('confirmed', 'confermato'), ('rejected', 'rifiutato'), ('uncertain', 'incerto')):
            with self.subTest(status=status), patch('boto3.client') as dynamodb, \
                    patch('lambda_function.bot_store.BotStore') as factory, \
                    patch.object(telegram_worker.requests, 'Session'), \
                    patch.object(telegram_worker, 'SportrickClient') as client, \
                    patch.object(telegram_worker.requests, 'post') as post:
                store = factory.return_value
                store.claim_job.return_value = {'payload': {'action': 'cancel', 'booking_id': '123'}}
                result = {'status': status}
                client.return_value.cancel.return_value = result
                def send(*args, **kwargs):
                    store.finish_job.assert_called_once_with('job', 'uncertain' if status == 'uncertain' else 'completed', result)
                    return Mock()
                post.side_effect = send
                self.assertEqual(telegram_worker.lambda_handler({'job_id': 'job'}, None), result)
                dynamodb.assert_called_once_with('dynamodb')
                factory.assert_called_once_with(dynamodb.return_value, 'jobs')
                self.assertEqual(post.call_args.kwargs['json'], {'chat_id': 'chat', 'text': f'Esito operazione: {label}'})

    def test_notification_failure_or_missing_configuration_does_not_change_result(self):
        for missing in (None, 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
            with self.subTest(missing=missing), patch('boto3.client'), patch('lambda_function.bot_store.BotStore'), \
                    patch.object(telegram_worker, 'run_job', return_value={'status': 'confirmed'}), \
                    patch.object(telegram_worker.requests, 'post', side_effect=RuntimeError('token-secret')) as post:
                if missing:
                    os.environ.pop(missing)
                self.assertEqual(telegram_worker.lambda_handler({'job_id': 'job'}, None), {'status': 'confirmed'})
                self.assertEqual(post.call_count, 0 if missing else 1)
                if missing:
                    os.environ[missing] = 'token-secret' if missing == 'TELEGRAM_BOT_TOKEN' else 'chat'

    def test_claim_conflict_or_failed_persistence_never_notifies(self):
        with patch('boto3.client'), patch('lambda_function.bot_store.BotStore') as factory, \
                patch.object(telegram_worker.requests, 'post') as post:
            factory.return_value.claim_job.side_effect = StoreConflict('running')
            self.assertEqual(telegram_worker.lambda_handler({'job_id': 'job'}, None), {'status': 'already_claimed'})
            factory.return_value.finish_job.assert_not_called()
            post.assert_not_called()
        with patch('boto3.client'), patch('lambda_function.bot_store.BotStore'), \
                patch.object(telegram_worker, 'run_job', side_effect=RuntimeError('Could not persist job outcome')), \
                patch.object(telegram_worker.requests, 'post') as post:
            with self.assertRaises(RuntimeError):
                telegram_worker.lambda_handler({'job_id': 'job'}, None)
            post.assert_not_called()


class TelegramReadHandlerTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'token-secret', 'TELEGRAM_CHAT_ID': '123'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_reads_and_sends_only_to_configured_chat(self):
        for request in ({'action': 'activities'}, {'action': 'bookings'},
                        {'action': 'schedule', 'activity_id': '123', 'date': '2026-11-12'}):
            with self.subTest(request=request), patch.object(telegram_worker, 'read_action', return_value={'data': ['lezione']}) as read, \
                    patch.object(telegram_worker.requests, 'post') as post, patch('boto3.client') as dynamodb:
                post.return_value.json.return_value = {'ok': True}
                self.assertEqual(telegram_worker.lambda_handler({'read': request, 'chat_id': '123'}, None), {'status': 'completed'})
                read.assert_called_once_with(request)
                self.assertEqual(post.call_args.kwargs['json']['chat_id'], '123')
                self.assertIn('lezione', post.call_args.kwargs['json']['text'])
                dynamodb.assert_not_called()

    def test_long_results_split_into_bounded_messages(self):
        with patch.object(telegram_worker, 'read_action', return_value={'data': 'x' * 9000}), \
                patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'chat_id': '123'}, None), {'status': 'completed'})
            self.assertEqual(post.call_count, 3)
            self.assertTrue(all(len(call.kwargs['json']['text']) <= 4000 for call in post.call_args_list))

    def test_untrusted_chat_and_missing_configuration_do_nothing(self):
        with patch.object(telegram_worker, 'read_action') as read, patch.object(telegram_worker.requests, 'post') as post:
            for chat in ('other', 123, None):
                self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'chat_id': chat}, None), {'status': 'unauthorized'})
            os.environ.pop('TELEGRAM_BOT_TOKEN')
            self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'chat_id': '123'}, None), {'status': 'unauthorized'})
            read.assert_not_called()
            post.assert_not_called()

    def test_invalid_actions_and_errors_send_generic_message(self):
        for request in ({'action': 'book'}, {'action': 'cancel'}, None, {'action': 'schedule', 'activity_id': 'x'}):
            with self.subTest(request=request), patch.object(telegram_worker.requests, 'Session') as session, \
                    patch.object(telegram_worker.requests, 'post') as post:
                post.return_value.json.return_value = {'ok': True}
                self.assertEqual(telegram_worker.lambda_handler({'read': request, 'chat_id': '123'}, None), {'status': 'error'})
                self.assertEqual(post.call_args.kwargs['json']['text'], 'Impossibile recuperare i dati richiesti.')
                session.assert_not_called()
        with patch.object(telegram_worker, 'read_action', side_effect=RuntimeError('token-secret')), \
                patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'chat_id': '123'}, None), {'status': 'error'})
            self.assertNotIn('token-secret', post.call_args.kwargs['json']['text'])

    def test_telegram_errors_are_reported_without_retry(self):
        for response in ({'ok': False}, {'ok': True}):
            with self.subTest(response=response), patch.object(telegram_worker, 'read_action', return_value={'data': 'x' * 9000}), \
                    patch.object(telegram_worker.requests, 'post') as post:
                post.return_value.json.return_value = response
                if response['ok']:
                    post.return_value.raise_for_status.side_effect = RuntimeError('token-secret')
                self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'chat_id': '123'}, None), {'status': 'delivery_failed'})
                post.assert_called_once()

    def test_ui_renders_and_only_last_chunk_has_keyboard(self):
        ui = {'kind': 'activities'}
        result = {'activities': []}
        keyboard = [[{'text': 'Apri', 'callback_data': 'open'}]]
        with patch.object(telegram_worker, 'read_action', return_value=result) as read, \
                patch('lambda_function.telegram_bot.render_ui', return_value=('x' * 8001, keyboard)) as render, \
                patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            event = {'read': {'action': 'activities'}, 'chat_id': '123', 'ui': ui}
            self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'completed'})
            read.assert_called_once_with(event['read'])
            render.assert_called_once_with(ui, result, '123')
            messages = [call.kwargs['json'] for call in post.call_args_list]
            self.assertEqual([len(message['text']) for message in messages], [4000, 4000, 1])
            self.assertNotIn('reply_markup', messages[0])
            self.assertNotIn('reply_markup', messages[1])
            self.assertEqual(messages[2]['reply_markup'], {'inline_keyboard': keyboard})

    def test_ui_untrusted_descriptors_and_missing_table_fail_closed(self):
        cases = [({'kind': 'slot', 'activity_id': '2', 'date': '2026-11-12'}, {'action': 'schedule', 'activity_id': '1', 'date': '2026-11-12'}),
                 ({'kind': 'slot', 'activity_id': '1', 'date': '2026-11-12'}, {'action': 'schedule', 'activity_id': '1', 'date': '2026-11-12'}),
                 ({'kind': 'booking'}, {'action': 'bookings'}),
                 ({'kind': 'other'}, {'action': 'activities'}), ([], {'action': 'activities'})]
        with patch.object(telegram_worker, 'read_action', return_value={}), \
                patch('lambda_function.telegram_bot.render_ui') as render, patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            for ui, request in cases:
                with self.subTest(ui=ui):
                    self.assertEqual(telegram_worker.lambda_handler({'read': request, 'chat_id': '123', 'ui': ui}, None), {'status': 'error'})
                    self.assertEqual(post.call_args.kwargs['json']['text'], 'Impossibile recuperare i dati richiesti.')
                    self.assertNotIn('reply_markup', post.call_args.kwargs['json'])
            render.assert_not_called()

    def test_ui_confirmation_rendering_requires_table_and_handles_errors(self):
        os.environ['BOT_TABLE_NAME'] = 'jobs'
        ui = {'kind': 'booking', 'action': 'cancel', 'booking_id': '1'}
        event = {'read': {'action': 'bookings'}, 'chat_id': '123', 'ui': ui}
        with patch.object(telegram_worker, 'read_action', return_value={'bookings': []}), \
                patch('lambda_function.telegram_bot.render_ui', side_effect=RuntimeError('token-secret')) as render, \
                patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'error'})
            render.assert_called_once_with(ui, {'bookings': []}, '123')
            self.assertEqual(post.call_args.kwargs['json']['text'], 'Impossibile recuperare i dati richiesti.')

    def test_unauthorized_ui_never_reads_or_renders(self):
        with patch.object(telegram_worker, 'read_action') as read, patch('lambda_function.telegram_bot.render_ui') as render:
            self.assertEqual(telegram_worker.lambda_handler({'read': {'action': 'activities'}, 'ui': {'kind': 'activities'}, 'chat_id': 'other'}, None), {'status': 'unauthorized'})
            read.assert_not_called()
            render.assert_not_called()
