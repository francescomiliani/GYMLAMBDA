import requests
import datetime
import os
import re
from zoneinfo import ZoneInfo

if __package__:
    from .sportrick import SportrickClient, require_date, require_id
else:
    from sportrick import SportrickClient, require_date, require_id

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
    username = os.environ.get('SPORTRICK_USERNAME')
    password = os.environ.get('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    body_login = {'UserName': username, 'Password': password}
    research_date = event.get('date') or (local_now() + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    # Fallback spento di default: si attiva solo passando fallback_times nel payload.
    times = [event.get('time', '18:15'), *(event.get('fallback_times') or [])]
    try:
        if event.get('inspect'):
            return inspect(body_login, research_date)
        return notify(book(body_login, research_date, times))
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
    username = os.environ.get('SPORTRICK_USERNAME')
    password = os.environ.get('SPORTRICK_PASSWORD')
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


# La notifica non deve mai far fallire una prenotazione già confermata.
def notify(result, action='book'):
    label = 'cancellazione confermata' if action == 'cancel' else 'prenotazione confermata'
    message = f"{result['date']} {result['time']} - {result.get('course') or 'SALA ATTREZZI'} - {label}"
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
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if token or chat_id:
        notifications['telegram'] = 'failed'
        if token and chat_id:
            try:
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


def book(body_login, research_date, times):
    with requests.Session() as session:
        login(session, body_login)
        daily_activities = schedule(session, research_date) or []
        # Una lezione al completo compare comunque in elenco: l'unico segnale
        # affidabile è Notes == 'Prenota', altrimenti il POST viene rifiutato.
        # Il fallback prova solo gli slot prenotabili, senza mai mandare un POST a vuoto.
        rejected = []
        for start_time in times:
            entry = ([d for d in daily_activities if d['TimeStart'] == start_time] or [None])[0]
            note = entry.get('Notes') if entry else 'orario non in calendario'
            if note == 'Prenota':
                break
            rejected.append(f'{start_time} ({note})')
        else:
            raise RuntimeError(f'{research_date} non prenotabile: {", ".join(rejected)}')
        appointmentID = entry['AppointmentId']

        body_booking = {'appointmentID': appointmentID}
        response_booking = session.post(booking_url, headers=headers_booking, data=body_booking, timeout=15)
        response_booking.raise_for_status()

        message = response_booking.json().get('message')
        if message == 'Prenotazione confermata con successo.':
            print(f'{research_date} {start_time}: {message}')
            return {'date': research_date, 'time': start_time, 'message': message}
        # Il messaggio del portale non è un segreto: serve a distinguere
        # "già prenotato" da "posti esauriti" senza indovinare.
        raise RuntimeError(f'Booking was not confirmed: {message}')

# Usa la funzione lambda_handler come punto di ingresso della Lambda
# lambda_handler("", "")
