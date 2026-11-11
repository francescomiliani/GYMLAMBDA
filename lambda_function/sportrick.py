import datetime
import re
from html.parser import HTMLParser

import requests


BASE = 'https://ecomm.sportrick.com/replywellnessTO'


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.activities = []
        self.activity = None
        self.field = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'seleziona-attivita' in attrs.get('class', '').split():
            if not attrs.get('data-idattivita') or not attrs.get('data-flags'):
                raise ValueError('Attività senza identificativo o tipo')
            self.activity = {'id': attrs['data-idattivita'], 'flags': attrs['data-flags'], 'name': '', 'description': ''}
        elif self.activity and tag in ('h3', 'p'):
            classes = attrs.get('class', '').split()
            self.field = 'name' if 'list-group-item-heading' in classes else 'description' if 'list-group-item-text' in classes else None
            self.depth = 1 if self.field else 0
        elif self.field:
            self.depth += 1

    def handle_data(self, data):
        if self.field:
            self.activity[self.field] += data

    def handle_endtag(self, tag):
        if self.field:
            self.depth -= 1
            if self.depth == 0:
                self.field = None
        if tag == 'a' and self.activity:
            self.activity['name'] = ' '.join(self.activity['name'].split())
            self.activity['description'] = ' '.join(self.activity['description'].split())
            if not self.activity['name']:
                raise ValueError('Attività senza nome')
            self.activities.append(self.activity)
            self.activity = None


class HiddenInputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.extra_input = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('select', 'textarea') or (tag == 'input' and attrs.get('type', '').lower() not in ('hidden', 'submit', 'button')):
            self.extra_input = True
        if tag == 'input' and attrs.get('type', '').lower() == 'hidden':
            for key in (attrs.get('id'), attrs.get('name')):
                if key:
                    self.values[key] = attrs.get('value', '')


def require_id(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError('Identificativo non valido: usare una stringa numerica')
    return value


def require_date(value):
    if not isinstance(value, str):
        raise ValueError('Data non valida')
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError('Data non valida: usare YYYY-MM-DD') from None
    if parsed.isoformat() != value:
        raise ValueError('Data non valida: usare YYYY-MM-DD')
    return value


class SportrickClient:
    def __init__(self, session):
        self.session = session

    def login(self, username, password):
        url = BASE + '/Account/Login?returnUrl=%2FreplywellnessTO'
        page = self.session.get(url, timeout=15)
        page.raise_for_status()
        parser = HiddenInputParser()
        parser.feed(page.text)
        token = parser.values.get('__RequestVerificationToken')
        if not token:
            raise RuntimeError('Pagina di accesso Sportrick non valida')
        r = self.session.post(url, data={'UserName': username, 'Password': password, '__RequestVerificationToken': token}, headers={'Referer': url}, allow_redirects=False, timeout=15)
        r.raise_for_status()
        if r.status_code not in (302, 303) or not r.headers.get('Location') or 'Account/Login' in r.headers['Location']:
            raise RuntimeError('Accesso Sportrick non riuscito')

    def get(self, path, **kwargs):
        r = self.session.get(BASE + path, timeout=15, **kwargs)
        r.raise_for_status()
        if isinstance(r.url, str) and '/Account/Login' in r.url:
            raise RuntimeError('Sessione Sportrick non autenticata')
        return r

    def activities(self):
        parser = CatalogParser()
        html = self.get('/Booking/ChooseActivity').text
        parser.feed(html)
        if not parser.activities and not re.search(r'<title>\s*ChooseActivity\s*-\s*Sportrick SocialBooking\s*</title>', html, re.I):
            raise RuntimeError('Pagina delle attività non riconosciuta')
        return parser.activities

    def schedule(self, activity_id, date):
        require_id(activity_id)
        require_date(date)
        activity = next((x for x in self.activities() if x['id'] == activity_id), None)
        if not activity:
            raise ValueError('Attività non presente nel catalogo')
        data = {'currentDate': date, 'activityID': activity_id, 'skillID': 0, 'activityFlags': activity['flags'], 'getForCatchUp': 'false'}
        r = self.session.post(BASE + '/Booking/GetActivitySchedule', data=data, timeout=15)
        r.raise_for_status()
        result = r.json()
        if result.get('status') != 'OK' or not isinstance(result.get('data'), list):
            raise RuntimeError('Calendario non disponibile')
        fields = ('AppointmentId', 'TimeStart', 'TimeDuration', 'Notes', 'Status', 'Type', 'EventName', 'ServiceId', 'StartDateTime', 'ParentCourseId')
        ids = ('AppointmentId', 'ServiceId', 'ParentCourseId')
        return [{k: str(item[k]) if k in ids and item[k] is not None else item[k] for k in fields if k in item} for item in result['data']]

    def bookings(self):
        result = self.get('/Booking/GetCustomerAppointments').json()
        if result.get('status') != 'OK' or not isinstance(result.get('data'), list):
            raise RuntimeError('Impegni non disponibili')
        fields = ('id', 'appointmentId', 'activityId', 'DateStart', 'TimeStart', 'TimeDuration', 'description', 'isOnWaitlist', 'isService', 'canDelete', 'canDeleteFromWaitlist', 'canMoveToAttendants')
        ids = ('id', 'appointmentId', 'activityId')
        return [{k: str(item[k]) if k in ids and item[k] is not None else item[k] for k in fields if k in item} for item in result['data']]

    def mutate(self, endpoint, data):
        try:
            r = self.session.post(BASE + endpoint, data=data, timeout=15)
            r.raise_for_status()
            result = r.json()
            if not isinstance(result, dict):
                return {'status': 'uncertain'}
            status = {'OK': 'confirmed', 'ERROR': 'rejected'}.get(result.get('status'), 'uncertain')
            return {'status': status, 'message': result.get('message') or result.get('error_message')}
        except (requests.RequestException, ValueError):
            return {'status': 'uncertain'}

    def cancel(self, booking_id):
        require_id(booking_id)
        booking = next((x for x in self.bookings() if x.get('id') == booking_id), None)
        if not booking or booking.get('canDelete') is not True or booking.get('isOnWaitlist') is True:
            raise ValueError('Prenotazione non cancellabile')
        if not all(booking.get(k) for k in ('description', 'DateStart', 'TimeStart')):
            raise ValueError('Dati della prenotazione incompleti')
        result = self.mutate('/Booking/DeleteBooking', {'id': booking_id})
        result.update(course=booking['description'], date=booking['DateStart'], time=booking['TimeStart'])
        return result

    def waitlist_leave(self, booking_id):
        return self.mutate('/Booking/RemoveFromWaitlist', {'bookingId': require_id(booking_id)})

    def waitlist_confirm(self, booking_id):
        return self.mutate('/Booking/MoveToAttendants', {'appointmentId': require_id(booking_id)})

    def waitlist_join(self, appointment_id):
        require_id(appointment_id)
        r = self.get('/Booking/ConfirmActivityBooking', params={'appointmentID': appointment_id})
        parser = HiddenInputParser()
        parser.feed(r.text)
        waitlist_id = parser.values.get('idImpegno')
        if not waitlist_id or 'btnAggiungiListaAttesa' not in r.text:
            raise ValueError('Lista d’attesa non disponibile per questo appuntamento')
        return self.mutate('/Booking/ConfirmAddToWaitList', {'appointmentId': require_id(waitlist_id)})

    def book(self, activity_id, date, appointment_id):
        require_id(appointment_id)
        slot = next((x for x in self.schedule(activity_id, date) if x.get('AppointmentId') == appointment_id), None)
        if not slot or slot.get('Type') != 'class' or slot.get('Status') != 'Available' or slot.get('Notes') != 'Prenota':
            raise ValueError('Appuntamento non prenotabile come lezione')
        if not slot.get('EventName') or not slot.get('TimeStart'):
            raise ValueError('Dati della lezione incompleti')
        page = self.get('/Booking/ConfirmActivityBooking', params={'appointmentID': appointment_id})
        parser = HiddenInputParser()
        parser.feed(page.text)
        if parser.values.get('appointmentID') != appointment_id or parser.extra_input or 'btnConfirmAppointmentBooking' not in page.text:
            raise ValueError('La prenotazione richiede una conferma non supportata')
        try:
            r = self.session.post(BASE + '/Booking/BookAppointment', data={'appointmentID': appointment_id}, timeout=15)
            r.raise_for_status()
            result = r.json()
            if not isinstance(result, dict):
                return {'status': 'uncertain'}
            message = result.get('message')
            if message == 'Prenotazione confermata con successo.':
                return {'status': 'confirmed', 'message': message, 'course': slot.get('EventName'), 'date': date, 'time': slot.get('TimeStart')}
            if result.get('status') == 'ERROR':
                return {'status': 'rejected', 'message': message}
            return {'status': 'uncertain', 'message': message}
        except (requests.RequestException, ValueError):
            return {'status': 'uncertain'}
