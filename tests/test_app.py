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


WAITLIST_NOTE = "Lezione al completo. Vuoi metterti in lista d'attesa?"
WAITLIST_PAGE = '<input type="hidden" name="idImpegno" value="777"><button id="btnAggiungiListaAttesa">x</button>'


class WaitlistBookingTests(unittest.TestCase):
    def run_booking(self, slots, event, posts, page=WAITLIST_PAGE):
        """posts: risposte dopo login e calendario (prenotazione e/o lista d'attesa)."""
        login, activity = MagicMock(), MagicMock()
        successful_login(login)
        activity.json.return_value = {'data': slots}
        session = session_with_login()
        session.post.side_effect = [login, activity, *posts]
        listing = MagicMock()
        listing.text = page
        listing.url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/ConfirmActivityBooking'
        session.get.side_effect = [session.get.return_value, listing]
        env = {'SPORTRICK_USERNAME': 'u', 'SPORTRICK_PASSWORD': 'p', 'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}
        with patch.dict(os.environ, env), patch.object(app.requests, 'Session') as factory, \
                patch.object(app, '_calendar_side', return_value=('', None)) as calendar, \
                contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.__enter__.return_value = session
            try:
                result = app.lambda_handler(event, None)
            except RuntimeError as error:
                result = error
        return result, session, calendar

    @staticmethod
    def reply(body):
        response = MagicMock()
        response.json.return_value = body
        return response

    FULL_THEN_FREE = [
        {'TimeStart': '07:00', 'AppointmentId': '10', 'Notes': WAITLIST_NOTE},
        {'TimeStart': '08:00', 'AppointmentId': '11', 'Notes': 'Prenota'},
    ]

    def test_full_first_choice_books_fallback_and_joins_waitlist(self):
        booked = self.reply({'message': 'Prenotazione confermata con successo.'})
        joined = self.reply({'status': 'OK', 'message': "Aggiunto alla lista d'attesa!"})
        result, session, _ = self.run_booking(
            self.FULL_THEN_FREE, {'date': '2026-11-20', 'time': '07:00', 'fallback_times': ['08:00'], 'waitlist': True},
            [booked, joined])
        self.assertEqual(result['time'], '08:00')
        self.assertEqual(result['waitlist'], {'time': '07:00', 'status': 'confirmed'})
        posts = session.post.call_args_list
        self.assertEqual(posts[2].kwargs['data'], {'appointmentID': '11'})  # prima il posto libero
        self.assertTrue(posts[3].args[0].endswith('/Booking/ConfirmAddToWaitList'))
        self.assertEqual(posts[3].kwargs['data'], {'appointmentId': '777'})  # idImpegno, non l'id dello slot
        self.assertEqual(session.get.call_args_list[1].kwargs['params'], {'appointmentID': '10'})

    def test_all_full_only_waitlist_is_reported_as_waitlisted(self):
        slots = [{'TimeStart': '07:00', 'AppointmentId': '10', 'Notes': WAITLIST_NOTE},
                 {'TimeStart': '08:00', 'AppointmentId': '11', 'Notes': WAITLIST_NOTE}]
        joined = self.reply({'status': 'OK'})
        result, session, calendar = self.run_booking(
            slots, {'date': '2026-11-20', 'time': '07:00', 'fallback_times': ['08:00'], 'waitlist': True}, [joined])
        self.assertEqual(result['status'], 'waitlisted')
        self.assertEqual(result['time'], '07:00')
        self.assertNotIn('/Booking/BookAppointment', [c.args[0][-25:] for c in session.post.call_args_list[2:]])
        calendar.assert_not_called()  # in lista non c'è impegno: niente evento

    def test_without_the_flag_nothing_changes_for_a_full_slot(self):
        result, session, _ = self.run_booking(
            self.FULL_THEN_FREE[:1], {'date': '2026-11-20', 'time': '07:00'}, [])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(session.post.call_count, 2)  # nessuna lista d'attesa senza richiesta

    def test_free_first_choice_never_touches_the_waitlist(self):
        slots = [{'TimeStart': '07:00', 'AppointmentId': '10', 'Notes': 'Prenota'}]
        result, session, _ = self.run_booking(
            slots, {'date': '2026-11-20', 'time': '07:00', 'waitlist': True},
            [self.reply({'message': 'Prenotazione confermata con successo.'})])
        self.assertNotIn('waitlist', result)
        self.assertEqual(session.post.call_count, 3)

    def test_rejected_waitlist_does_not_hide_the_booking(self):
        booked = self.reply({'message': 'Prenotazione confermata con successo.'})
        result, _, _ = self.run_booking(
            self.FULL_THEN_FREE, {'date': '2026-11-20', 'time': '07:00', 'fallback_times': ['08:00'], 'waitlist': True},
            [booked, self.reply({'status': 'ERROR', 'message': 'no'})])
        self.assertEqual(result['time'], '08:00')
        self.assertEqual(result['waitlist']['status'], 'rejected')

    def test_missing_waitlist_button_is_rejected_without_a_post(self):
        booked = self.reply({'message': 'Prenotazione confermata con successo.'})
        result, session, _ = self.run_booking(
            self.FULL_THEN_FREE, {'date': '2026-11-20', 'time': '07:00', 'fallback_times': ['08:00'], 'waitlist': True},
            [booked], page='<p>niente</p>')
        self.assertEqual(result['waitlist']['status'], 'rejected')
        self.assertEqual(session.post.call_count, 3)

    def test_uncertain_waitlist_alone_is_not_retried_and_is_an_error(self):
        slots = [{'TimeStart': '07:00', 'AppointmentId': '10', 'Notes': WAITLIST_NOTE}]
        broken = MagicMock()
        broken.raise_for_status.side_effect = app.requests.RequestException('x')
        result, session, _ = self.run_booking(slots, {'date': '2026-11-20', 'time': '07:00', 'waitlist': True}, [broken])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn('incerto', str(result))
        self.assertEqual(session.post.call_count, 3)

    def test_notify_text_mentions_the_waitlist(self):
        with patch.dict(os.environ, {'SNS_TOPIC_ARN': '', 'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': ''}), \
                patch.object(app, '_calendar_side', return_value=('', None)):
            both = {'date': '2026-11-20', 'time': '08:00', 'waitlist': {'time': '07:00', 'status': 'confirmed'}}
            sent = {}
            with patch.object(app.requests, 'post') as post:
                post.return_value.json.return_value = {'ok': True}
                with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '1'}):
                    app.notify(both)
                    sent['both'] = post.call_args.kwargs['json']['text']
                    app.notify({'status': 'waitlisted', 'date': '2026-11-20', 'time': '07:00',
                                'waitlist': {'time': '07:00', 'status': 'confirmed'}})
                    sent['only'] = post.call_args.kwargs['json']['text']
        self.assertIn("lista d'attesa per le 07:00", sent['both'])
        self.assertIn("2026-11-20 08:00", sent['both'])
        self.assertIn("in lista d'attesa", sent['only'])
        self.assertNotIn('prenotazione confermata', sent['only'])


if __name__ == '__main__':
    unittest.main()
