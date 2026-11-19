"""Avvisa su Telegram quando una voce in lista d'attesa diventa una prenotazione.

Solo letture sul portale (login e impegni): non muta mai nulla. Lo stato `waitlist_seen` è
l'elenco delle voci in lista all'ultima lettura. Una voce che era in lista e ora compare tra gli
impegni confermati è stata promossa: si manda il messaggio, si scrive l'evento su Google Calendar
(verde) e si invia il file .ics. Le voci create dal sito web entrano da sole nell'elenco.
"""
import datetime
import os
from zoneinfo import ZoneInfo

import requests

if __package__:
    from . import google_calendar, telegram_bot
    from .bot_store import BotStore
    from .sportrick import SportrickClient
    from .ssm_config import get_secret
else:
    import google_calendar
    import telegram_bot
    from bot_store import BotStore
    from sportrick import SportrickClient
    from ssm_config import get_secret

STATE_KEY = 'waitlist_seen'
LOCAL_TZ = ZoneInfo('Europe/Rome')
SLIM_FIELDS = ('id', 'description', 'DateStart', 'TimeStart', 'TimeDuration')


def _key(item):
    return f"{item.get('DateStart')}|{item.get('TimeStart')}"


def _day(item):
    try:
        return datetime.datetime.strptime(item.get('DateStart') or '', '%d/%m/%Y').date()
    except ValueError:
        return None


def _slim(item, offered=False):
    entry = {field: item.get(field) for field in SLIM_FIELDS}
    entry['offered'] = offered
    return entry


def evaluate(previous, bookings, today):
    """Return (promoted, offers, seen).

    promoted: [(booking, other confirmed bookings the same day)]; offers: waiting entries the
    portal lets you confirm, once each; seen: the new list to persist. First run (previous is
    None) only records.
    """
    before = {_key(entry): entry for entry in previous or []}
    real = [item for item in bookings if item.get('isService') is not True]
    confirmed = [item for item in real if item.get('isOnWaitlist') is not True]
    by_key = {_key(item): item for item in confirmed}
    promoted = []
    for key in before:
        item = by_key.get(key)
        if item is not None:
            others = [other for other in confirmed
                      if other is not item and other.get('DateStart') == item.get('DateStart')]
            promoted.append((item, others))
    offers, seen = [], []
    for item in real:
        if item.get('isOnWaitlist') is not True or (_day(item) or today) < today:
            continue
        was_offered = bool(before.get(_key(item), {}).get('offered'))
        can_move = item.get('canMoveToAttendants') is True
        if can_move and not was_offered:
            offers.append(item)
        seen.append(_slim(item, offered=was_offered or can_move))
    return promoted, offers, seen


def _cancel_buttons(items, chat_id, now):
    sid = telegram_bot._screen_id(chat_id, int(now.timestamp()))
    buttons = []
    for item in items:
        booking_id = item.get('id')
        if item.get('canDelete') is True and isinstance(booking_id, str) and booking_id.isascii() and booking_id.isdigit():
            buttons.append(telegram_bot._button(f"🗑️ Cancella {item.get('TimeStart') or booking_id}", f'bc:{booking_id}:{sid}'))
    return telegram_bot._rows(buttons)


def _promotion_message(item, others, chat_id, now):
    text = f"🎉 Sei passato da lista d'attesa a partecipante: {telegram_bot._booking_summary(item)}."
    keyboard = None
    if others:
        text += ('\n⚠️ Hai un altro impegno lo stesso giorno: '
                 + ', '.join(telegram_bot._booking_summary(other) for other in others)
                 + ". Se non puoi frequentare entrambe, cancellane una: il posto serve ai colleghi in lista.")
        keyboard = _cancel_buttons([item, *others], chat_id, now) or None
    return text, keyboard


def _offer_message(item, chat_id, now):
    text = f"📣 Si è liberato un posto: {telegram_bot._booking_summary(item)}. Confermi dalla lista?"
    booking_id = item.get('id')
    sid = telegram_bot._screen_id(chat_id, int(now.timestamp()))
    button = telegram_bot._button('✅ Conferma da lista', f'bm:{booking_id}:{sid}')
    return text, telegram_bot._rows([button]) or None


def _store(table):
    import boto3
    return BotStore(boto3.client('dynamodb'), table)


def lambda_handler(event, context):
    table = os.environ.get('BOT_TABLE_NAME')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not table or not chat_id:
        raise RuntimeError('Waitlist watch is not configured')
    username = get_secret('SPORTRICK_USERNAME')
    password = get_secret('SPORTRICK_PASSWORD')
    token = get_secret('TELEGRAM_BOT_TOKEN')
    if not username or not password or not token:
        raise RuntimeError('Waitlist watch secrets are not configured')
    store = _store(table)
    saved = store.get_state(STATE_KEY)
    previous = saved.get('items') if isinstance(saved, dict) and isinstance(saved.get('items'), list) else None
    try:
        with requests.Session() as session:
            client = SportrickClient(session)
            client.login(username, password)
            bookings = client.bookings()
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print('Sportrick request failed:', type(exc).__name__)  # solo il tipo, mai il messaggio
        raise RuntimeError('Sportrick request failed') from None
    now = datetime.datetime.now(LOCAL_TZ)
    promoted, offers, seen = evaluate(previous, bookings, now.date())
    # Prima l'avviso, poi lo stato: se l'invio fallisce la voce resta in lista e si riprova
    # alla prossima lettura (al peggio un doppione, mai un avviso perso).
    for item, others in promoted:
        text, keyboard = _promotion_message(item, others, chat_id, now)
        telegram_bot._send(token, chat_id, text, keyboard)
        _after_promotion(item, store, token, chat_id)
    for item in offers:
        text, keyboard = _offer_message(item, chat_id, now)
        telegram_bot._send(token, chat_id, text, keyboard)
    store.put_state(STATE_KEY, {'items': seen})
    return {'status': 'ok', 'promoted': len(promoted), 'offered': len(offers), 'waiting': len(seen)}


def _after_promotion(item, store, token, chat_id):
    """Google Calendar (verde) e file .ics. Non fa mai fallire l'avviso già inviato."""
    try:
        outcome = google_calendar.publish(item, store)
        if outcome.get('ics'):
            telegram_bot._send_document(token, chat_id, 'palestra.ics', outcome['ics'],
                                        "Palestra. Un secondo import può duplicare l'evento.")
    except Exception:
        pass
