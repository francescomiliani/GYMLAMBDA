import datetime
import os
import unittest
from unittest.mock import patch

from lambda_function import telegram_bot, waitlist_watch

TODAY = datetime.date(2026, 11, 19)


def waiting(time='07:00', **extra):
    return {'id': '501', 'description': 'SALA ATTREZZI', 'DateStart': '20/11/2026', 'TimeStart': time,
            'TimeDuration': '60 min', 'isOnWaitlist': True, 'canDeleteFromWaitlist': True,
            'canMoveToAttendants': False, 'canDelete': False, **extra}


def confirmed(time='08:00', booking_id='502', **extra):
    return {'id': booking_id, 'description': 'SALA ATTREZZI', 'DateStart': '20/11/2026', 'TimeStart': time,
            'TimeDuration': '60 min', 'isOnWaitlist': False, 'canDelete': True, **extra}


class Memory:
    def __init__(self, value=None):
        self.value = value
        self.writes = []

    def get_state(self, key):
        return self.value

    def put_state(self, key, value, purge_at=None):
        self.writes.append((key, value))
        self.value = value


class EvaluateTests(unittest.TestCase):
    def test_first_run_only_records_waiting_entries(self):
        promoted, offers, seen = waitlist_watch.evaluate(None, [waiting(), confirmed('08:00')], TODAY)
        self.assertEqual((promoted, offers), ([], []))
        self.assertEqual([entry['TimeStart'] for entry in seen], ['07:00'])

    def test_entry_that_becomes_a_booking_is_promoted_with_same_day_others(self):
        previous = [waitlist_watch._slim(waiting())]
        promoted, _, seen = waitlist_watch.evaluate(
            previous, [confirmed('07:00', '600'), confirmed('08:00', '502')], TODAY)
        self.assertEqual(seen, [])
        (item, others), = promoted
        self.assertEqual(item['id'], '600')
        self.assertEqual([other['id'] for other in others], ['502'])

    def test_vanished_entry_is_dropped_silently_and_services_are_ignored(self):
        previous = [waitlist_watch._slim(waiting())]
        promoted, offers, seen = waitlist_watch.evaluate(
            previous, [confirmed('07:00', '9', isService=True)], TODAY)
        self.assertEqual((promoted, offers, seen), ([], [], []))

    def test_past_waiting_entries_are_forgotten(self):
        old = waiting(DateStart='01/11/2026')
        self.assertEqual(waitlist_watch.evaluate([], [old], TODAY)[2], [])

    def test_move_offer_is_made_once(self):
        item = waiting(canMoveToAttendants=True)
        _, offers, seen = waitlist_watch.evaluate([], [item], TODAY)
        self.assertEqual(len(offers), 1)
        self.assertTrue(seen[0]['offered'])
        _, again, _ = waitlist_watch.evaluate(seen, [item], TODAY)
        self.assertEqual(again, [])


class HandlerTests(unittest.TestCase):
    ENV = {'BOT_TABLE_NAME': 't', 'TELEGRAM_CHAT_ID': '99'}

    def run_watch(self, state, bookings, sent=None, fail_send=False):
        memory = Memory(state)
        sent = sent if sent is not None else []

        class Client:
            def __init__(self, session):
                pass

            def login(self, user, password):
                pass

            def bookings(self):
                return bookings

        def send(token, chat, text, keyboard=None):
            if fail_send:
                raise telegram_bot.TelegramError('x')
            sent.append((text, keyboard))

        secrets = {'SPORTRICK_USERNAME': 'u', 'SPORTRICK_PASSWORD': 'p', 'TELEGRAM_BOT_TOKEN': 'tok'}
        with patch.dict(os.environ, self.ENV), patch.object(waitlist_watch, '_store', return_value=memory), \
                patch.object(waitlist_watch, 'SportrickClient', Client), \
                patch.object(waitlist_watch, 'get_secret', side_effect=secrets.get), \
                patch.object(waitlist_watch.telegram_bot, '_send', side_effect=send), \
                patch.object(waitlist_watch.telegram_bot, '_send_document') as document, \
                patch.object(waitlist_watch.google_calendar, 'publish',
                             return_value={'channel': 'google', 'ics': 'BEGIN:VCALENDAR'}) as publish:
            try:
                result = waitlist_watch.lambda_handler({}, None)
            except Exception as error:
                result = error
        return result, memory, sent, document, publish

    def test_promotion_sends_message_calendar_ics_and_forgets_the_entry(self):
        state = {'items': [waitlist_watch._slim(waiting())]}
        result, memory, sent, document, publish = self.run_watch(
            state, [confirmed('07:00', '600'), confirmed('08:00', '502')])
        self.assertEqual(result['promoted'], 1)
        text, keyboard = sent[0]
        self.assertIn("Sei passato da lista d'attesa a partecipante", text)
        self.assertIn('07:00', text)
        self.assertIn('altro impegno lo stesso giorno', text)
        data = [button['callback_data'] for row in keyboard for button in row]
        self.assertEqual(len(data), 2)
        self.assertTrue(all(item.startswith('bc:') and len(item.encode()) <= 64 for item in data))
        publish.assert_called_once()
        document.assert_called_once()
        self.assertEqual(memory.value, {'items': []})

    def test_promotion_without_double_booking_has_no_buttons(self):
        state = {'items': [waitlist_watch._slim(waiting())]}
        _, _, sent, _, _ = self.run_watch(state, [confirmed('07:00', '600')])
        self.assertIsNone(sent[0][1])

    def test_second_run_does_not_repeat_the_message(self):
        state = {'items': [waitlist_watch._slim(waiting())]}
        bookings = [confirmed('07:00', '600')]
        _, memory, sent, _, _ = self.run_watch(state, bookings)
        self.assertEqual(len(sent), 1)
        self.run_watch(memory.value, bookings, sent)
        self.assertEqual(len(sent), 1)

    def test_failed_send_keeps_the_entry_for_the_next_run(self):
        state = {'items': [waitlist_watch._slim(waiting())]}
        result, memory, _, _, publish = self.run_watch(state, [confirmed('07:00', '600')], fail_send=True)
        self.assertIsInstance(result, telegram_bot.TelegramError)
        self.assertEqual(memory.writes, [])
        publish.assert_not_called()

    def test_calendar_failure_does_not_lose_the_notice(self):
        state = {'items': [waitlist_watch._slim(waiting())]}
        memory = Memory(state)
        with patch.object(waitlist_watch.google_calendar, 'publish', side_effect=RuntimeError('x')):
            waitlist_watch._after_promotion(confirmed('07:00', '600'), memory, 'tok', '99')

    def test_missing_configuration_stops_before_the_portal(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(waitlist_watch.requests, 'Session') as session:
            with self.assertRaises(RuntimeError):
                waitlist_watch.lambda_handler({}, None)
        session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
