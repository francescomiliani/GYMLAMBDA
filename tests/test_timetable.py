import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lambda_function'))
import timetable


class TimetableTests(unittest.TestCase):
    def test_week_lists_gym_and_courses_from_the_photo(self):
        text = timetable.format_week()
        self.assertIn('07:00-09:00, 12:30-14:30, 18:15-20:15', text)
        self.assertIn('Lunedì: 13:00 GAG, 18:30 Functional Training', text)
        self.assertIn('Venerdì: 12:30 Cross Training, 13:30 Functional Training, 18:30 Functional Training', text)
        self.assertNotIn('Sabato', text)

    def test_parse_times_keeps_priority_order_and_rejects_unknown_slots(self):
        self.assertEqual(timetable.parse_times('12:30,07:00'), ['12:30', '07:00'])
        self.assertEqual(timetable.parse_times('off'), [])
        for bad in ('', '08:00', '07:00,07:00', '07:00;12:30', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                timetable.parse_times(bad)

    def test_parse_day(self):
        self.assertEqual(timetable.parse_day('mon'), 'MON')
        for bad in ('SAT', 'SUN', 'xx', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                timetable.parse_day(bad)

    def test_set_day_copies_and_removes(self):
        prefs = timetable.set_day(None, 'MON', ['07:00', '12:30'])
        self.assertEqual(prefs, {'MON': [{'activity': 'gym', 'time': '07:00'}, {'activity': 'gym', 'time': '12:30'}]})
        other = timetable.set_day(prefs, 'WED', ['18:15'])
        self.assertNotIn('WED', prefs)
        self.assertEqual(timetable.set_day(other, 'MON', []), {'WED': other['WED']})

    def test_resolve_times_fallback_order_default_and_empty_days(self):
        prefs = timetable.set_day(None, 'MON', ['07:00', '12:30', '18:15'])
        self.assertEqual(timetable.resolve_times(prefs, '2026-11-23'), ['07:00', '12:30', '18:15'])  # lunedì
        self.assertEqual(timetable.resolve_times(prefs, '2026-11-24'), [])  # martedì senza preferenza: non prenota
        self.assertEqual(timetable.resolve_times({}, '2026-11-23', '18:15'), [])
        self.assertEqual(timetable.resolve_times(None, '2026-11-24', '18:15'), ['18:15'])  # nessuna pref: legacy
        self.assertEqual(timetable.resolve_times(None, '2026-11-24'), [])

    def test_data_is_consistent(self):
        self.assertEqual(set(timetable.COURSES), set(timetable.DAY_NAMES))
        self.assertTrue(set(timetable.DAY_NAMES) <= set(timetable.DAYS))
        for start, end in timetable.GYM_SLOTS:
            self.assertLess(start, end)
        for day, items in timetable.COURSES.items():
            self.assertEqual([i[0] for i in items], sorted(i[0] for i in items), day)
            for start, end, title in items:
                self.assertLess(start, end)
                self.assertTrue(title)
        self.assertLess(len(timetable.format_week()), 1000)  # un solo messaggio Telegram

    def test_input_variants(self):
        self.assertEqual(timetable.parse_times('OFF'), [])
        self.assertEqual(timetable.parse_day('Fri'), 'FRI')
        for bad in ('07:00,', ',07:00', '07:00, 12:30', '7:00', '07:00-09:00'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                timetable.parse_times(bad)

    def test_resolve_ignores_unknown_activities_and_malformed_entries(self):
        prefs = {'MON': [{'activity': 'course', 'time': '13:00'}, {'activity': 'gym', 'time': '07:00'}, {}]}
        self.assertEqual(timetable.resolve_times(prefs, '2026-11-23'), ['07:00'])

    def test_format_prefs(self):
        self.assertIn('orario predefinito', timetable.format_prefs(None))
        self.assertIn('non prenota', timetable.format_prefs({}))
        prefs = timetable.set_day(timetable.set_day(None, 'WED', ['18:15']), 'MON', ['07:00', '12:30'])
        self.assertEqual(timetable.format_prefs(prefs), 'Lunedì 07:00 > 12:30; Mercoledì 18:15')


if __name__ == '__main__':
    unittest.main()
