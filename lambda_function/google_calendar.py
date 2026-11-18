"""Scrive gli impegni sul calendario Google. Se non può, restituisce un file ICS."""
import json
import os
from pathlib import Path

if __package__:
    from . import calendar_ics
else:
    import calendar_ics

TOKEN_URL = 'https://oauth2.googleapis.com/token'
EVENTS = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
REMINDERS = {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 12 * 60},
                                                {'method': 'popup', 'minutes': 60}]}


class CalendarError(Exception):
    pass


def load_credentials(path=None):
    """JSON with client_id, client_secret, refresh_token. Never log the value."""
    raw = None
    try:
        if __package__:
            from .ssm_config import get_secret
        else:
            from ssm_config import get_secret
        raw = get_secret('GOOGLE_CALENDAR')
    except Exception:
        raw = None
    file_path = Path(path) if path else Path(os.environ['GOOGLE_CALENDAR_FILE']) if os.environ.get('GOOGLE_CALENDAR_FILE') else None
    if not raw and file_path and file_path.is_file():
        try:
            raw = file_path.read_text()
        except OSError:
            raw = None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not all(data.get(key) for key in ('client_id', 'client_secret', 'refresh_token')):
        return None
    return data


def _requests():
    import requests
    return requests


def _token(creds, http):
    response = http.post(TOKEN_URL, data={
        'grant_type': 'refresh_token', 'client_id': creds['client_id'],
        'client_secret': creds['client_secret'], 'refresh_token': creds['refresh_token'],
    }, timeout=15)
    try:
        body = response.json()
    except ValueError:
        raise CalendarError('refresh failed') from None
    token = body.get('access_token') if isinstance(body, dict) else None
    if response.status_code != 200 or not token:
        raise CalendarError('refresh failed')
    return token


def _body(item, now):
    span = calendar_ics.window(item)
    if not span:
        raise CalendarError('not a class')
    start, end = span
    summary = item.get('description') or item.get('course') or item.get('EventName') or 'Palestra'
    return {
        'iCalUID': calendar_ics.uid(item),
        'summary': summary,
        'location': 'Reply Wellness',
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Europe/Rome'},
        'end': {'dateTime': end.isoformat(), 'timeZone': 'Europe/Rome'},
        'reminders': REMINDERS,
    }


def _state_key(item):
    key = item.get('id') or item.get('appointment_id') or item.get('booking_id')
    return 'gcal#' + str(key) if key else None


def _saved_id(store, item):
    key = _state_key(item)
    if not store or not key:
        return None
    try:
        saved = store.get_state(key)
    except Exception:
        return None
    return saved.get('event_id') if isinstance(saved, dict) else None


def _remember(store, item, event_id):
    key = _state_key(item)
    if not store or not key or not event_id:
        return
    try:
        store.put_state(key, {'event_id': event_id})
    except Exception:
        pass


def _import(item, creds, http, now):
    token = _token(creds, http)
    response = http.post(EVENTS + '/import', json=_body(item, now),
                         headers={'Authorization': 'Bearer ' + token}, timeout=15)
    try:
        body = response.json()
    except ValueError:
        raise CalendarError('import failed') from None
    event_id = body.get('id') if isinstance(body, dict) else None
    if response.status_code not in (200, 201) or not event_id:
        raise CalendarError('import failed')
    return event_id


def publish(item, store=None, http=None, now=None, path=None):
    """Write one class. channel is 'google', 'ics', or 'skipped'. Never raises."""
    import datetime
    now = now or datetime.datetime.now(calendar_ics.ROME)
    ics = calendar_ics.render([item], now)
    if calendar_ics.window(item) is None or not ics:
        return {'channel': 'skipped', 'ics': None}
    saved = _saved_id(store, item)
    if saved:
        return {'channel': 'google', 'event_id': saved, 'ics': None}
    creds = load_credentials(path)
    if not creds:
        return {'channel': 'ics', 'ics': ics, 'reason': 'unconfigured'}
    try:
        http = http or _requests()
        event_id = _import(item, creds, http, now)
    except Exception:
        return {'channel': 'ics', 'ics': ics, 'reason': 'failed'}
    _remember(store, item, event_id)
    return {'channel': 'google', 'event_id': event_id, 'ics': None}


def sync(items, store=None, http=None, now=None, path=None):
    """Align every still-open class. A partial failure returns an ICS of just those."""
    import datetime
    now = now or datetime.datetime.now(calendar_ics.ROME)
    chosen = calendar_ics.open_items(items, now)
    if not chosen:
        return {'channel': 'empty', 'count': 0, 'ics': None}
    failed, reasons, written = [], [], 0
    for item in chosen:
        outcome = publish(item, store, http, now, path)
        if outcome['channel'] == 'google':
            written += 1
        else:
            failed.append(item)
            reasons.append(outcome.get('reason'))
    if failed:
        reason = 'unconfigured' if reasons and all(item == 'unconfigured' for item in reasons) else 'failed'
        return {'channel': 'ics', 'count': written, 'ics': calendar_ics.render(failed, now),
                'failed': len(failed), 'reason': reason}
    return {'channel': 'google', 'count': written, 'ics': None}


def _find_id(booking_id, creds, http):
    token = _token(creds, http)
    response = http.get(EVENTS, params={'iCalUID': f'gymbooking-{booking_id}@gymbooking'},
                        headers={'Authorization': 'Bearer ' + token}, timeout=15)
    try:
        body = response.json()
    except ValueError:
        raise CalendarError('lookup failed') from None
    items = body.get('items') if isinstance(body, dict) else None
    if response.status_code != 200 or not items:
        return None
    return items[0].get('id')


def remove(booking_id, store=None, http=None, path=None):
    """Delete the Google event for this booking. 'deleted', 'missing', or 'failed'."""
    if not booking_id:
        return 'missing'
    event_id = None
    if store:
        try:
            saved = store.get_state('gcal#' + str(booking_id))
        except Exception:
            saved = None
        if isinstance(saved, dict):
            event_id = saved.get('event_id')
    creds = load_credentials(path)
    if not creds:
        return 'missing' if not event_id else 'failed'
    try:
        http = http or _requests()
        if not event_id:
            event_id = _find_id(booking_id, creds, http)
        if not event_id:
            return 'missing'
        token = _token(creds, http)
        response = http.delete(EVENTS + '/' + event_id, headers={'Authorization': 'Bearer ' + token}, timeout=15)
        if response.status_code not in (200, 204, 404, 410):
            return 'failed'
    except Exception:
        return 'failed'
    if store:
        try:
            store.put_state('gcal#' + str(booking_id), {})
        except Exception:
            pass
    return 'deleted'
