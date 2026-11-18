"""Live Google Calendar check. Runs only with GYMBOOKING_LIVE_GCAL=1.

Creates one far-future event, checks the reminders, then deletes it.
The secret file is never printed.
"""
import os
import unittest
from pathlib import Path

from lambda_function import google_calendar

ITEM = {'id': 'livetest', 'description': 'GymBooking live test', 'date': '2099-06-01', 'time': '07:00'}
SECRET = Path.home() / '.config' / 'gymbooking' / 'google-calendar.json'


@unittest.skipUnless(os.environ.get('GYMBOOKING_LIVE_GCAL') == '1', 'live Google Calendar check')
class GoogleCalendarLiveTests(unittest.TestCase):
    def test_import_reminders_and_delete(self):
        self.assertTrue(SECRET.is_file(), 'manca il file del calendario')
        outcome = google_calendar.publish(ITEM, path=SECRET)
        self.assertEqual(outcome['channel'], 'google', outcome.get('reason'))
        self.assertTrue(outcome.get('event_id'))
        try:
            import requests
            creds = google_calendar.load_credentials(SECRET)
            token = google_calendar._token(creds, requests)
            response = requests.get(
                google_calendar.EVENTS + '/' + outcome['event_id'],
                headers={'Authorization': 'Bearer ' + token}, timeout=15)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body.get('iCalUID'), 'gymbooking-livetest@gymbooking')
            self.assertEqual(body.get('summary'), 'GymBooking live test')
            minutes = sorted(item['minutes'] for item in body['reminders']['overrides'])
            self.assertEqual(minutes, [60, 720])
        finally:
            self.assertEqual(google_calendar.remove(ITEM['id'], path=SECRET), 'deleted')
            self.assertEqual(google_calendar.remove(ITEM['id'], path=SECRET), 'missing')
