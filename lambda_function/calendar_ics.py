"""File iCalendar degli impegni, in UTC, da importare in Google Calendar."""
import datetime
import re
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
UTC = datetime.timezone.utc


def minutes(item):
    match = re.match(r'(\d+)', str(item.get('TimeDuration') or item.get('duration') or ''))
    return int(match.group(1)) if match else 60


def window(item):
    """Start and end in Europe/Rome, or None when the item is not a class to remember."""
    if item.get('isService') is True or item.get('isOnWaitlist') is True:
        return None
    date = item.get('date') or item.get('DateStart')
    clock = item.get('time') or item.get('TimeStart')
    if not isinstance(date, str) or not isinstance(clock, str) or ':' not in clock:
        return None
    try:
        day = datetime.datetime.strptime(date, '%d/%m/%Y').date() if '/' in date else datetime.date.fromisoformat(date)
        hour, minute = clock.split(':', 1)
        start = datetime.datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=ROME)
    except ValueError:
        return None
    return start, start + datetime.timedelta(minutes=minutes(item))


def open_items(items, now):
    chosen = []
    for item in items or []:
        span = window(item)
        if span and span[1] > now:
            chosen.append(item)
    return chosen


def uid(item):
    key = item.get('id') or item.get('appointment_id') or item.get('booking_id')
    if key:
        return f'gymbooking-{key}@gymbooking'
    date = item.get('date') or item.get('DateStart')
    clock = (item.get('time') or item.get('TimeStart') or '').replace(':', '')
    return f'gymbooking-{date}-{clock}@gymbooking'


def _escape(text):
    return str(text).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def _stamp(moment):
    return moment.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')


def vevent(item, now):
    span = window(item)
    if not span:
        return None
    start, end = span
    summary = item.get('description') or item.get('course') or item.get('EventName') or 'Palestra'
    return '\r\n'.join([
        'BEGIN:VEVENT',
        f'UID:{uid(item)}',
        f'DTSTAMP:{_stamp(now)}',
        f'DTSTART:{_stamp(start)}',
        f'DTEND:{_stamp(end)}',
        f'SUMMARY:{_escape(summary)}',
        'LOCATION:Reply Wellness',
        'COLOR:green',
        'END:VEVENT',
    ])


IMPORT_NOTE = ('Importa il file in Google Calendar. Un secondo import può creare un duplicato. '
               'Una cancellazione sul portale non toglie un evento importato da file.')


def document_body(chat_id, filename, content, caption):
    """Multipart body for Telegram sendDocument, without the bot token."""
    boundary = 'gymbookingboundary'
    chunks = []
    for name, value, attached in (
            ('chat_id', str(chat_id), None),
            ('caption', caption, None),
            ('document', content.encode(), filename)):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if attached:
            head += f'; filename="{attached}"\r\nContent-Type: text/calendar'
        chunks.append(head.encode() + b'\r\n\r\n' + (value if isinstance(value, bytes) else value.encode()) + b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def render(items, now=None):
    now = now or datetime.datetime.now(ROME)
    events = [vevent(item, now) for item in open_items(items, now)]
    events = [event for event in events if event]
    if not events:
        return None
    body = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//GymBooking//IT', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH']
    body.extend(events)
    body.append('END:VCALENDAR')
    return '\r\n'.join(body) + '\r\n'
