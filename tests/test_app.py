import contextlib
import io
import os
import unittest
from unittest.mock import MagicMock, patch

from lambda_function import app


class BookingTests(unittest.TestCase):
    def test_login_and_booking(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = MagicMock()
        session.post.side_effect = [login, activity, booking]
        output = io.StringIO()
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({}, None)
        calls = session.post.call_args_list
        self.assertEqual(calls[0].kwargs['data'], {'UserName': 'secret-user', 'Password': 'secret-pass'})
        self.assertEqual(calls[2].kwargs['data'], {'appointmentID': '123'})
        self.assertTrue(all(call.kwargs['timeout'] == 15 for call in calls))
        self.assertNotIn('secret-user', output.getvalue())
        self.assertNotIn('secret-pass', output.getvalue())

    def test_failure_does_not_expose_response_or_credentials(self):
        session = MagicMock()
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
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123'}]}
        page.text = '<form action="/Booking/BookAppointment"></form>'
        session = MagicMock()
        session.post.side_effect = [login, activity]
        session.get.return_value = page
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            result = app.lambda_handler({'inspect': True, 'date': '2026-11-17'}, None)
        self.assertEqual(result['slots'], [('07:00', '123')])
        self.assertEqual(session.post.call_count, 2)  # login + calendario, nessuna prenotazione

    def test_missing_time_never_calls_booking(self):
        login, activity = MagicMock(), MagicMock()
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123'}]}
        session = MagicMock()
        session.post.side_effect = [login, activity]
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            with self.assertRaises(RuntimeError) as error:
                app.lambda_handler({'date': '2026-11-17', 'time': '07:00'}, None)
        self.assertNotIn('07:00', str(error.exception))
        self.assertEqual(session.post.call_count, 2)  # mai BookAppointment

    def test_rejected_booking_reports_portal_message(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        activity.json.return_value = {'data': [{'TimeStart': '07:00', 'AppointmentId': '123'}]}
        booking.json.return_value = {'message': 'Lezione al completo.'}
        session = MagicMock()
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

    def test_defaults_to_seven_days_ahead_at_1815(self):
        login, activity, booking = (MagicMock() for _ in range(3))
        activity.json.return_value = {'data': [{'TimeStart': '18:15', 'AppointmentId': '123'}]}
        booking.json.return_value = {'message': 'Prenotazione confermata con successo.'}
        session = MagicMock()
        session.post.side_effect = [login, activity, booking]
        expected = (app.datetime.datetime.now() + app.datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'secret-user', 'SPORTRICK_PASSWORD': 'secret-pass'}), \
             patch.object(app.requests, 'Session') as factory, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            app.lambda_handler({}, None)
        calls = session.post.call_args_list
        self.assertEqual(calls[1].kwargs['data']['currentDate'], expected)
        self.assertEqual(calls[2].kwargs['data'], {'appointmentID': '123'})


if __name__ == '__main__':
    unittest.main()
