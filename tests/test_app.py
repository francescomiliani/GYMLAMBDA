import contextlib
import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from lambda_function import app


def session_with_login():
    session = MagicMock()
    session.get.return_value.text = '<input type="hidden" name="__RequestVerificationToken" value="token">'
    return session


def successful_login(login):
    login.status_code = 302
    login.headers = {'Location': '/replywellnessTO'}


class BookingTests(unittest.TestCase):
    def test_login_and_booking(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        output = io.StringIO()
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({}, None)
        calls = session.post.call_args_list
        self.assertEqual(calls[0].kwargs['data'], {'UserName': 'secret-user', 'Password': 'secret-pass', '__RequestVerificationToken': 'token'})
        self.assertEqual(calls[2].kwargs['data'], {'appointmentID': '123'})
        self.assertTrue(all(call.kwargs['timeout'] == 15 for call in calls))
        self.assertNotIn('secret-user', output.getvalue())
        self.assertNotIn('secret-pass', output.getvalue())

    def test_failure_does_not_expose_response_or_credentials(self):
        session = session_with_login()
        session.post.side_effect = app.requests.RequestException('secret-user secret-pass')
        output = io.StringIO()
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError) as error:
                app.lambda_handler({}, None)
        self.assertNotIn('secret', output.getvalue() + str(error.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_missing_credentials_stops_before_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(app.requests, 'Session') as factory:
            with self.assertRaises(RuntimeError):
                app.lambda_handler({}, None)
        factory.assert_not_called()


    def test_inspect_lists_slots_without_booking(self):
        login, activity, page = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123'}]}
        page.text = '<form action="/Booking/BookAppointment"></form>'
        session = session_with_login()
        session.post.side_effect = [login, activity]
        session.get.side_effect = [session.get.return_value, page]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            result = app.lambda_handler({'inspect': True, 'date': '2026-11-17'}, None)
        self.assertEqual(result['slots'], [('07:00', '123')])
        self.assertEqual(session.post.call_count, 2)  # login + calendario, nessuna prenotazione

    def test_missing_time_never_calls_booking(self):
        login, activity = MagicMock(), MagicMock()
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        session = session_with_login()
        session.post.side_effect = [login, activity]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError) as error:
                app.lambda_handler({'date': '2026-11-17', 'time': '07:00'}, None)
        self.assertIn('orario non in calendario', str(error.exception))
        self.assertEqual(session.post.call_count, 2)  # mai BookAppointment

    def test_fallback_books_next_available_slot(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [
            {'TimeStart': '07:00', 'AppointmentId': 'full', 'Notes': 'Lezione al completo.'},
            {'TimeStart': '13:30', 'AppointmentId': 'free', 'Notes': 'Prenota'},
        ]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            result = app.lambda_handler({'date': '2026-11-16', 'time': '07:00', 'fallback_times': ['13:30']}, None)
        self.assertEqual(result['time'], '13:30')
        self.assertEqual(session.post.call_args_list[2].kwargs['data'], {'appointmentID': 'free'})

    def test_fallback_is_disabled_by_default(self):
        login, activity = MagicMock(), MagicMock()
        successful_login(login)
        activity.json.return_value = {'data': [
            {'TimeStart': '07:00', 'AppointmentId': 'full', 'Notes': 'Lezione al completo.'},
            {'TimeStart': '13:30', 'AppointmentId': 'free', 'Notes': 'Prenota'},
        ]}
        session = session_with_login()
        session.post.side_effect = [login, activity]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError):
                app.lambda_handler({'date': '2026-11-16', 'time': '07:00'}, None)
        self.assertEqual(session.post.call_count, 2)  # nessun ripiego, nessun BookAppointment

    def test_rejected_booking_reports_portal_message(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        booking.json.return_value = {'message': 'Lezione al completo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        output = io.StringIO()
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(output):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError) as error:
                app.lambda_handler({'date': '2026-11-17', 'time': '07:00'}, None)
        self.assertEqual(str(error.exception), 'Booking was not confirmed: Lezione al completo.')
        self.assertNotIn('secret', output.getvalue())

    def test_full_lesson_is_not_booked(self):
        login, activity = MagicMock(), MagicMock()
        successful_login(login)
        activity.json.return_value = {'data': [{
            'TimeStart': '08:00', 'AppointmentId': '123',
            'Notes': "Lezione al completo. Vuoi metterti in lista d'attesa?",
        }]}
        session = session_with_login()
        session.post.side_effect = [login, activity]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError) as error:
                app.lambda_handler({'date': '2026-11-18', 'time': '08:00'}, None)
        self.assertIn('non prenotabile', str(error.exception))
        self.assertEqual(session.post.call_count, 2)  # mai BookAppointment

    def test_successful_booking_sends_notification(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        fake_boto3 = MagicMock()
        env = {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass',
               'SNS_TOPIC_ARN': 'arn:aws:sns:eu-north-1:1:topic'}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {'boto3': fake_boto3}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({'date': '2026-11-17', 'time': '07:00'}, None)
        published = fake_boto3.client.return_value.publish.call_args.kwargs
        self.assertEqual(published['TopicArn'], 'arn:aws:sns:eu-north-1:1:topic')
        self.assertIn('2026-11-17 07:00', published['Message'])

    def test_notification_failure_does_not_lose_the_booking(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        fake_boto3 = MagicMock()
        fake_boto3.client.side_effect = RuntimeError('sns unavailable')
        env = {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass',
               'SNS_TOPIC_ARN': 'arn:aws:sns:eu-north-1:1:topic'}
        with patch.dict(os.environ, env), patch.dict(sys.modules, {'boto3': fake_boto3}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            result = app.lambda_handler({'date': '2026-11-17', 'time': '07:00'}, None)
        self.assertEqual(result['time'], '07:00')

    def test_no_topic_means_no_notification(self):
        fake_boto3 = MagicMock()
        with patch.dict(os.environ, {'SNS_TOPIC_ARN': ''}), patch.dict(sys.modules, {'boto3': fake_boto3}):
            app.notify({'date': '2026-11-17', 'time': '07:00', 'message': 'ok'})
        fake_boto3.client.assert_not_called()

    def test_local_timezone_is_rome(self):
        self.assertEqual(app.local_now().tzinfo.key, 'Europe/Rome')
        self.assertEqual(app.LOCAL_TZ.key, 'Europe/Rome')

    def test_defaults_to_seven_days_ahead_at_1815(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        successful_login(login)
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123', 'Notes': 'Prenota'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = session_with_login()
        session.post.side_effect = [login, activity, booking]
        expected = (app.local_now() + app.datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({}, None)
        calls = session.post.call_args_list
        self.assertEqual(calls[1].kwargs['data']['currentDate'], expected)
        self.assertEqual(calls[2].kwargs['data'], {'appointmentID': '123'})


if __name__ == '__main__':
    unittest.main()
