import requests
import datetime
import os
import re
from zoneinfo import ZoneInfo

# Il runtime Lambda vive in UTC: la data va calcolata nel fuso della palestra,
# altrimenti tra mezzanotte e le 02:00 italiane si prenota il giorno sbagliato.
LOCAL_TZ = ZoneInfo('Europe/Rome')

login_url = 'https://ecomm.sportrick.com/replywellnessTO/Account/Login?returnUrl=%2FreplywellnessTO'
booking_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/BookAppointment'
activity_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/GetActivitySchedule'
booking_page_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/BookActivity?activityId=224715757956816031&activityFlags=Classes'
headers_login = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Referer': login_url
}
headers_booking = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
# headers_activity = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'  }
    # 'Referer':'https://ecomm.sportrick.com/replywellnessTO/Booking/BookActivity?activityId=224715757956816031&activityFlags=Classes'

def lambda_handler(event, context):
    event = event or {}
    username = os.environ.get('SPORTRICK_USERNAME')
    password = os.environ.get('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    body_login = {'UserName': username, 'Password': password}
    research_date = event.get('date') or (local_now() + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
    try:
        if event.get('inspect'):
            return inspect(body_login, research_date)
        book(body_login, research_date, event.get('time', '18:15'))
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError('Sportrick request or response failed') from None


def local_now():
    return datetime.datetime.now(LOCAL_TZ)


def login(session, body_login):
    response_login = session.post(login_url, headers=headers_login, data=body_login, allow_redirects=False, timeout=15)
    response_login.raise_for_status()


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


def book(body_login, research_date, start_time):
    with requests.Session() as session:
        login(session, body_login)
        daily_activities = schedule(session, research_date) or []
        entry = [d for d in daily_activities if d['TimeStart'] == start_time][0]
        # Una lezione al completo compare comunque in elenco: l'unico segnale
        # affidabile è Notes == 'Prenota', altrimenti il POST viene rifiutato.
        if entry.get('Notes') != 'Prenota':
            raise RuntimeError(f'{research_date} {start_time} non prenotabile: {entry.get("Notes")}')
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
