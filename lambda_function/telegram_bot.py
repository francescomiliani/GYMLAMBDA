"""Private Telegram webhook; mutations are confirmed and dispatched separately."""

import base64
import binascii
import datetime
import hashlib
import hmac
import json
import os
import re
import time
from zoneinfo import ZoneInfo

import requests

if __package__:
    from . import telegram_worker
    from .bot_scheduler import BotScheduler, fingerprint
    from .store_conflict import StoreConflict
    from .sportrick import require_date, require_id
    from .ssm_config import get_secret
else:
    import telegram_worker
    from bot_scheduler import BotScheduler, fingerprint
    from store_conflict import StoreConflict
    from sportrick import require_date, require_id
    from ssm_config import get_secret


HELP = ('Comandi: /bookings, /activities, /schedule ID DATA, '
        '/book ATTIVITA DATA APPUNTAMENTO, /cancel ID, '
        '/waitlist_join ATTIVITA DATA APPUNTAMENTO, /waitlist_leave ID, '
        '/waitlist_confirm ID, /automation, /weekly GIORNI HH:MM, /disable. '
        'Per le azioni servono gli ID mostrati da /bookings, /activities e /schedule. '
        'Giorni: MON,TUE,WED,THU,FRI,SAT,SUN. L’attivazione è bloccata.')
MUTATIONS = {'book', 'cancel', 'waitlist_join', 'waitlist_leave', 'waitlist_confirm'}
MAX_BODY_BYTES = 64 * 1024


class TelegramError(Exception):
    pass


def _store():
    import boto3
    if __package__:
        from .bot_store import BotStore
    else:
        from bot_store import BotStore
    return BotStore(boto3.client('dynamodb'), os.environ['BOT_TABLE_NAME'])


def _scheduler():
    import boto3
    from botocore.config import Config
    return BotScheduler(boto3.client('scheduler', config=Config(retries={'total_max_attempts': 1})), os.environ['BOT_SCHEDULE_NAME'],
                        os.environ.get('BOT_SCHEDULE_GROUP', 'default'))


def _dispatch(job_id):
    import boto3
    from botocore.config import Config
    response = boto3.client('lambda', config=Config(retries={'total_max_attempts': 1})).invoke(
        FunctionName=os.environ['BOT_WORKER_FUNCTION'], InvocationType='Event',
        Payload=json.dumps({'job_id': job_id}).encode())
    if response.get('StatusCode') != 202:
        raise RuntimeError('Dispatch failed')


def _telegram(token, method, payload):
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/{method}', json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError('Invalid response')
    except (requests.RequestException, ValueError):
        raise TelegramError('Telegram request failed') from None


def _send(token, chat_id, text, keyboard=None):
    for offset in range(0, len(text), 1000):
        payload = {'chat_id': chat_id, 'text': text[offset:offset + 1000]}
        if keyboard and offset + 1000 >= len(text):
            payload['reply_markup'] = {'inline_keyboard': keyboard}
        _telegram(token, 'sendMessage', payload)


FIELDS = {
    'bookings': [('description', 'Descrizione'), ('id', 'ID impegno'), ('appointmentId', 'ID appuntamento'),
                 ('activityId', 'ID attività'), ('DateStart', 'Data'), ('TimeStart', 'Ora'),
                 ('TimeDuration', 'Durata'), ('isOnWaitlist', 'In lista d’attesa'), ('isService', 'Servizio'),
                 ('canDelete', 'Cancellabile'), ('canDeleteFromWaitlist', 'Uscita dalla lista consentita'),
                 ('canMoveToAttendants', 'Conferma dalla lista consentita')],
    'activities': [('name', 'Attività'), ('id', 'ID attività'), ('description', 'Descrizione'), ('flags', 'Tipi')],
    'schedule': [('EventName', 'Evento'), ('AppointmentId', 'ID appuntamento'), ('TimeStart', 'Ora'),
                 ('TimeDuration', 'Durata'), ('Notes', 'Note'), ('Status', 'Stato'), ('Type', 'Tipo'),
                 ('ServiceId', 'ID servizio'), ('StartDateTime', 'Data e ora'), ('ParentCourseId', 'ID corso')],
}


def _format_read(action, result):
    items = result[action]
    if not isinstance(items, list):
        raise ValueError('Risultato non valido')
    titles = {'bookings': 'Impegni', 'activities': 'Attività', 'schedule': 'Calendario'}
    if not items:
        return titles[action] + ': nessun risultato.'
    sections = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Risultato non valido')
        fields = [f'{label}: {item[key]}' for key, label in FIELDS[action] if key in item and item[key] is not None]
        sections.append('\n'.join(fields) if fields else 'Nessun campo disponibile')
    return titles[action] + ':\n\n' + '\n\n'.join(sections)


def _cancel_confirmation(store, confirmation, owner):
    from botocore.exceptions import ClientError
    try:
        store.client.delete_item(
            TableName=store.table_name,
            Key=store._encode({'pk': 'confirmation#' + confirmation}),
            ConditionExpression='attribute_exists(pk) AND #owner = :owner AND attribute_not_exists(job_id)',
            ExpressionAttributeNames={'#owner': 'owner'},
            ExpressionAttributeValues=store._encode({':owner': owner}),
        )
        return True
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def _command(text):
    parts = text.split()
    if not parts:
        return None
    action = parts[0].lstrip('/')
    args = parts[1:]
    if action in ('book', 'waitlist_join') and len(args) == 3:
        return {'action': action, 'activity_id': require_id(args[0]),
                'date': require_date(args[1]), 'appointment_id': require_id(args[2])}
    if action in ('cancel', 'waitlist_leave', 'waitlist_confirm') and len(args) == 1:
        return {'action': action, 'booking_id': require_id(args[0])}
    raise ValueError('Comando non valido')


def _handle_message(message, token, owner, update_id=None):
    text = message.get('text')
    if not isinstance(text, str):
        return
    parts = text.split()
    if not parts:
        return
    command = parts[0]
    chat_id = message['chat']['id']
    if command in ('/weekly', '/disable') or (command.startswith('/') and command[1:] in MUTATIONS):
        if type(update_id) is not int or update_id < 0:
            raise ValueError('Update ID non valido')
    if command in ('/start', '/help'):
        _send(token, chat_id, HELP, [[{'text': 'Impegni', 'callback_data': 'read:bookings'},
                                      {'text': 'Attività', 'callback_data': 'read:activities'}],
                                     [{'text': 'Automazione', 'callback_data': 'menu:automation'},
                                      {'text': 'Aiuto', 'callback_data': 'menu:help'}]])
    elif command in ('/bookings', '/activities', '/schedule'):
        action = command[1:]
        event = {'action': action}
        if action == 'schedule':
            if len(parts) != 3:
                raise ValueError('Uso: /schedule ID DATA')
            event.update(activity_id=require_id(parts[1]), date=require_date(parts[2]))
        elif len(parts) != 1:
            raise ValueError('Comando non valido')
        ui = {'kind': action, **{k: v for k, v in event.items() if k != 'action'}}
        if action == 'bookings':
            ui['sid'] = _screen_id(owner, update_id)
        _run_ui(token, chat_id, owner, ui)
    elif command == '/automation':
        snapshot = _scheduler().get_schedule()
        _send(token, chat_id, 'Stato: {}\nProgramma: {}\nFuso: {}'.format(
            snapshot.get('State'), snapshot.get('ScheduleExpression'), snapshot.get('ScheduleExpressionTimezone')))
    elif command in ('/weekly', '/disable'):
        payload = {'action': command[1:]}
        if command == '/weekly':
            if len(parts) != 3:
                raise ValueError('Uso: /weekly MON,WED HH:MM')
            days = parts[1].split(',')
            clock = parts[2].split(':')
            if (not days or len(set(days)) != len(days) or any(d not in ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN') for d in days)
                    or len(clock) != 2 or any(len(x) != 2 or not x.isascii() or not x.isdigit() for x in clock)
                    or int(clock[0]) > 23 or int(clock[1]) > 59):
                raise ValueError('Giorni o ora non validi')
            payload.update(days=days, hour=int(clock[0]), minute=int(clock[1]))
        elif len(parts) != 1:
            raise ValueError('Comando non valido')
        store = _store()
        previous = store.get_update(str(update_id))
        if previous:
            previous_payload = previous.get('payload')
            if (previous.get('kind') != 'confirmation' or previous.get('owner') != owner
                    or not isinstance(previous_payload, dict)
                    or {k: v for k, v in previous_payload.items() if k != 'fingerprint'} != payload
                    or not isinstance(previous_payload.get('fingerprint'), str)):
                raise StoreConflict('Update already used')
            payload['fingerprint'] = previous_payload['fingerprint']
        else:
            payload['fingerprint'] = fingerprint(_scheduler().get_schedule())
        _confirm(token, chat_id, owner, payload, update_id, store)
    elif command[1:] in MUTATIONS and command.startswith('/'):
        _confirm(token, chat_id, owner, _command(text), update_id)


def _confirm(token, chat_id, owner, payload, update_id, store=None, summary=None, expires_at=None):
    text, keyboard = _confirmation_message(owner, payload, update_id, store, summary, expires_at)
    _send(token, chat_id, text, keyboard)


def _confirmation_message(owner, payload, update_id, store=None, summary=None, expires_at=None):
    """Persist a confirmation (never a mutation) and return (text, keyboard)."""
    confirmation = (store or _store()).create_confirmation_once(
        owner, payload, expires_at or int(time.time()) + 300, str(update_id))
    names = {'book': 'Prenotazione', 'cancel': 'Cancellazione', 'waitlist_join': 'Ingresso in lista d’attesa',
             'waitlist_leave': 'Uscita dalla lista d’attesa', 'waitlist_confirm': 'Conferma dalla lista d’attesa',
             'weekly': 'Modifica programmazione settimanale', 'disable': 'Disabilitazione automazione'}
    labels = {'activity_id': 'ID attività', 'date': 'Data', 'appointment_id': 'ID appuntamento',
              'booking_id': 'ID impegno', 'days': 'Giorni', 'hour': 'Ora', 'minute': 'Minuti'}
    details = '\n'.join(f'{label}: {", ".join(payload[key]) if key == "days" else payload[key]}'
                        for key, label in labels.items() if key in payload)
    if summary:
        details = summary + '\n' + details
    return ('Confermi: ' + names[payload['action']] + '?\n' + details +
            '\nVerifica le conseguenze prima di procedere.',
            [[{'text': 'Conferma', 'callback_data': confirmation},
              {'text': 'Annulla', 'callback_data': 'cancel:' + confirmation}]])


TIMEZONE = 'Europe/Rome'
WEEKDAYS = ('Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom')
WAITLIST_NOTE = "Lezione al completo. Vuoi metterti in lista d'attesa?"
BUTTON_LIMIT = 20
CALLBACK_LIMIT = 64


def _today():
    return datetime.datetime.now(ZoneInfo(TIMEZONE)).date()


def _button(text, data):
    """Return a button, or None if the callback data would exceed Telegram's limit."""
    if len(data.encode('utf-8')) > CALLBACK_LIMIT:
        return None
    return {'text': text[:48], 'callback_data': data}


def _rows(buttons, width=1):
    buttons = [b for b in buttons if b]
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def _allowed_dates():
    today = _today()
    return [today + datetime.timedelta(days=offset) for offset in range(7)]


def _parse_flow_date(value):
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise ValueError('Data non valida')
    parsed = datetime.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    if parsed not in _allowed_dates():
        raise ValueError('Data fuori dai prossimi 7 giorni')
    return parsed


def _slot_action(slot):
    """Mirror the conditions enforced by SportrickClient.book and the worker."""
    if not isinstance(slot, dict) or slot.get('Type') != 'class':
        return None
    appointment_id = slot.get('AppointmentId')
    if not isinstance(appointment_id, str) or not appointment_id.isascii() or not appointment_id.isdigit():
        return None
    if (slot.get('Status') == 'Available' and slot.get('Notes') == 'Prenota'
            and slot.get('EventName') and slot.get('TimeStart')):
        return 'book'
    if slot.get('Notes') == WAITLIST_NOTE:
        return 'waitlist_join'
    return None


def _booking_actions(item):
    """Actions allowed by the portal flags; services are never offered."""
    if not isinstance(item, dict) or item.get('isService') is True:
        return []
    booking_id = item.get('id')
    if not isinstance(booking_id, str) or not booking_id.isascii() or not booking_id.isdigit():
        return []
    if item.get('isOnWaitlist') is True:
        return [action for action, flag in (('waitlist_leave', 'canDeleteFromWaitlist'),
                                            ('waitlist_confirm', 'canMoveToAttendants'))
                if item.get(flag) is True]
    return ['cancel'] if item.get('canDelete') is True else []


BOOKING_CALLBACKS = {'cancel': 'bc', 'waitlist_leave': 'bw', 'waitlist_confirm': 'bm'}
BOOKING_LABELS = {'cancel': 'Cancella', 'waitlist_leave': 'Esci dalla lista', 'waitlist_confirm': 'Conferma da lista'}


def _booking_summary(item):
    return ' '.join(str(item[key]) for key in ('description', 'DateStart', 'TimeStart') if item.get(key))


def _read_keyboard(action, items, sid=None):
    buttons = []
    if action == 'activities':
        for item in items:
            activity_id = item.get('id')
            if isinstance(activity_id, str) and activity_id.isascii() and activity_id.isdigit():
                buttons.append(_button('Prenota: ' + str(item.get('name') or activity_id), 'fa:' + activity_id))
    elif action == 'bookings':
        for item in items:
            for booking_action in _booking_actions(item):
                buttons.append(_button(f'{BOOKING_LABELS[booking_action]}: {_booking_summary(item) or item["id"]}',
                                       f'{BOOKING_CALLBACKS[booking_action]}:{item["id"]}:{_require_sid(sid)}'))
    return _rows(buttons[:BUTTON_LIMIT])


SID_PATTERN = re.compile(r'[0-9a-f]{12}')
ID_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
MAX_PACKED_ID = 24


def _pack_id(value):
    """Lossless, colon-free encoding of a numeric ID string (leading zeros preserved).

    The digits are prefixed with a sentinel "1" and written as a base-64 number, so
    ``_unpack_id(_pack_id(x)) == x`` for every string accepted by ``require_id``.
    """
    number, packed = int('1' + require_id(value)), ''
    while number:
        number, rest = divmod(number, 64)
        packed = ID_ALPHABET[rest] + packed
    return packed


def _unpack_id(token):
    """Inverse of _pack_id; rejects non-canonical or malformed tokens instead of guessing."""
    if (not isinstance(token, str) or not 1 <= len(token) <= MAX_PACKED_ID
            or any(char not in ID_ALPHABET for char in token)):
        raise ValueError('Identificativo non valido')
    number = 0
    for char in token:
        number = number * 64 + ID_ALPHABET.index(char)
    text = str(number)
    if not text.startswith('1'):
        raise ValueError('Identificativo non valido')
    value = require_id(text[1:])
    if _pack_id(value) != token:
        raise ValueError('Identificativo non valido')
    return value


def _require_sid(sid):
    if not isinstance(sid, str) or not SID_PATTERN.fullmatch(sid):
        raise ValueError('Identità schermata non valida')
    return sid


def _screen_id(owner, update_id):
    """Logical id of a read screen, derived from the update that requested it.

    It is stable across webhook redeliveries, worker retries and duplicate copies of the
    screen (different Telegram message ids), and different for every new request.
    It only keys deduplication: ids in callbacks are always re-verified against a fresh read.
    """
    if not isinstance(owner, str) or not owner or type(update_id) is not int or update_id < 0:
        raise ValueError('Update ID non valido')
    return hashlib.sha256(json.dumps(['screen', owner, update_id]).encode('utf-8')).hexdigest()[:12]


def _selection_id(owner, sid, base_callback):
    """Stable identity of one button on one logical screen (independent of message ids).

    Passed as ``update_id`` to ``create_confirmation_once``: any click on any copy of the
    screen returns the original token and keeps its original expiry.
    """
    if not isinstance(base_callback, str) or not base_callback or not isinstance(owner, str) or not owner:
        raise ValueError('Callback non valida')
    raw = json.dumps(['select', owner, _require_sid(sid), base_callback], ensure_ascii=True)
    return 'flow-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:40]


SELECTION_ID_PATTERN = re.compile(r'flow-[0-9a-f]{40}')
ASYNC_ACK = 'Sto recuperando i dati dal portale: ti scrivo appena sono pronti.'
READ_ERROR = 'Impossibile recuperare i dati richiesti.'


def _async_reads():
    return os.environ.get('BOT_ASYNC_READS') == '1' and bool(os.environ.get('BOT_WORKER_FUNCTION'))


def _dispatch_read(request, ui, chat_id):
    """Queue a read; the worker only renders and sends it (see render_ui)."""
    import boto3
    from botocore.config import Config
    response = boto3.client('lambda', config=Config(retries={'total_max_attempts': 1})).invoke(
        FunctionName=os.environ['BOT_WORKER_FUNCTION'], InvocationType='Event',
        Payload=json.dumps({'read': request, 'chat_id': str(chat_id), 'ui': ui}).encode())
    if response.get('StatusCode') != 202:
        raise RuntimeError('Dispatch failed')


def _read_request(ui):
    kind = ui['kind']
    if kind in ('activities', 'bookings'):
        return {'action': kind}
    if kind == 'booking':
        return {'action': 'bookings'}
    return {'action': 'schedule', 'activity_id': ui['activity_id'], 'date': ui['date']}


def _parse_ui(data, owner, update_id):
    """Map a callback to a serialisable UI descriptor, or None if it is not a read/flow callback."""
    parts = data.split(':')
    kind = parts[0]
    if data == 'read:activities':
        return {'kind': 'activities'}
    if data == 'read:bookings':
        return {'kind': 'bookings', 'sid': _screen_id(owner, update_id)}
    if kind == 'fd' and len(parts) == 3:
        return {'kind': 'day', 'activity_id': require_id(parts[1]), 'date': _parse_flow_date(parts[2]).isoformat(),
                'sid': _screen_id(owner, update_id)}
    if kind in ('gs', 'gw') and len(parts) == 5:
        # 'gs'/'gw' carry packed IDs; the old plain 'fs'/'fw' format is deliberately not parsed
        sid, base = _require_sid(parts[4]), ':'.join(parts[:4])
        return {'kind': 'slot', 'action': 'book' if kind == 'gs' else 'waitlist_join',
                'sel': _selection_id(owner, sid, base),
                'activity_id': _unpack_id(parts[1]), 'date': _parse_flow_date(parts[2]).isoformat(),
                'appointment_id': _unpack_id(parts[3])}
    if kind in ('bc', 'bw', 'bm') and len(parts) == 3:
        sid, base = _require_sid(parts[2]), ':'.join(parts[:2])
        return {'kind': 'booking', 'action': {v: k for k, v in BOOKING_CALLBACKS.items()}[kind],
                'sel': _selection_id(owner, sid, base), 'booking_id': require_id(parts[1])}
    return None


def _day_view(ui, slots):
    if not isinstance(slots, list):
        raise ValueError('Risultato non valido')
    day = datetime.date.fromisoformat(require_date(ui['date']))
    activity_id, compact, sid = ui['activity_id'], day.strftime('%Y%m%d'), _require_sid(ui.get('sid'))
    lines, buttons = [], []
    for slot in slots:
        action = _slot_action(slot)
        when = f'{slot.get("TimeStart", "?")} {slot.get("EventName", "")}'.strip() if isinstance(slot, dict) else '?'
        if action == 'book':
            lines.append(f'{when}: prenotabile')
            buttons.append(_button(f'Prenota {when}', 'gs:{}:{}:{}:{}'.format(
                _pack_id(activity_id), compact, _pack_id(slot['AppointmentId']), sid)))
        elif action == 'waitlist_join':
            lines.append(f'{when}: completa, lista d’attesa disponibile')
            buttons.append(_button(f'Lista d’attesa {when}', 'gw:{}:{}:{}:{}'.format(
                _pack_id(activity_id), compact, _pack_id(slot['AppointmentId']), sid)))
        else:
            lines.append(f'{when}: non prenotabile')
    title = f'{WEEKDAYS[day.weekday()]} {day.strftime("%d/%m/%Y")}'
    text = title + (':\n' + '\n'.join(lines) if lines else ': nessuno slot.')
    if any(b is None for b in buttons[:BUTTON_LIMIT]):
        text += '\nAlcuni identificativi sono troppo lunghi per un pulsante: usa /book o /waitlist_join.'
    elif len([b for b in buttons if b]) > BUTTON_LIMIT:
        text += f'\nMostrati solo i primi {BUTTON_LIMIT} pulsanti.'
    return text, _rows(buttons[:BUTTON_LIMIT]) + [[{'text': 'Indietro', 'callback_data': 'fa:' + activity_id}]]


def render_ui(ui, result, owner, store=None):
    """Pure renderer shared by webhook and worker: return (text, keyboard or None).

    May persist a confirmation through the store for slot/booking screens, never a mutation.
    Raises ValueError on a malformed descriptor or result.
    """
    if not isinstance(ui, dict) or not isinstance(result, dict) or not isinstance(owner, str) or not owner:
        raise ValueError('Descrittore non valido')
    kind = ui.get('kind')
    if kind in ('activities', 'bookings'):
        text = _format_read(kind, result)
        return text, _read_keyboard(kind, result[kind], ui.get('sid') if kind == 'bookings' else None) or None
    if kind == 'schedule':
        return _format_read('schedule', result), None
    if kind == 'day':
        require_id(ui.get('activity_id'))
        return _day_view(ui, result['schedule'])
    sel = ui.get('sel')
    if not isinstance(sel, str) or not SELECTION_ID_PATTERN.fullmatch(sel):
        raise ValueError('Identità selezione non valida')
    if kind == 'slot':
        action = ui.get('action')
        activity_id, appointment_id = require_id(ui.get('activity_id')), require_id(ui.get('appointment_id'))
        date = require_date(ui.get('date'))
        slot = next((s for s in result['schedule']
                     if isinstance(s, dict) and s.get('AppointmentId') == appointment_id), None)
        if action not in ('book', 'waitlist_join') or _slot_action(slot) != action:
            return 'Slot non più disponibile per questa operazione. Riapri la scelta del giorno.', None
        payload = {'action': action, 'activity_id': activity_id, 'date': date, 'appointment_id': appointment_id}
        summary = f'{slot.get("EventName", "")} {slot.get("TimeStart", "")}'.strip()
    elif kind == 'booking':
        action, booking_id = ui.get('action'), require_id(ui.get('booking_id'))
        item = next((b for b in result['bookings'] if isinstance(b, dict) and b.get('id') == booking_id), None)
        if action not in _booking_actions(item):
            return 'Operazione non consentita dal portale per questo impegno. Aggiorna /bookings.', None
        payload = {'action': action, 'booking_id': booking_id}
        summary = _booking_summary(item)
    else:
        raise ValueError('Descrittore non valido')
    return _confirmation_message(owner, payload, sel, store, summary)


def _run_ui(token, chat_id, owner, ui):
    request = _read_request(ui)
    if _async_reads():
        _dispatch_read(request, ui, chat_id)
        _send(token, chat_id, ASYNC_ACK)
        return
    text, keyboard = render_ui(ui, telegram_worker.read_action(request), owner)
    _send(token, chat_id, text, keyboard)


def _flow_callback(data, token, chat_id, owner, update_id=None):
    """Handle guided-flow callbacks; return False if data is not a flow callback."""
    parts = data.split(':')
    if parts[0] == 'fa' and len(parts) == 2:
        activity_id = require_id(parts[1])
        buttons = [_button(f'{WEEKDAYS[d.weekday()]} {d.strftime("%d/%m")}',
                           f'fd:{activity_id}:{d.strftime("%Y%m%d")}') for d in _allowed_dates()]
        keyboard = _rows(buttons, 2) + [[{'text': 'Indietro', 'callback_data': 'read:activities'}]]
        _send(token, chat_id, f'Attività {activity_id}: scegli il giorno ({TIMEZONE}).', keyboard)
        return True
    ui = _parse_ui(data, owner, update_id)
    if ui is None:
        if parts[0] in ('fd', 'fs', 'fw', 'gs', 'gw', 'bc', 'bw', 'bm'):
            _send(token, chat_id, 'Schermata obsoleta: riaprila dal menu. Nessuna operazione eseguita.')
            return True
        return False
    _run_ui(token, chat_id, owner, ui)
    return True


def _handle_callback(update, callback, token, owner):
    chat_id = callback['message']['chat']['id']
    callback_id = callback.get('id')
    if not isinstance(callback_id, str) or not callback_id:
        raise ValueError('Callback non valida')
    try:
        _telegram(token, 'answerCallbackQuery', {'callback_query_id': callback_id})
    except TelegramError:
        pass
    data = callback.get('data')
    if data in ('read:bookings', 'read:activities'):
        action = data.split(':')[1]
        _run_ui(token, chat_id, owner, _parse_ui(data, owner, update.get('update_id')))
        return
    if data in ('menu:automation', 'menu:help'):
        _handle_message({'chat': callback['message']['chat'], 'text': '/' + data.split(':')[1]}, token, owner)
        return
    if isinstance(data, str) and data.startswith('cancel:'):
        confirmation = data[len('cancel:'):]
        if not confirmation:
            raise ValueError('Callback non valida')
        cancelled = _cancel_confirmation(_store(), confirmation, owner)
        _send(token, chat_id, 'Conferma annullata.' if cancelled else 'Conferma non disponibile: già annullata o utilizzata.')
        return
    if isinstance(data, str) and _flow_callback(data, token, chat_id, owner, update.get('update_id')):
        return
    if not isinstance(data, str) or not data or type(update.get('update_id')) is not int:
        raise ValueError('Callback non valida')
    store = _store()
    try:
        job = store.consume_confirmation(data, owner, update['update_id'])
    except StoreConflict:
        _send(token, chat_id, 'Conferma non valida o scaduta.')
        return
    if not store.should_dispatch(job):
        if job and job.get('status') == 'running':
            _send(token, chat_id, 'Operazione in corso o esito incerto; verifica impegni.')
            return
        result = job.get('result') if job else None
        _send(token, chat_id, 'Operazione già ricevuta. Risultato: ' + json.dumps(result, ensure_ascii=False, default=str))
        return
    action = job.get('payload', {}).get('action')
    if action in MUTATIONS:
        _dispatch(data)
        _send(token, chat_id, 'Operazione accettata dal worker. Verifica gli impegni per conoscere l’esito.')
    elif action in ('weekly', 'disable'):
        try:
            job = store.claim_job(data)
        except StoreConflict:
            _send(token, chat_id, 'Operazione già ricevuta.')
            return
        try:
            payload = job['payload']
            scheduler = _scheduler()
            snapshot = scheduler.get_schedule()
            if fingerprint(snapshot) != payload['fingerprint']:
                raise ValueError('Schedule cambiata')
            if action == 'weekly':
                scheduler.set_weekly_time(snapshot, payload['days'], payload['hour'], payload['minute'])
            else:
                scheduler.disable(snapshot, confirmed=True)
            store.finish_job(data, 'completed', {'status': 'confirmed'})
        except Exception:
            try:
                store.finish_job(data, 'uncertain', {'status': 'uncertain'})
            except Exception:
                pass
            _send(token, chat_id, 'Esito incerto: verificare la programmazione prima di riprovare.')
        else:
            _send(token, chat_id, 'Programmazione aggiornata.')
    else:
        _send(token, chat_id, 'Operazione non riconosciuta.')


def lambda_handler(event, context):
    try:
        secret = get_secret('TELEGRAM_WEBHOOK_SECRET')
        token = get_secret('TELEGRAM_BOT_TOKEN')
    except Exception:
        return {'statusCode': 503, 'body': 'Not configured'}
    allowed_user = os.environ.get('TELEGRAM_ALLOWED_USER_ID')
    if not secret or not allowed_user or not token:
        return {'statusCode': 503, 'body': 'Not configured'}

    if not isinstance(event, dict) or not isinstance(event.get('headers'), dict):
        return {'statusCode': 400, 'body': 'Invalid request'}
    request_context = event.get('requestContext')
    http = request_context.get('http') if isinstance(request_context, dict) else None
    if not isinstance(http, dict) or http.get('method') != 'POST':
        return {'statusCode': 405, 'body': 'Method not allowed'}
    headers = event['headers']
    secrets = [value for key, value in headers.items()
               if isinstance(key, str) and key.lower() == 'x-telegram-bot-api-secret-token']
    try:
        valid_secret = (len(secrets) == 1 and isinstance(secrets[0], str)
                        and hmac.compare_digest(secrets[0].encode('utf-8'), secret.encode('utf-8')))
    except UnicodeError:
        valid_secret = False
    if not valid_secret:
        return {'statusCode': 403, 'body': 'Forbidden'}

    try:
        body = event['body']
        if not isinstance(body, str):
            raise ValueError('Invalid body')
        if len(body) > MAX_BODY_BYTES or len(body.encode('utf-8')) > MAX_BODY_BYTES:
            return {'statusCode': 413, 'body': 'Request too large'}
        if event.get('isBase64Encoded', False):
            body = base64.b64decode(body, validate=True).decode('utf-8')
        update = json.loads(body)
    except (KeyError, ValueError, UnicodeError, binascii.Error):
        return {'statusCode': 400, 'body': 'Invalid request'}

    if not isinstance(update, dict):
        return {'statusCode': 400, 'body': 'Invalid update'}
    if 'message' not in update and 'callback_query' not in update:
        return {'statusCode': 200, 'body': 'OK'}
    callback = update.get('callback_query')
    if 'callback_query' in update:
        if not isinstance(callback, dict) or not isinstance(callback.get('message'), dict):
            return {'statusCode': 403, 'body': 'Forbidden'}
        message = callback['message']
        sender = callback.get('from')
    elif not isinstance(update['message'], dict):
        return {'statusCode': 400, 'body': 'Invalid update'}
    else:
        message = update['message']
        sender = message.get('from')
    chat = message.get('chat')
    if (not isinstance(chat, dict) or chat.get('type') != 'private'
            or not isinstance(sender, dict) or type(sender.get('id')) is not int
            or str(sender['id']) != allowed_user or type(chat.get('id')) is not int
            or chat['id'] != sender['id']):
        return {'statusCode': 403, 'body': 'Forbidden'}

    try:
        if callback is not None:
            _handle_callback(update, callback, token, str(sender['id']))
        else:
            _handle_message(message, token, str(sender['id']), update.get('update_id'))
    except (ValueError, StoreConflict):
        return {'statusCode': 400, 'body': 'Invalid request or Telegram response'}
    except Exception:
        return {'statusCode': 502, 'body': 'Request failed'}
    return {'statusCode': 200, 'body': 'OK'}
