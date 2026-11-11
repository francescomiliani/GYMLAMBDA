import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import requests

from lambda_function import app


ACTIVITY_ID = '901234567890123456'
APPOINTMENT_ID = '901234567890123457'
BOOKING_ID = '901234567890123458'
LOGIN_FORM = '<input type="hidden" name="__RequestVerificationToken" value="form-token">'
CATALOG = f'''<a class="list-group-item seleziona-attivita" data-idattivita="{ACTIVITY_ID}" data-flags="Classes">
    <h3 class="list-group-item-heading">PILATES</h3><p class="list-group-item-text">Giovedì</p></a>'''
CONFIRM = f'<input type="hidden" id="appointmentID" value="{APPOINTMENT_ID}"><button id="btnConfirmAppointmentBooking"></button>'
SLOT = {'AppointmentId': int(APPOINTMENT_ID), 'TimeStart': '18:30', 'TimeDuration': '60 min',
        'Notes': 'Prenota', 'Status': 'Available', 'Type': 'class', 'EventName': 'PILATES',
        'StartDateTime': '2026-11-19T18:30:00'}
BOOKING = {'id': int(BOOKING_ID), 'DateStart': '19/11/2026', 'TimeStart': '18:30',
           'TimeDuration': '60 min', 'description': 'PILATES', 'isOnWaitlist': False,
           'isService': False, 'canDelete': True}
ENV = {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'password',
       'SNS_TOPIC_ARN': 'arn:aws:sns:eu-north-1:123456789012:test',
       'TELEGRAM_BOT_TOKEN': 'bot-token', 'TELEGRAM_CHAT_ID': '123'}


def http_response(body, *, status=200, url='https://ecomm.sportrick.com/replywellnessTO/Booking', location=None):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = (json.dumps(body) if isinstance(body, (dict, list)) else body).encode()
    if location:
        response.headers['Location'] = location
    return response


def login_response():
    return http_response('', status=302, location='/replywellnessTO')


class LambdaWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.session.__enter__.return_value = self.session
        self.sns = MagicMock()
        self.telegram = http_response({'ok': True})
        self.env_patch = patch.dict(os.environ, ENV, clear=True)
        self.session_patch = patch.object(app.requests, 'Session', return_value=self.session)
        self.telegram_patch = patch.object(app.requests, 'post', return_value=self.telegram)
        self.sns_patch = patch.dict(sys.modules, {'boto3': self.sns})
        self.network_patch = patch('socket.socket.connect', side_effect=AssertionError('unexpected network access'))
        for item in (self.env_patch, self.session_patch, self.telegram_patch, self.sns_patch, self.network_patch):
            item.start()
            self.addCleanup(item.stop)

    def booking_responses(self, outcome):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(CATALOG), http_response(CONFIRM)]
        self.session.post.side_effect = [login_response(), http_response({'status': 'OK', 'data': [SLOT]}), outcome]

    def test_booking_notifies_both_channels_with_course_and_time(self):
        self.booking_responses(http_response({'message': 'Prenotazione confermata con successo.'}))
        result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                     'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual((result['course'], result['date'], result['time']), ('PILATES', '2026-11-19', '18:30'))
        self.assertEqual(result['notifications'], {'sns': 'accepted', 'telegram': 'accepted'})
        sent = self.sns.client.return_value.publish.call_args.kwargs
        self.assertIn('PILATES', sent['Message'])
        self.assertIn('2026-11-19 18:30', sent['Message'])
        telegram = app.requests.post.call_args.kwargs['json']['text']
        self.assertIn('PILATES', telegram)
        self.assertIn('18:30', telegram)
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'appointmentID': APPOINTMENT_ID})

    def test_cancellation_resolves_booking_and_notifies_both_channels(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'OK', 'data': [BOOKING]})]
        self.session.post.side_effect = [login_response(), http_response({'status': 'OK', 'message': 'Eliminata'})]
        result = app.lambda_handler({'action': 'cancel', 'booking_id': BOOKING_ID}, None)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual((result['course'], result['date'], result['time']), ('PILATES', '19/11/2026', '18:30'))
        self.assertEqual(result['notifications'], {'sns': 'accepted', 'telegram': 'accepted'})
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'id': BOOKING_ID})
        sent = self.sns.client.return_value.publish.call_args.kwargs
        self.assertIn('PILATES', sent['Message'])
        self.assertIn('19/11/2026 18:30', sent['Message'])
        self.assertIn('cancellazione', app.requests.post.call_args.kwargs['json']['text'])

    def test_timeout_after_booking_is_uncertain_and_never_retried_or_notified(self):
        self.booking_responses(requests.Timeout('request may have reached server'))
        result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                     'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.session.post.call_count, 3)  # login, schedule, exactly one booking
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_unrecognized_booking_response_is_uncertain_without_notification(self):
        self.booking_responses(http_response('temporarily unavailable'))
        result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                     'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.session.post.call_count, 3)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_portal_rejects_booking_without_sending_a_confirmation(self):
        self.booking_responses(http_response({'status': 'ERROR', 'message': 'Lezione al completo'}))
        result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                     'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'rejected')
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_unexpected_json_booking_results_are_uncertain(self):
        for payload in ([], {'status': 'PENDING', 'message': 'In elaborazione'}):
            with self.subTest(payload=payload):
                self.session.reset_mock()
                self.booking_responses(http_response(payload))
                result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                             'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
                self.assertEqual(result['status'], 'uncertain')
                self.assertEqual(self.session.post.call_count, 3)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_rejected_cancellation_never_reports_success_or_sends_notifications(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'OK', 'data': [BOOKING]})]
        self.session.post.side_effect = [login_response(), http_response({'status': 'ERROR', 'error_message': 'Non consentito'})]
        result = app.lambda_handler({'action': 'cancel', 'booking_id': BOOKING_ID}, None)
        self.assertEqual(result['status'], 'rejected')
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_cancellation_timeout_is_uncertain_and_not_retried(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'OK', 'data': [BOOKING]})]
        self.session.post.side_effect = [login_response(), requests.Timeout('response lost')]
        result = app.lambda_handler({'action': 'cancel', 'booking_id': BOOKING_ID}, None)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.session.post.call_count, 2)  # login and one cancellation
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_cancellation_with_unexpected_response_remains_uncertain(self):
        for payload in ([], {'status': 'PENDING'}):
            with self.subTest(payload=payload):
                self.session.reset_mock()
                self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'OK', 'data': [BOOKING]})]
                self.session.post.side_effect = [login_response(), http_response(payload)]
                result = app.lambda_handler({'action': 'cancel', 'booking_id': BOOKING_ID}, None)
                self.assertEqual(result['status'], 'uncertain')
                self.assertEqual(self.session.post.call_count, 2)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_cancellation_refuses_unknown_waitlisted_and_non_cancellable_bookings(self):
        cases = ([BOOKING], [{**BOOKING, 'isOnWaitlist': True}],
                 [{**BOOKING, 'canDelete': False}], [{**BOOKING, 'description': ''}])
        ids = ('999', BOOKING_ID, BOOKING_ID, BOOKING_ID)
        for records, booking_id in zip(cases, ids):
            with self.subTest(booking_id=booking_id, records=records):
                self.session.reset_mock()
                self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'OK', 'data': records})]
                self.session.post.side_effect = [login_response()]
                with self.assertRaises(ValueError):
                    app.lambda_handler({'action': 'cancel', 'booking_id': booking_id}, None)
                self.assertEqual(self.session.post.call_count, 1)  # only login
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_waitlist_join_uses_confirmation_id_without_sending_booking_notice(self):
        confirmation = '<input type="hidden" id="idImpegno" value="777"><button id="btnAggiungiListaAttesa"></button>'
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(confirmation)]
        self.session.post.side_effect = [login_response(), http_response({'status': 'OK', 'message': 'In lista'})]
        result = app.lambda_handler({'action': 'waitlist_join', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(self.session.get.call_args.kwargs['params'], {'appointmentID': APPOINTMENT_ID})
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'appointmentId': '777'})
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_waitlist_join_stops_if_confirmation_has_no_id(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response('<button id="btnAggiungiListaAttesa"></button>')]
        self.session.post.side_effect = [login_response()]
        with self.assertRaises(ValueError):
            app.lambda_handler({'action': 'waitlist_join', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(self.session.post.call_count, 1)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_waitlist_join_timeout_is_uncertain_without_retry(self):
        confirmation = '<input type="hidden" id="idImpegno" value="777"><button id="btnAggiungiListaAttesa"></button>'
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(confirmation)]
        self.session.post.side_effect = [login_response(), requests.Timeout('response lost')]
        result = app.lambda_handler({'action': 'waitlist_join', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(self.session.post.call_count, 2)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_waitlist_leave_and_confirm_use_booking_id_without_booking_notice(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(LOGIN_FORM)]
        self.session.post.side_effect = [login_response(), http_response({'status': 'OK'}),
                                         login_response(), http_response({'status': 'OK'})]
        for action in ('waitlist_leave', 'waitlist_confirm'):
            result = app.lambda_handler({'action': action, 'booking_id': BOOKING_ID}, None)
            self.assertEqual(result['status'], 'confirmed')
        calls = self.session.post.call_args_list
        self.assertTrue(calls[1].args[0].endswith('/Booking/RemoveFromWaitlist'))
        self.assertEqual(calls[1].kwargs['data'], {'bookingId': BOOKING_ID})
        self.assertTrue(calls[3].args[0].endswith('/Booking/MoveToAttendants'))
        self.assertEqual(calls[3].kwargs['data'], {'appointmentId': BOOKING_ID})
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_waitlist_leave_and_confirm_report_rejection_or_timeout(self):
        outcomes = ((http_response({'status': 'ERROR', 'error_message': 'Non consentito'}), 'rejected'),
                    (requests.Timeout('response lost'), 'uncertain'))
        for action in ('waitlist_leave', 'waitlist_confirm'):
            for outcome, expected in outcomes:
                with self.subTest(action=action, expected=expected):
                    self.session.reset_mock()
                    self.session.get.side_effect = [http_response(LOGIN_FORM)]
                    self.session.post.side_effect = [login_response(), outcome]
                    result = app.lambda_handler({'action': action, 'booking_id': BOOKING_ID}, None)
                    self.assertEqual(result['status'], expected)
                    self.assertEqual(self.session.post.call_count, 2)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_missing_login_token_stops_before_credentials_are_sent(self):
        self.session.get.return_value = http_response('<html><title>Login unavailable</title></html>')
        with self.assertRaises(RuntimeError):
            app.lambda_handler({'action': 'activities'}, None)
        self.session.post.assert_not_called()

    def test_expired_session_is_not_mistaken_for_an_empty_catalog(self):
        expired = http_response('<html>Login</html>', url='https://ecomm.sportrick.com/replywellnessTO/Account/Login')
        self.session.get.side_effect = [http_response(LOGIN_FORM), expired]
        self.session.post.side_effect = [login_response()]
        with self.assertRaises(RuntimeError):
            app.lambda_handler({'action': 'activities'}, None)
        self.assertEqual(self.session.post.call_count, 1)

    def test_incomplete_catalog_activity_is_rejected(self):
        incomplete = '<a class="seleziona-attivita" data-flags="Classes"><h3 class="list-group-item-heading">PILATES</h3></a>'
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(incomplete)]
        self.session.post.side_effect = [login_response()]
        with self.assertRaises(ValueError):
            app.lambda_handler({'action': 'activities'}, None)
        self.assertEqual(self.session.post.call_count, 1)

    def test_calendar_failure_is_not_mistaken_for_no_classes(self):
        for payload in ({'status': 'ERROR', 'data': []}, {'status': 'OK', 'data': None}):
            with self.subTest(payload=payload):
                self.session.reset_mock()
                self.session.get.side_effect = [http_response(LOGIN_FORM), http_response(CATALOG)]
                self.session.post.side_effect = [login_response(), http_response(payload)]
                with self.assertRaises(RuntimeError):
                    app.lambda_handler({'action': 'schedule', 'activity_id': ACTIVITY_ID, 'date': '2026-11-19'}, None)
                self.assertEqual(self.session.post.call_count, 2)
        self.sns.client.assert_not_called()
        app.requests.post.assert_not_called()

    def test_bookings_failure_is_not_mistaken_for_no_reservations(self):
        self.session.get.side_effect = [http_response(LOGIN_FORM), http_response({'status': 'ERROR', 'data': []})]
        self.session.post.side_effect = [login_response()]
        with self.assertRaises(RuntimeError):
            app.lambda_handler({'action': 'bookings'}, None)
        self.assertEqual(self.session.post.call_count, 1)

    def test_explicit_action_without_credentials_never_contacts_portal(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(RuntimeError):
            app.lambda_handler({'action': 'activities'}, None)
        app.requests.Session.assert_not_called()

    def test_request_error_does_not_expose_credentials(self):
        self.session.get.return_value = http_response(LOGIN_FORM)
        self.session.post.side_effect = requests.ConnectionError('password bot-token')
        with self.assertRaises(RuntimeError) as error:
            app.lambda_handler({'action': 'activities'}, None)
        self.assertNotIn('password', str(error.exception))
        self.assertNotIn('bot-token', str(error.exception))

    def test_failed_telegram_api_does_not_hide_confirmed_booking(self):
        self.telegram._content = b'{"ok":false,"description":"blocked"}'
        self.booking_responses(http_response({'message': 'Prenotazione confermata con successo.'}))
        result = app.lambda_handler({'action': 'book', 'activity_id': ACTIVITY_ID,
                                     'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID}, None)
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['notifications'], {'sns': 'accepted', 'telegram': 'failed'})

    def test_missing_or_invalid_ids_never_open_a_session(self):
        self.session_patch.stop()
        with patch.object(app.requests, 'Session') as factory:
            for value in (None, 123, '', '１２３', '-123', '123.0'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    app.lambda_handler({'action': 'cancel', 'booking_id': value}, None)
            factory.assert_not_called()

    def test_invalid_dates_never_open_a_session(self):
        self.session_patch.stop()
        with patch.object(app.requests, 'Session') as factory:
            for date in ('2026-02-30', '20261119', '2026-1-9', '', None):
                with self.subTest(date=date), self.assertRaises(ValueError):
                    app.lambda_handler({'action': 'schedule', 'activity_id': ACTIVITY_ID, 'date': date}, None)
            factory.assert_not_called()

    def test_missing_required_action_parameters_never_open_a_session(self):
        self.session_patch.stop()
        events = ({'action': 'schedule'}, {'action': 'schedule', 'activity_id': ACTIVITY_ID},
                  {'action': 'schedule', 'date': '2026-11-19'}, {'action': 'book'},
                  {'action': 'book', 'activity_id': ACTIVITY_ID, 'date': '2026-11-19'},
                  {'action': 'book', 'activity_id': ACTIVITY_ID, 'appointment_id': APPOINTMENT_ID},
                  {'action': 'book', 'date': '2026-11-19', 'appointment_id': APPOINTMENT_ID},
                  {'action': 'cancel'}, {'action': 'waitlist_join'},
                  {'action': 'waitlist_leave'}, {'action': 'waitlist_confirm'})
        with patch.object(app.requests, 'Session') as factory:
            for event in events:
                with self.subTest(event=event), self.assertRaises(ValueError):
                    app.lambda_handler(event, None)
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
