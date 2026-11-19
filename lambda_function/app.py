import requests
import datetime
import os
import re
from zoneinfo import ZoneInfo

if __package__:
    from .sportrick import WAITLIST_NOTE, SportrickClient, require_date, require_id
    from .ssm_config import get_secret
else:
    from sportrick import WAITLIST_NOTE, SportrickClient, require_date, require_id
    from ssm_config import get_secret

# Il runtime Lambda vive in UTC: la data va calcolata nel fuso della palestra,
# altrimenti tra mezzanotte e le 02:00 italiane si prenota il giorno sbagliato.
LOCAL_TZ = ZoneInfo('Europe/Rome')

booking_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/BookAppointment'
activity_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/GetActivitySchedule'
booking_page_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/BookActivity?activityId=224715757956816031&activityFlags=Classes'
headers_booking = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
# headers_activity = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'  }
    # 'Referer':'https://ecomm.sportrick.com/replywellnessTO/Booking/BookActivity?activityId=224715757956816031&activityFlags=Classes'

def lambda_handler(event, context):
    event = event or {}
    if 'action' in event:
        return dispatch(event)
    username = get_secret('SPORTRICK_USERNAME')
    password = get_secret('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    body_login = {'UserName': username, 'Password': password}
    research_date = event.get('date') or (local_now() + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    # Fallback spento di default: si attiva solo passando fallback_times nel payload.
    times = [event.get('time', '18:15'), *(event.get('fallback_times') or [])]
    try:
        if event.get('inspect'):
            return inspect(body_login, research_date)
        return notify(book(body_login, research_date, times, waitlist=event.get('waitlist') is True))
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError('Sportrick request or response failed') from None


def dispatch(event):
    action = event['action']
    fields = {
        'activities': (), 'bookings': (), 'schedule': ('activity_id', 'date'),
        'book': ('activity_id', 'date', 'appointment_id'),
        'cancel': ('booking_id',), 'waitlist_join': ('appointment_id',),
        'waitlist_leave': ('booking_id',), 'waitlist_confirm': ('booking_id',),
    }
    if action not in fields:
        raise ValueError('Azione non riconosciuta')
    args = []
    for name in fields[action]:
        value = event.get(name)
        args.append(require_date(value) if name == 'date' else require_id(value))
    username = get_secret('SPORTRICK_USERNAME')
    password = get_secret('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    try:
        with requests.Session() as session:
            client = SportrickClient(session)
            client.login(username, password)
            result = getattr(client, action)(*args)
            if action in ('book', 'cancel') and result.get('status') == 'confirmed':
                result = notify(result, action)
            return {action: result} if action in ('activities', 'schedule', 'bookings') else result
    except requests.RequestException:
        raise RuntimeError('Sportrick request failed') from None


def local_now():
    return datetime.datetime.now(LOCAL_TZ)


def _calendar_store():
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


def _calendar_side(result, action):
    """Google first. Returns (suffix for the text, ICS or None). Never raises."""
    try:
        if __package__:
            from . import google_calendar
        else:
            import google_calendar
        if action == 'cancel':
            outcome = google_calendar.remove(result.get('booking_id') or result.get('id'), _calendar_store())
            if outcome == 'failed':
                return " L'evento potrebbe essere ancora su Google Calendar.", None
            return '', None
        if action != 'book':
            return '', None
        written = google_calendar.publish(result, _calendar_store())
        result['calendar'] = written.get('channel')
        return '', written.get('ics')
    except Exception:
        return '', None


# La notifica non deve mai far fallire una prenotazione già confermata.
def notify(result, action='book'):
    label = 'cancellazione confermata' if action == 'cancel' else 'prenotazione confermata'
    waitlisted = action == 'book' and result.get('status') == 'waitlisted'
    if waitlisted:
        # Solo lista d'attesa: nessun impegno, quindi niente evento in calendario.
        label = "in lista d'attesa"
        suffix, ics = '', None
    else:
        suffix, ics = _calendar_side(result, action)
    message = f"{result['date']} {result['time']} - {result.get('course') or 'SALA ATTREZZI'} - {label}" + suffix
    wait = result.get('waitlist')
    if action == 'book' and not waitlisted and isinstance(wait, dict):
        message += {
            'confirmed': f" Sono anche in lista d'attesa per le {wait.get('time')}: ti avviso se si libera un posto.",
            'rejected': f" Lista d'attesa per le {wait.get('time')} non riuscita.",
        }.get(wait.get('status'), f" Lista d'attesa per le {wait.get('time')} con esito incerto: verifica gli impegni.")
    notifications = {}
    topic = os.environ.get('SNS_TOPIC_ARN')
    if topic:
        try:
            import boto3  # import locale: la notifica è opzionale, il resto no
            config = boto3.session.Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1})
            boto3.client('sns', config=config).publish(
                TopicArn=topic,
                Subject=f'GymBooking: {label}',
                Message=message,
            )
            notifications['sns'] = 'accepted'
        except Exception:
            notifications['sns'] = 'failed'
    try:
        token = get_secret('TELEGRAM_BOT_TOKEN')
    except Exception:
        token = None
        if os.environ.get('SSM_TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID'):
            notifications['telegram'] = 'failed'
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if token or chat_id:
        notifications['telegram'] = 'failed'
        if token and chat_id:
            try:
                if ics:
                    try:
                        requests.post(
                            f'https://api.telegram.org/bot{token}/sendDocument',
                            data={'chat_id': chat_id, 'caption': 'Palestra. Un secondo import può duplicare l\'evento.'},
                            files={'document': ('palestra.ics', ics.encode(), 'text/calendar')}, timeout=15)
                    except Exception:
                        pass
                response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat_id, 'text': message}, timeout=15)
                response.raise_for_status()
                if response.json().get('ok') is True:
                    notifications['telegram'] = 'accepted'
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                pass
    result['notifications'] = notifications
    return result


def login(session, body_login):
    SportrickClient(session).login(body_login['UserName'], body_login['Password'])


def schedule(session, research_date):
    body_activity = {
        'currentDate': research_date,
        'activityID': '224715757956816031',
        'skillID': 0,
        'activityFlags': 'Classes',
        'getForCatchUp': 'false'
    }
    response_activity = session.post(activity_url, headers=headers_booking, data=body_activity, timeout=15)
    response_activity.raise_for_status()
    return response_activity.json().get('data')


# Sola lettura: elenca gli slot del giorno e i campi nascosti che il form di
# prenotazione si aspetta, così il POST non va più indovinato.
def inspect(body_login, research_date):
    with requests.Session() as session:
        login(session, body_login)
        entries = schedule(session, research_date) or []
        slots = [(d.get('TimeStart'), d.get('AppointmentId')) for d in entries]
        print(f'{research_date} slots: {slots}')
        # Campi completi: due slot dello stesso giorno possono avere flag diversi.
        print(f'slot detail: {[d for d in entries if d.get("TimeStart") in ("07:00", "08:00")]}')
        print(f'bookable: {[(d.get("TimeStart"), d.get("Notes")) for d in entries]}')
        page = session.get(booking_page_url, timeout=15)
        page.raise_for_status()
        actions = re.findall(r'<form[^>]*action="([^"]*)"', page.text, re.I)
        hidden = re.findall(r'<input[^>]*type="hidden"[^>]*>', page.text, re.I)
        print(f'form actions: {actions}')
        names = [re.search(r'name="([^"]*)"', h) for h in hidden]
        print(f'hidden input names: {[m.group(1) for m in names if m]}')
        return {'date': research_date, 'slots': slots, 'local_now': str(local_now())}


def _join_waitlist(session, entry):
    """Lista d'attesa su uno slot pieno, nella sessione già autenticata. Mai solleva."""
    start_time = entry.get('TimeStart')
    try:
        result = SportrickClient(session).waitlist_join(str(entry['AppointmentId']))
    except ValueError:
        return {'time': start_time, 'status': 'rejected'}
    except (requests.RequestException, RuntimeError):
        return {'time': start_time, 'status': 'uncertain'}
    print(f'{start_time}: lista d\'attesa {result.get("status")}')
    return {'time': start_time, 'status': result.get('status') or 'uncertain'}


def book(body_login, research_date, times, waitlist=False):
    """Prenota il primo orario libero di `times`.

    Con waitlist=True, se la prima scelta è piena entra anche nella sua lista d'attesa:
    prima il posto libero (più scarso), poi la lista. Senza posto libero ma con lista
    confermata l'esito è 'waitlisted'.
    """
    with requests.Session() as session:
        login(session, body_login)
        daily_activities = schedule(session, research_date) or []
        # Una lezione al completo compare comunque in elenco: l'unico segnale
        # affidabile è Notes == 'Prenota', altrimenti il POST viene rifiutato.
        # Il fallback prova solo gli slot prenotabili, senza mai mandare un POST a vuoto.
        rejected = []
        entry = None
        for start_time in times:
            candidate = ([d for d in daily_activities if d['TimeStart'] == start_time] or [None])[0]
            note = candidate.get('Notes') if candidate else 'orario non in calendario'
            if note == 'Prenota':
                entry = candidate
                break
            rejected.append(f'{start_time} ({note})')
        first = ([d for d in daily_activities if d['TimeStart'] == times[0]] or [None])[0]
        wait_entry = first if (waitlist and first is not entry and first.get('Notes') == WAITLIST_NOTE
                               and first.get('AppointmentId')) else None
        if entry is None and wait_entry is None:
            raise RuntimeError(f'{research_date} non prenotabile: {", ".join(rejected)}')

        booked = booking_error = None
        if entry is not None:
            body_booking = {'appointmentID': entry['AppointmentId']}
            response_booking = session.post(booking_url, headers=headers_booking, data=body_booking, timeout=15)
            response_booking.raise_for_status()

            message = response_booking.json().get('message')
            if message == 'Prenotazione confermata con successo.':
                print(f'{research_date} {start_time}: {message}')
                booked = {'date': research_date, 'time': start_time, 'message': message}
            else:
                # Il messaggio del portale non è un segreto: serve a distinguere
                # "già prenotato" da "posti esauriti" senza indovinare.
                booking_error = f'Booking was not confirmed: {message}'
        joined = _join_waitlist(session, wait_entry) if wait_entry is not None else None
        if booked:
            if joined:
                booked['waitlist'] = joined
            return booked
        if joined and joined['status'] == 'confirmed':
            return {'status': 'waitlisted', 'date': research_date, 'time': joined['time'], 'waitlist': joined,
                    'message': booking_error or f'Nessun posto libero: {", ".join(rejected)}'}
        if booking_error:
            raise RuntimeError(booking_error)
        if joined and joined['status'] == 'uncertain':
            raise RuntimeError(f'{research_date}: lista d\'attesa con esito incerto')
        raise RuntimeError(f'{research_date} non prenotabile: {", ".join(rejected)}')

# Usa la funzione lambda_handler come punto di ingresso della Lambda
# lambda_handler("", "")
