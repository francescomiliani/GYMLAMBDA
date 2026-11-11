import requests
import datetime
import os

login_url = 'https://ecomm.sportrick.com/replywellnessTO/Account/Login?returnUrl=%2FreplywellnessTO'
booking_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/BookAppointment'
activity_url = 'https://ecomm.sportrick.com/replywellnessTO/Booking/GetActivitySchedule'
headers_login = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Referer': login_url
}
headers_booking = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
# headers_activity = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'  }
    # 'Referer':'https://ecomm.sportrick.com/replywellnessTO/Booking/BookActivity?activityId=224715757956816031&activityFlags=Classes'

def lambda_handler(event, context):
    # Dizionario dei codici appointmentID per ciascun giorno della settimana

    today = datetime.datetime.now() + datetime.timedelta(days=7)
    weekday = today.weekday()  # weekday = 4
    research_date = today.strftime('%Y-%m-%d')
    print(f"TODAY: {today} WEEKDAY:{weekday}")

    username = os.environ.get('SPORTRICK_USERNAME')
    password = os.environ.get('SPORTRICK_PASSWORD')
    if not username or not password:
        raise RuntimeError('Sportrick credentials are not configured')
    body_login = {'UserName': username, 'Password': password}
    try:
        book(body_login, research_date)
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        raise RuntimeError('Sportrick request or response failed') from None


def book(body_login, research_date):
    with requests.Session() as session:
        # Effettua la login
        response_login = session.post(login_url, headers=headers_login, data=body_login, allow_redirects=False, timeout=15)
        response_login.raise_for_status()

        body_activity = {
            'currentDate': research_date,  # da sostituire
            'activityID': '224715757956816031',
            'skillID': 0,
            'activityFlags': 'Classes',
            'getForCatchUp': 'false'
        }

        response_activity = session.post(activity_url, headers=headers_booking, data=body_activity, timeout=15)
        response_activity.raise_for_status()
        daily_activities = response_activity.json().get('data') # lista di dizionari

        appointmentID = [d['AppointmentId'] for d in daily_activities if d['TimeStart'] == '18:15'][0]

        body_booking = {'appointmentID': appointmentID}
        # Effettua la prenotazione
        response_booking = session.post(booking_url, headers=headers_booking, data=body_booking, timeout=15)
        response_booking.raise_for_status()

        message = response_booking.json().get('message')
        if message == 'Prenotazione confermata con successo.':
            print(message)
        else:
            # Il messaggio del portale non è un segreto: serve a distinguere
            # "già prenotato" da "posti esauriti" senza indovinare.
            raise RuntimeError(f'Booking was not confirmed: {message}')

# Usa la funzione lambda_handler come punto di ingresso della Lambda
# lambda_handler("", "")
