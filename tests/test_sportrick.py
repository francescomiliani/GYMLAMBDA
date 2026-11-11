import unittest
import sys
from unittest.mock import MagicMock, patch

from lambda_function import app


CATALOG = '''<a class="list-group-item seleziona-attivita" data-idattivita="260634532724900628" data-flags="Classes, Services">
  <h3 class="list-group-item-heading">VISITA MEDICA</h3>
  <p class="list-group-item-text"></p><p class="list-group-item-text">Martedì</p></a>'''


def response(*, payload=None, text='', status=200, location=''):
    r = MagicMock()
    r.status_code = status
    r.headers = {'Location': location}
    r.text = text
    r.json.return_value = payload
    return r


class SportrickClientTests(unittest.TestCase):
    def client(self):
        self.session = MagicMock()
        return app.SportrickClient(self.session)

    def test_catalog_preserves_long_ids_and_flags(self):
        client = self.client()
        self.session.get.return_value = response(text=CATALOG)
        self.assertEqual(client.activities(), [{'id': '260634532724900628', 'name': 'VISITA MEDICA', 'description': 'Martedì', 'flags': 'Classes, Services'}])

    def test_empty_catalog_is_distinct_from_unexpected_html(self):
        client = self.client()
        self.session.get.return_value = response(text='<html><title>ChooseActivity - Sportrick SocialBooking</title></html>')
        self.assertEqual(client.activities(), [])
        self.session.get.return_value = response(text='<html><title>Server error</title></html>')
        with self.assertRaises(RuntimeError):
            client.activities()

    def test_schedule_uses_catalog_flags_and_keeps_long_ids(self):
        client = self.client()
        self.session.get.return_value = response(text=CATALOG)
        self.session.post.return_value = response(payload={'status': 'OK', 'data': [{'AppointmentId': 260634532724900629, 'TimeStart': '07:00', 'Notes': 'Prenota', 'Type': 'class'}]})
        result = client.schedule('260634532724900628', '2026-11-17')
        self.assertEqual(result[0]['AppointmentId'], '260634532724900629')
        self.assertEqual(self.session.post.call_args.kwargs['data']['activityFlags'], 'Classes, Services')

    def test_schedule_rejects_unknown_activity_without_post(self):
        client = self.client()
        self.session.get.return_value = response(text=CATALOG)
        with self.assertRaises(ValueError):
            client.schedule('123', '2026-11-17')
        self.session.post.assert_not_called()

    def test_bookings_only_returns_expected_fields_and_string_id(self):
        client = self.client()
        self.session.get.return_value = response(payload={'status': 'OK', 'data': [{'id': 260634532724900629, 'DateStart': '17/11/2026', 'TimeStart': '07:00', 'description': 'PILATES', 'isOnWaitlist': True, 'secret': 'omit'}]})
        items = client.bookings()
        self.assertEqual(items[0]['id'], '260634532724900629')
        self.assertTrue(items[0]['isOnWaitlist'])
        self.assertNotIn('secret', items[0])

    def test_cancel_and_waitlist_use_observed_payloads(self):
        client = self.client()
        client.bookings = MagicMock(return_value=[{'id': '123', 'description': 'YOGA', 'DateStart': '17/11/2026', 'TimeStart': '18:30', 'canDelete': True, 'isOnWaitlist': False}])
        self.session.post.return_value = response(payload={'status': 'OK', 'message': 'OK'})
        cancelled = client.cancel('123')
        self.assertEqual(cancelled['status'], 'confirmed')
        self.assertEqual((cancelled['course'], cancelled['date'], cancelled['time']), ('YOGA', '17/11/2026', '18:30'))
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'id': '123'})
        self.assertEqual(client.waitlist_leave('456')['status'], 'confirmed')
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'bookingId': '456'})

    def test_waitlist_confirmation_uses_booking_id_not_slot_id(self):
        client = self.client()
        self.session.post.return_value = response(payload={'status': 'OK'})
        self.assertEqual(client.waitlist_confirm('456')['status'], 'confirmed')
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'appointmentId': '456'})
        session = MagicMock()
        with patch.dict('os.environ', {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'pass'}), \
             patch.object(app.requests, 'Session') as factory, patch.object(app, 'SportrickClient') as client_class:
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({'action': 'waitlist_confirm', 'booking_id': '456', 'appointment_id': '123'}, None)
        client_class.return_value.waitlist_confirm.assert_called_once_with('456')

    def test_mutation_timeout_is_uncertain_and_not_retried(self):
        import requests
        client = self.client()
        client.bookings = MagicMock(return_value=[{'id': '123', 'description': 'YOGA', 'DateStart': '17/11/2026', 'TimeStart': '18:30', 'canDelete': True, 'isOnWaitlist': False}])
        self.session.post.side_effect = requests.Timeout()
        self.assertEqual(client.cancel('123')['status'], 'uncertain')
        self.assertEqual(self.session.post.call_count, 1)

    def test_login_requires_successful_redirect(self):
        client = self.client()
        self.session.get.return_value = response(text='<input type="hidden" name="__RequestVerificationToken" value="token">')
        self.session.post.return_value = response(status=200, text='Login')
        with self.assertRaises(RuntimeError):
            client.login('user', 'pass')

    def test_login_submits_verification_token_from_login_page(self):
        client = self.client()
        self.session.get.return_value = response(text='<input type="hidden" name="__RequestVerificationToken" value="token">')
        self.session.post.return_value = response(status=302, location='/replywellnessTO')
        client.login('user', 'pass')
        self.assertEqual(self.session.post.call_args.kwargs['data']['__RequestVerificationToken'], 'token')

    def test_legacy_login_uses_current_login_form(self):
        session = MagicMock()
        session.get.return_value = response(text='<input type="hidden" name="__RequestVerificationToken" value="token">')
        session.post.return_value = response(status=302, location='/replywellnessTO')
        app.login(session, {'UserName': 'user', 'Password': 'pass'})
        self.assertEqual(session.post.call_args.kwargs['data']['__RequestVerificationToken'], 'token')

    def test_join_waitlist_uses_id_from_confirmation_form(self):
        client = self.client()
        self.session.get.return_value = response(text='<input type="hidden" id="idImpegno" value="456"><button id="btnAggiungiListaAttesa"></button>')
        self.session.post.return_value = response(payload={'status': 'OK'})
        self.assertEqual(client.waitlist_join('123')['status'], 'confirmed')
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'appointmentId': '456'})

    def test_join_waitlist_does_not_post_without_confirmation(self):
        client = self.client()
        self.session.get.return_value = response(text='<input type="hidden" id="idImpegno" value="456">')
        with self.assertRaises(ValueError):
            client.waitlist_join('123')
        self.session.post.assert_not_called()

    def test_book_rejects_unavailable_and_service_slots(self):
        client = self.client()
        for slot in ({'AppointmentId': '123', 'Type': 'service', 'Notes': 'Prenota', 'Status': 'Available'},
                     {'AppointmentId': '123', 'Type': 'class', 'Notes': 'Prenota', 'Status': 'Unavailable'}):
            client.schedule = MagicMock(return_value=[slot])
            with self.assertRaises(ValueError):
                client.book('1', '2026-11-17', '123')
        self.session.post.assert_not_called()

    def test_book_posts_selected_class_and_reports_confirmation(self):
        client = self.client()
        client.schedule = MagicMock(return_value=[{'AppointmentId': '123', 'Type': 'class', 'Notes': 'Prenota', 'Status': 'Available', 'EventName': 'YOGA', 'TimeStart': '18:30'}])
        self.session.get.return_value = response(text='<input type="hidden" id="appointmentID" value="123"><button id="btnConfirmAppointmentBooking"></button>')
        self.session.post.return_value = response(payload={'message': 'Prenotazione confermata con successo.'})
        booked = client.book('1', '2026-11-17', '123')
        self.assertEqual(booked['status'], 'confirmed')
        self.assertEqual((booked['course'], booked['date'], booked['time']), ('YOGA', '2026-11-17', '18:30'))
        self.assertEqual(self.session.post.call_args.kwargs['data'], {'appointmentID': '123'})

    def test_book_refuses_confirmation_requiring_area_selection(self):
        client = self.client()
        client.schedule = MagicMock(return_value=[{'AppointmentId': '123', 'Type': 'class', 'Notes': 'Prenota', 'Status': 'Available', 'EventName': 'YOGA', 'TimeStart': '18:30'}])
        self.session.get.return_value = response(text='<input type="hidden" id="appointmentID" value="123"><select id="cmbAreaElementID"></select><button id="btnConfirmAppointmentBooking"></button>')
        with self.assertRaises(ValueError):
            client.book('1', '2026-11-17', '123')
        self.session.post.assert_not_called()

    def test_book_refuses_confirmation_for_another_appointment(self):
        client = self.client()
        client.schedule = MagicMock(return_value=[{'AppointmentId': '123', 'Type': 'class', 'Notes': 'Prenota', 'Status': 'Available', 'EventName': 'YOGA', 'TimeStart': '18:30'}])
        self.session.get.return_value = response(text='<input type="hidden" id="appointmentID" value="999"><button id="btnConfirmAppointmentBooking"></button>')
        with self.assertRaises(ValueError):
            client.book('1', '2026-11-17', '123')
        self.session.post.assert_not_called()

    def test_book_requires_course_name_and_time_for_notification(self):
        client = self.client()
        client.schedule = MagicMock(return_value=[{'AppointmentId': '123', 'Type': 'class', 'Notes': 'Prenota', 'Status': 'Available'}])
        self.session.get.return_value = response(text='<input type="hidden" id="appointmentID" value="123"><button id="btnConfirmAppointmentBooking"></button>')
        self.session.post.return_value = response(payload={'message': 'Prenotazione confermata con successo.'})
        with self.assertRaises(ValueError):
            client.book('1', '2026-11-17', '123')
        self.session.post.assert_not_called()

    def test_invalid_action_never_opens_session(self):
        with self.assertRaises(ValueError):
            app.lambda_handler({'action': 'unknown'}, None)

    def test_booking_and_cancellation_notifications_include_course_and_time(self):
        sns = MagicMock()
        telegram = response(payload={'ok': True})
        env = {'SNS_TOPIC_ARN': 'arn:topic', 'TELEGRAM_BOT_TOKEN': 'secret-token', 'TELEGRAM_CHAT_ID': '123'}
        with patch.dict('os.environ', env, clear=True), patch.dict(sys.modules, {'boto3': sns}), \
             patch.object(app.requests, 'post', return_value=telegram) as send:
            for action in ('book', 'cancel'):
                result = app.notify({'course': 'YOGA', 'date': '2026-11-17', 'time': '18:30', 'message': 'ok'}, action)
                self.assertEqual(result['notifications'], {'sns': 'accepted', 'telegram': 'accepted'})
                self.assertIn('YOGA', sns.client.return_value.publish.call_args.kwargs['Message'])
                self.assertIn('18:30', sns.client.return_value.publish.call_args.kwargs['Message'])
                self.assertIn('YOGA', send.call_args.kwargs['json']['text'])

    def test_notification_failure_does_not_turn_success_into_booking_failure(self):
        sns = MagicMock()
        sns.client.side_effect = RuntimeError('unavailable')
        with patch.dict('os.environ', {'SNS_TOPIC_ARN': 'arn:topic'}, clear=True), patch.dict(sys.modules, {'boto3': sns}):
            result = app.notify({'course': 'YOGA', 'date': '2026-11-17', 'time': '18:30', 'message': 'ok'}, 'cancel')
        self.assertEqual(result['notifications']['sns'], 'failed')

    def test_sns_uses_short_timeouts_and_does_not_retry(self):
        sns = MagicMock()
        with patch.dict('os.environ', {'SNS_TOPIC_ARN': 'arn:topic'}, clear=True), patch.dict(sys.modules, {'boto3': sns}):
            app.notify({'course': 'YOGA', 'date': '2026-11-17', 'time': '18:30'})
        sns.session.Config.assert_called_once_with(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1})
        sns.client.assert_called_once_with('sns', config=sns.session.Config.return_value)

    def test_sns_failure_does_not_block_telegram(self):
        sns = MagicMock()
        sns.client.side_effect = RuntimeError('unavailable')
        env = {'SNS_TOPIC_ARN': 'arn:topic', 'TELEGRAM_BOT_TOKEN': 'secret-token', 'TELEGRAM_CHAT_ID': '123'}
        with patch.dict('os.environ', env, clear=True), patch.dict(sys.modules, {'boto3': sns}), \
             patch.object(app.requests, 'post', return_value=response(payload={'ok': True})):
            result = app.notify({'course': 'YOGA', 'date': '2026-11-17', 'time': '18:30', 'message': 'ok'}, 'cancel')
        self.assertEqual(result['notifications'], {'sns': 'failed', 'telegram': 'accepted'})

    def test_telegram_failure_does_not_log_token(self):
        import contextlib
        import io
        import requests
        output = io.StringIO()
        env = {'TELEGRAM_BOT_TOKEN': 'secret-token', 'TELEGRAM_CHAT_ID': '123'}
        with patch.dict('os.environ', env, clear=True), contextlib.redirect_stdout(output), \
             patch.object(app.requests, 'post', side_effect=requests.ConnectionError('secret-token')):
            result = app.notify({'course': 'YOGA', 'date': '2026-11-17', 'time': '18:30', 'message': 'ok'})
        self.assertEqual(result['notifications']['telegram'], 'failed')
        self.assertNotIn('secret-token', output.getvalue())

    def test_dispatch_notifies_only_after_a_confirmed_mutation(self):
        env = {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'pass'}
        with patch.dict('os.environ', env, clear=True), patch.object(app.requests, 'Session'), \
             patch.object(app, 'SportrickClient') as factory, patch.object(app, 'notify', side_effect=lambda result, action: result) as notify:
            factory.return_value.cancel.return_value = {'status': 'uncertain'}
            app.lambda_handler({'action': 'cancel', 'booking_id': '123'}, None)
            notify.assert_not_called()
            factory.return_value.cancel.return_value = {'status': 'confirmed'}
            app.lambda_handler({'action': 'cancel', 'booking_id': '123'}, None)
            notify.assert_called_once_with({'status': 'confirmed'}, 'cancel')

    def test_invalid_date_does_not_open_session(self):
        with patch.object(app.requests, 'Session') as factory:
            with self.assertRaises(ValueError):
                app.lambda_handler({'action': 'schedule', 'activity_id': '123', 'date': '2026-02-31'}, None)
        factory.assert_not_called()

    def test_read_only_actions_do_not_fall_through_to_booking(self):
        session = MagicMock()
        page = response(text='<input type="hidden" name="__RequestVerificationToken" value="token">')
        session.get.side_effect = [page, response(text=CATALOG)]
        session.post.return_value = response(status=302, location='/replywellnessTO')
        with patch.dict('os.environ', {'SPORTRICK_USERNAME': 'user', 'SPORTRICK_PASSWORD': 'pass'}), \
             patch.object(app.requests, 'Session') as factory:
            factory.return_value.__enter__.return_value = session
            result = app.lambda_handler({'action': 'activities'}, None)
        self.assertEqual(result['activities'][0]['name'], 'VISITA MEDICA')
        self.assertEqual(session.post.call_count, 1)


if __name__ == '__main__':
    unittest.main()
