"""La skill Claude `gymbooking` deve restare allineata a SportrickClient e senza segreti."""
import ast
import json
import re
import unittest
from pathlib import Path

from lambda_function import sportrick

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / '.claude' / 'skills' / 'gymbooking' / 'SKILL.md'
SOURCE = (ROOT / 'lambda_function' / 'sportrick.py').read_text()
ENDPOINT = re.compile(r'/(?:Account|Booking)/[A-Za-z]+')


def skill_text():
    return SKILL.read_text()


class SkillTest(unittest.TestCase):
    def test_frontmatter(self):
        text = skill_text()
        self.assertTrue(text.startswith('---\n'))
        header = text.split('---', 2)[1]
        self.assertRegex(header, r'(?m)^name: gymbooking$')
        self.assertRegex(header, r'(?m)^description: \S')

    def test_endpoints_match_client(self):
        self.assertEqual(set(ENDPOINT.findall(SOURCE)), set(ENDPOINT.findall(skill_text())) - {'/Booking/BookActivity'})

    def test_base_url(self):
        self.assertIn(sportrick.BASE, skill_text())

    def test_form_fields_and_markers(self):
        text = skill_text()
        for token in ('appointmentID', 'appointmentId', 'bookingId', 'id=', '__RequestVerificationToken',
                      'currentDate', 'activityID', 'skillID', 'activityFlags', 'getForCatchUp',
                      'Prenotazione confermata con successo.', 'Prenota', 'btnConfirmAppointmentBooking',
                      'btnAggiungiListaAttesa', 'idImpegno', 'seleziona-attivita', 'data-idattivita',
                      'data-flags', 'canDelete', 'isOnWaitlist'):
            self.assertIn(token, text, token)

    def test_safety_rules(self):
        text = skill_text().lower()
        self.assertIn('conferma esplicita', text)
        self.assertIn('non ritentare', text)
        self.assertIn('europe/rome', text)

    def test_notify_block(self):
        text = skill_text()
        block = re.search(r"python3 - \"\$@\" <<'PY'\n(.*?)\nPY\n", text, re.S).group(1)
        compile(block, 'notify', 'exec')
        # topic come in README e Subject SNS solo ASCII (le emoji stanno nel corpo)
        topic = re.search(r'arn:aws:sns:[\w-]+:\d+:GymBooking-notifiche', (ROOT / 'README.md').read_text()).group(0)
        self.assertIn(topic, block)
        for token in ('aws', 'sns', 'publish', '--profile', 'aws-personale', 'eu-north-1', 'SUBJECT'):
            self.assertIn(token, block, token)
        namespace = {}
        for name in ('SUBJECT', 'HEAD', 'TAIL'):
            namespace[name] = ast.literal_eval(re.search(rf'^{name} = (\{{.*?\}})\n(?=[A-Z]|text)', block, re.S | re.M).group(1))
        kinds = {'book', 'cancel', 'wl_join', 'wl_leave'}
        for name, table in namespace.items():
            self.assertEqual(set(table), kinds, name)
        for kind in kinds:
            self.assertTrue(namespace['SUBJECT'][kind].isascii(), kind)
            self.assertFalse(namespace['HEAD'][kind].isascii(), kind)
            self.assertFalse(namespace['TAIL'][kind].isascii(), kind)

    def test_no_secrets(self):
        text = skill_text()
        self.assertNotRegex(text, r'\d{8,}:[A-Za-z0-9_-]{30,}')
        credentials = ROOT / 'lambda_function' / 'credentials.json'
        if credentials.exists():
            for value in json.loads(credentials.read_text()).values():
                self.assertNotIn(value, text)
        token = Path.home() / '.config' / 'gymbooking' / 'telegram-token'
        if token.exists():
            self.assertNotIn(token.read_text().strip(), text)


if __name__ == '__main__':
    unittest.main()
