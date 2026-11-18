"""Orari della palestra e preferenze di prenotazione.

Fonte degli orari: foto ufficiale in docs/reference/orari-palestra-2026-11.png
(verificata il 2026-11-18). Gli orari reali e la disponibilità restano quelli del
portale: questi dati servono per orientarsi e per validare le preferenze.

Le preferenze sono per giorno della lezione. Il trigger del lunedì a mezzanotte
prenota lunedì, mercoledì e venerdì di quella settimana; gli altri trigger
prenotano lo stesso giorno sette giorni dopo.
"""
import datetime

DAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
DAY_NAMES = {'MON': 'Lunedì', 'TUE': 'Martedì', 'WED': 'Mercoledì', 'THU': 'Giovedì', 'FRI': 'Venerdì'}
GYM = 'gym'  # sala attrezzi; unica attività prenotabile dalle preferenze
GYM_ACTIVITY_ID = '224715757956816031'
GYM_SLOTS = (('07:00', '09:00'), ('12:30', '14:30'), ('18:15', '20:15'))  # lun-ven
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
    lines = ['🗓️ Orari palestra (da foto del 2026-11-18, verifica sempre sul portale)', '',
             '🏋️ Sala attrezzi (lun-ven): ' + ', '.join(f'{a}-{b}' for a, b in GYM_SLOTS), '',
             '🧘 Sala corsi:']
    for day, name in DAY_NAMES.items():
        lines.append(f'{name}: ' + ', '.join(f'{a} {title}' for a, _, title in COURSES[day]))
    return '\n'.join(lines)


def gym_times():
    return [start for start, _ in GYM_SLOTS]


def parse_day(value):
    day = value.upper() if isinstance(value, str) else None
    if day not in DAY_NAMES:
        raise ValueError('Giorno non valido: usa MON,TUE,WED,THU,FRI')
    return day


def parse_times(text):
    """'07:00,12:30' -> ['07:00', '12:30'] (ordine = priorità); 'off' -> []."""
    if not isinstance(text, str) or not text:
        raise ValueError('Orari non validi')
    if text.lower() == 'off':
        return []
    times = text.split(',')
    if len(set(times)) != len(times) or any(item not in gym_times() for item in times):
        raise ValueError('Orari ammessi sala attrezzi: ' + ', '.join(gym_times()))
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
    return '; '.join(f'{DAY_NAMES[d]} ' + ' > '.join(e['time'] for e in prefs[d])
                     for d in DAYS if d in prefs)


def resolve_times(prefs, date_iso, default_time=None):
    """Ordered times to try for the class date; [] means do not book.

    No preferences stored at all keeps the legacy single default time.
    """
    if prefs is None:
        return [default_time] if default_time else []
    day = DAYS[datetime.date.fromisoformat(date_iso).weekday()]
    return [e['time'] for e in prefs.get(day, []) if e.get('activity') == GYM]
