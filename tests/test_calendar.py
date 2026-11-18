import datetime
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from lambda_function import app, calendar_ics, google_calendar, telegram_worker

ROME = ZoneInfo('Europe/Rome')
NOW = datetime.datetime(2026, 11, 18, 12, 0, tzinfo=ROME)
FUTURE = {'id': '42', 'description': 'SALA, ATTREZZI', 'DateStart': '20/11/2026',
          'TimeStart': '13:30', 'TimeDuration': '90 min'}
PAST = {'id': '1', 'description': 'IERI', 'date': '2026-11-17', 'time': '07:00'}
WAITLIST = {'id': '2', 'description': 'LISTA', 'DateStart': '20/11/2026', 'TimeStart': '13:30', 'isOnWaitlist': True}
SERVICE = {'id': '3', 'description': 'VISITA', 'DateStart': '20/11/2026', 'TimeStart': '13:30', 'isService': True}


class Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class Http:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append(('post', url, json or data))
        if 'oauth2' in url:
            return Response(200, {'access_token': 'ya'})
        if self.body is not None:
            return Response(self.status, self.body)
        uid = (json or {}).get('iCalUID', 'x')
        return Response(200, {'id': 'evt-' + uid})

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(('get', url, params))
        return Response(200, {'items': [{'id': 'found'}]})

    def delete(self, url, headers=None, timeout=None):
        self.calls.append(('delete', url))
        return Response(self.status, {})


class CalendarIcsTests(unittest.TestCase):
    def test_open_classes_become_one_utc_event(self):
        text = calendar_ics.render([FUTURE, PAST, WAITLIST, SERVICE, {'description': 'vuoto'}], NOW)
        self.assertEqual(text.count('BEGIN:VEVENT'), 1)
        self.assertIn('UID:gymbooking-42@gymbooking', text)
        self.assertIn('DTSTART:20261120T123000Z', text)
        self.assertIn('DTEND:20261120T140000Z', text)
        self.assertIn('SUMMARY:SALA\\, ATTREZZI', text)
        self.assertIn('\r\n', text)
        self.assertIsNone(calendar_ics.render([PAST, WAITLIST, SERVICE], NOW))

    def test_uid_without_id_uses_date_and_time(self):
        self.assertEqual(calendar_ics.uid({'date': '2026-11-20', 'time': '07:00'}),
                         'gymbooking-2026-11-20-0700@gymbooking')


class GoogleCalendarTests(unittest.TestCase):
    def test_success_writes_once_and_does_not_return_a_file(self):
        http = Http()
        store = {}

        class Memory:
            def get_state(self, key):
                return store.get(key)

            def put_state(self, key, value, purge_at=None):
                store[key] = value

        with patch.object(google_calendar, 'load_credentials', return_value={'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r'}):
            first = google_calendar.publish(FUTURE, Memory(), http, NOW)
            second = google_calendar.publish(FUTURE, Memory(), http, NOW)
        self.assertEqual(first['channel'], 'google')
        self.assertIsNone(first['ics'])
        self.assertEqual(store['gcal#42']['event_id'], 'evt-gymbooking-42@gymbooking')
        self.assertEqual(second['event_id'], store['gcal#42']['event_id'])
        self.assertEqual(len([call for call in http.calls if call[0] == 'post' and str(call[1]).endswith('/import')]), 1)
        body = next(call[2] for call in http.calls if str(call[1]).endswith('/import'))
        self.assertEqual(body['reminders']['overrides'], [{'method': 'popup', 'minutes': 720}, {'method': 'popup', 'minutes': 60}])
        self.assertEqual(body['start']['timeZone'], 'Europe/Rome')

    def test_missing_secret_or_rejected_call_returns_ics_and_does_not_raise(self):
        with patch.object(google_calendar, 'load_credentials', return_value=None):
            missing = google_calendar.publish(FUTURE, now=NOW)
        self.assertEqual(missing['channel'], 'ics')
        self.assertIn('BEGIN:VEVENT', missing['ics'])
        http = Http(status=500, body={'error': 'no'})
        with patch.object(google_calendar, 'load_credentials', return_value={'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r'}):
            failed = google_calendar.publish(FUTURE, http=http, now=NOW)
        self.assertEqual(failed['reason'], 'failed')
        self.assertIn('BEGIN:VEVENT', failed['ics'])

    def test_cancel_deletes_the_stored_event(self):
        http = Http()

        class Memory:
            def get_state(self, key):
                return {'event_id': 'evt-1'}

            def put_state(self, key, value, purge_at=None):
                self.cleared = value

        store = Memory()
        with patch.object(google_calendar, 'load_credentials', return_value={'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r'}):
            self.assertEqual(google_calendar.remove('42', store, http), 'deleted')
        self.assertTrue(any(call[0] == 'delete' and call[1].endswith('/evt-1') for call in http.calls))
        self.assertEqual(store.cleared, {})
        http.status = 404
        with patch.object(google_calendar, 'load_credentials', return_value={'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r'}):
            self.assertEqual(google_calendar.remove('42', store, http), 'deleted')

    def test_sync_skips_a_stored_id_and_files_only_the_failure(self):
        other = {'id': '7', 'description': 'YOGA', 'DateStart': '20/11/2026', 'TimeStart': '18:15'}
        saved = {'gcal#42': {'event_id': 'already'}}

        class Memory:
            def get_state(self, key):
                return saved.get(key)

            def put_state(self, key, value, purge_at=None):
                saved[key] = value

        http = Http(status=500, body={})
        with patch.object(google_calendar, 'load_credentials', return_value={'client_id': 'c', 'client_secret': 's', 'refresh_token': 'r'}):
            outcome = google_calendar.sync([FUTURE, other, WAITLIST], Memory(), http, NOW)
        self.assertEqual(outcome['count'], 1)
        self.assertIn('YOGA', outcome['ics'])
        self.assertNotIn('SALA', outcome['ics'])
        self.assertEqual(google_calendar.sync([], now=NOW)['channel'], 'empty')


class NotifyCalendarTests(unittest.TestCase):
    def test_file_goes_out_before_the_text_and_a_document_error_keeps_the_booking(self):
        calls = []

        def post(url, json=None, data=None, files=None, timeout=None):
            calls.append(url)
            if 'sendDocument' in url and len(calls) == 1:
                raise app.requests.ConnectionError('secret-token')
            reply = unittest.mock.Mock()
            reply.json.return_value = {'ok': True}
            reply.raise_for_status.return_value = None
            return reply

        env = {'TELEGRAM_BOT_TOKEN': 'secret-token', 'TELEGRAM_CHAT_ID': '123'}
        item = {'course': 'SALA ATTREZZI', 'date': '2099-01-02', 'time': '07:00', 'appointment_id': '42'}
        with patch.dict(os.environ, env, clear=True), patch.object(app.requests, 'post', side_effect=post):
            result = app.notify(dict(item), 'book')
        self.assertEqual(result['notifications'], {'telegram': 'accepted'})
        self.assertEqual(result['calendar'], 'ics')
        self.assertIn('sendDocument', calls[0])
        self.assertIn('sendMessage', calls[1])
        reply = unittest.mock.Mock()
        reply.json.return_value = {'ok': True}
        reply.raise_for_status.return_value = None
        with patch.dict(os.environ, env, clear=True), patch.object(google_calendar, 'remove', return_value='failed'), \
                patch.object(app.requests, 'post', return_value=reply) as send:
            app.notify({'course': 'SALA', 'date': '20/11/2026', 'time': '13:30', 'booking_id': '42'}, 'cancel')
        text = send.call_args.kwargs['json']['text']
        self.assertIn('cancellazione', text)
        self.assertIn('Google Calendar', text)


class TelegramCalendarTests(unittest.TestCase):
    def test_worker_sends_the_file_only_for_classes_google_did_not_take(self):
        os.environ['TELEGRAM_BOT_TOKEN'] = 'token'
        os.environ['TELEGRAM_CHAT_ID'] = '123'
        self.addCleanup(os.environ.pop, 'TELEGRAM_BOT_TOKEN', None)
        self.addCleanup(os.environ.pop, 'TELEGRAM_CHAT_ID', None)
        event = {'read': {'action': 'bookings'}, 'chat_id': '123', 'ui': {'kind': 'calendar'}}
        with patch.object(telegram_worker, 'read_action', return_value={'bookings': []}), \
                patch.object(telegram_worker.requests, 'post') as post:
            post.return_value.json.return_value = {'ok': True}
            post.return_value.raise_for_status.return_value = None
            self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'completed'})
            self.assertIn('Nessun impegno', post.call_args.kwargs['json']['text'])
            self.assertEqual(post.call_count, 1)
        later = {'id': '42', 'description': 'SALA', 'DateStart': '02/01/2099', 'TimeStart': '07:00'}
        with patch.object(telegram_worker, 'read_action', return_value={'bookings': [later]}), \
                patch.object(telegram_worker.requests, 'post') as post, \
                patch('lambda_function.telegram_bot._send_document') as document:
            post.return_value.json.return_value = {'ok': True}
            post.return_value.raise_for_status.return_value = None
            self.assertEqual(telegram_worker.lambda_handler(event, None), {'status': 'completed'})
            document.assert_called_once()
            self.assertIn('non è configurato', post.call_args.kwargs['json']['text'])
