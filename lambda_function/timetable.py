"""Orari della palestra e preferenze di prenotazione.

Fonte degli orari: foto ufficiale in docs/reference/orari-palestra-2026-11.png
(verificata il 2026-11-18). Gli orari reali e la disponibilità restano quelli del
portale: questi dati servono per orientarsi e per validare le preferenze.

Le preferenze sono per giorno della lezione. Il trigger parte ogni giorno a
mezzanotte e prenota lo stesso giorno sette giorni dopo, solo se è in preferenza.
"""
import datetime
import re

DAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
DAY_NAMES = {'MON': 'Lunedì', 'TUE': 'Martedì', 'WED': 'Mercoledì', 'THU': 'Giovedì', 'FRI': 'Venerdì'}
GYM = 'gym'  # sala attrezzi; unica attività prenotabile dalle preferenze
GYM_ACTIVITY_ID = '224715757956816031'
# Slot da un'ora prenotabili sul portale (lun-ven): finestre 07:00-09:00, 12:30-14:30, 18:15-20:15.
GYM_SLOTS = (('07:00', '08:00'), ('08:00', '09:00'), ('12:30', '13:30'), ('13:30', '14:30'),
             ('18:15', '19:15'), ('19:15', '20:15'))
DAY_ALIASES = {'LUN': 'MON', 'MAR': 'TUE', 'MER': 'WED', 'GIO': 'THU', 'VEN': 'FRI', 'SAB': 'SAT', 'DOM': 'SUN',
               'LUNEDI': 'MON', 'MARTEDI': 'TUE', 'MERCOLEDI': 'WED', 'GIOVEDI': 'THU', 'VENERDI': 'FRI',
               'SABATO': 'SAT', 'DOMENICA': 'SUN'}
PERIODS = (('🌅', 'Mattina', '00:00', '12:00'), ('🍽️', 'Pranzo', '12:00', '16:00'), ('🌙', 'Sera', '16:00', '24:00'))


def period_of(time):
    """Emoji and name of the part of the day for an HH:MM time."""
    for emoji, name, start, end in PERIODS:
        if start <= time < end:
            return emoji, name
    return PERIODS[-1][:2]
COURSES = {
    'MON': (('13:00', '14:00', 'GAG'), ('18:30', '19:30', 'Functional Training')),
    'TUE': (('08:00', '09:00', 'Functional Training'), ('13:00', '14:00', 'Total Body'),
            ('18:30', '19:30', 'Yoga')),
    'WED': (('13:00', '14:00', 'Cross Training'), ('18:30', '19:30', 'Total Body')),
    'THU': (('08:00', '09:00', 'Cross Training'), ('13:00', '14:00', 'Functional Training'),
            ('18:30', '19:30', 'Pilates')),
    'FRI': (('12:30', '13:30', 'Cross Training'), ('13:30', '14:30', 'Functional Training'),
            ('18:30', '19:30', 'Functional Training')),
}


def format_week():
    lines = ['🗓️ Orari della palestra', '', '🏋️ Sala attrezzi (lun-ven, slot da 1 ora)']
    for emoji, name, start, end in PERIODS:
        slots = [a for a, _ in GYM_SLOTS if start <= a < end]
        if slots:
            lines.append(f'{emoji} {name}: ' + '  ·  '.join(slots))
    lines += ['', '🧘 Sala corsi']
    for day, name in DAY_NAMES.items():
        lines.append(f'• {name[:3]}: ' + '  ·  '.join(f'{a} {title}' for a, _, title in COURSES[day]))
    lines += ['', '👉 Disponibilità in tempo reale: /disponibilita',
              'ℹ️ Dati dalla foto del 2026-11-18, verifica sempre sul portale.']
    return '\n'.join(lines)


def gym_times():
    return [start for start, _ in GYM_SLOTS]


def parse_day(value):
    """MON..FRI in English or Italian (lun, lunedì, ven...), any case."""
    day = value.upper().replace('Ì', 'I') if isinstance(value, str) else None
    day = DAY_ALIASES.get(day, day)
    if day not in DAY_NAMES:
        raise ValueError('Giorno non valido: scrivi lun, mar, mer, gio, ven (o MON, TUE, WED, THU, FRI)')
    return day


def _normalize_time(item):
    """'7', '7:00', '07.00', '0700' -> '07:00'; anything else is returned unchanged."""
    match = re.fullmatch(r'(\d{1,2})(?:[:.]?(\d{2}))?', item)
    return f'{int(match[1]):02d}:{match[2] or "00"}' if match else item


def parse_times(text):
    """'07:00,12:30' -> ['07:00', '12:30'] (ordine = priorità); 'off' -> []. Also accepts '7,12.30'."""
    if not isinstance(text, str) or not text:
        raise ValueError('Orari non validi')
    if text.lower() in ('off', 'no', 'niente'):
        return []
    times = [_normalize_time(item) for item in text.split(',')]
    if len(set(times)) != len(times) or any(item not in gym_times() for item in times):
        raise ValueError('Orari ammessi sala attrezzi: ' + ', '.join(gym_times())
                         + '. Esempio: /prefs lun 07:00,12:30 (prima le 7, poi le 12:30)')
    return times


def set_day(prefs, day, times):
    """Return a copy of prefs with the ordered gym times for day (empty list removes the day)."""
    prefs = dict(prefs or {})
    if times:
        prefs[day] = [{'activity': GYM, 'time': item} for item in times]
    else:
        prefs.pop(day, None)
    return prefs


def format_prefs(prefs):
    if prefs is None:
        return 'nessuna preferenza: prenota solo l’orario predefinito'
    if not prefs:
        return 'nessun giorno impostato: l’automazione non prenota'
    lines = []
    for day in DAYS:
        if day in prefs:
            times = [e['time'] for e in prefs[day]]
            lines.append(f'• {DAY_NAMES[day]}: ' + ' → '.join(f'{period_of(t)[0]} {t}' for t in times)
                         + (' (se pieno, passa al successivo)' if len(times) > 1 else ''))
    return '\n' + '\n'.join(lines)


def resolve_times(prefs, date_iso, default_time=None):
    """Ordered times to try for the class date; [] means do not book.

    No preferences stored at all keeps the legacy single default time.
    """
    if prefs is None:
        return [default_time] if default_time else []
    day = DAYS[datetime.date.fromisoformat(date_iso).weekday()]
    return [e['time'] for e in prefs.get(day, []) if e.get('activity') == GYM]
