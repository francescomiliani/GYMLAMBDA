import json
import os

import requests

if __package__:
    from .sportrick import SportrickClient, require_date, require_id
    from .ssm_config import get_secret
else:
    from sportrick import SportrickClient, require_date, require_id
    from ssm_config import get_secret


def outcome_text(result):
    status = result.get('status') if isinstance(result, dict) else None
    emoji, label = {'confirmed': ('✅', 'confermato'), 'rejected': ('🚫', 'rifiutato')}.get(status, ('⚠️', 'incerto'))
    return f'{emoji} Esito operazione: {label}'


def read_action(event):
    if not isinstance(event, dict):
        raise ValueError('Evento non valido')
    action = event.get('action')
    if not isinstance(action, str) or action not in ('activities', 'bookings', 'schedule'):
        raise ValueError('Azione non riconosciuta')
    args = ()
    if action == 'schedule':
        args = (require_id(event.get('activity_id')), require_date(event.get('date')))

    username = get_secret('SPORTRICK_USERNAME')
    password = get_secret('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    try:
        with requests.Session() as session:
            client = SportrickClient(session)
            client.login(username, password)
            return {action: getattr(client, action)(*args)}
    except Exception:
        raise RuntimeError('Sportrick request or response failed') from None


def run_job(job_id, store):
    """Claim and execute a single mutation at most once."""
    # Credenziali prima del claim: se mancano o SSM è giù il job resta non reclamato.
    username = get_secret('SPORTRICK_USERNAME')
    password = get_secret('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    record = store.claim_job(job_id)
    try:
        payload = record['payload']
        action = payload.get('action')
        if action not in ('book', 'cancel', 'waitlist_join', 'waitlist_leave', 'waitlist_confirm'):
            raise ValueError('Azione non riconosciuta')
        if action in ('book', 'waitlist_join'):
            activity_id = require_id(payload.get('activity_id'))
            date = require_date(payload.get('date'))
            appointment_id = require_id(payload.get('appointment_id'))
        else:
            booking_id = require_id(payload.get('booking_id'))

        with requests.Session() as session:
            client = SportrickClient(session)
            client.login(username, password)
            if action == 'book':
                result = client.book(activity_id, date, appointment_id)
            elif action == 'cancel':
                result = client.cancel(booking_id)
            elif action == 'waitlist_join':
                slot = next((item for item in client.schedule(activity_id, date)
                             if item.get('AppointmentId') == appointment_id), None)
                if not slot or slot.get('Type') != 'class' or slot.get('Notes') != "Lezione al completo. Vuoi metterti in lista d'attesa?":
                    raise ValueError('Lista d’attesa non disponibile')
                result = client.waitlist_join(appointment_id)
            else:
                booking = next((item for item in client.bookings() if item.get('id') == booking_id), None)
                flag = 'canDeleteFromWaitlist' if action == 'waitlist_leave' else 'canMoveToAttendants'
                if not booking or booking.get('isOnWaitlist') is not True or booking.get(flag) is not True:
                    raise ValueError('Operazione sulla lista d’attesa non disponibile')
                result = getattr(client, action)(booking_id)
        status = 'completed' if isinstance(result, dict) and result.get('status') in ('confirmed', 'rejected') else 'uncertain'
        if not isinstance(result, dict):
            result = {'status': 'uncertain'}
    except Exception:
        status, result = 'uncertain', {'status': 'uncertain'}
    try:
        store.finish_job(job_id, status, result)
    except Exception:
        raise RuntimeError('Could not persist job outcome') from None
    return result


def lambda_handler(event, context):
    if isinstance(event, dict) and 'read' in event:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        try:
            token = get_secret('TELEGRAM_BOT_TOKEN')
        except Exception:
            return {'status': 'unauthorized'}
        if not chat_id or not token or event.get('chat_id') != chat_id:
            return {'status': 'unauthorized'}
        document = None
        try:
            result = read_action(event['read'])
            keyboard = None
            if 'ui' in event:
                ui = event['ui']
                if not isinstance(ui, dict) or ui.get('kind') not in ('activities', 'bookings', 'schedule', 'day', 'slot', 'booking', 'calendar'):
                    raise ValueError('Descrittore non valido')
                if __package__:
                    from . import telegram_bot
                else:
                    import telegram_bot
                if telegram_bot._read_request(ui) != event['read']:
                    raise ValueError('Lettura non coerente')
                if ui['kind'] in ('slot', 'booking') and not os.environ.get('BOT_TABLE_NAME'):
                    raise RuntimeError('Bot table is not configured')
                document = None
                if ui['kind'] == 'calendar':
                    text, document = telegram_bot.calendar_view(result.get('bookings'), _state_store())
                    keyboard = None
                else:
                    text, keyboard = telegram_bot.render_ui(ui, result, chat_id)
                if not isinstance(text, str) or not text or (keyboard is not None and not isinstance(keyboard, list)):
                    raise ValueError('Rendering non valido')
            else:
                text = json.dumps(result, ensure_ascii=False, indent=2)
            status = 'completed'
        except Exception:
            text = '😕 Impossibile recuperare i dati richiesti.'
            keyboard = None
            document = None
            status = 'error'
        try:
            for start in range(0, len(text), 4000):
                message = {'chat_id': chat_id, 'text': text[start:start + 4000]}
                if keyboard and start + 4000 >= len(text):
                    message['reply_markup'] = {'inline_keyboard': keyboard}
                response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                         json=message, timeout=15)
                response.raise_for_status()
                if response.json().get('ok') is not True:
                    return {'status': 'delivery_failed'}
            if document:
                telegram_bot._send_document(token, chat_id, 'palestra.ics', document, text[:1024])
        except Exception:
            return {'status': 'delivery_failed'}
        return {'status': status}

    import boto3
    if __package__:
        from .bot_store import BotStore, StoreConflict
    else:
        from bot_store import BotStore, StoreConflict

    job_id = event.get('job_id') if isinstance(event, dict) else None
    if not isinstance(job_id, str) or not job_id:
        raise ValueError('Invalid job ID')
    table = os.environ.get('BOT_TABLE_NAME')
    if not table:
        raise RuntimeError('Bot table is not configured')
    store = BotStore(boto3.client('dynamodb'), table)
    try:
        result = run_job(job_id, store)
    except StoreConflict:
        return {'status': 'already_claimed'}

    try:
        token = get_secret('TELEGRAM_BOT_TOKEN')
    except Exception:
        token = None
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if token and chat_id:
        try:
            note = _calendar_after_job(result, store, token, chat_id)
            text = outcome_text(result) + (('\n' + note) if note else '')
            response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                     json={'chat_id': chat_id, 'text': text}, timeout=15)
            response.raise_for_status()
        except Exception:
            pass
    return result


def _state_store():
    table = os.environ.get('BOT_TABLE_NAME')
    if not table:
        return None
    try:
        import boto3
        if __package__:
            from .bot_store import BotStore
        else:
            from bot_store import BotStore
        return BotStore(boto3.client('dynamodb'), table)
    except Exception:
        return None


def _calendar_after_job(result, store, token, chat_id):
    """Write or delete the Google event. An ICS file is only the fallback. Never raises."""
    if not isinstance(result, dict) or result.get('status') != 'confirmed':
        return ''
    try:
        if __package__:
            from . import google_calendar, telegram_bot
        else:
            import google_calendar
            import telegram_bot
        if result.get('booking_id'):
            if google_calendar.remove(result.get('booking_id'), store) == 'failed':
                return "L'evento potrebbe essere ancora su Google Calendar."
            return ''
        if not (result.get('date') or result.get('DateStart')):
            return ''
        outcome = google_calendar.publish(result, store)
        if outcome.get('ics'):
            telegram_bot._send_document(token, chat_id, 'palestra.ics', outcome['ics'],
                                        'Palestra. Un secondo import può duplicare l\'evento.')
    except Exception:
        return ''
    return ''
