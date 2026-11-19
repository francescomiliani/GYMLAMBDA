"""At-most-once guard for an explicitly identified Scheduler occurrence."""
import datetime
import hashlib
import os
import re
from zoneinfo import ZoneInfo

if __package__:
    from . import app, timetable
else:
    import app
    import timetable

UTC = datetime.timezone.utc
MAX_DELAY = datetime.timedelta(minutes=5)
FUTURE_TOLERANCE = datetime.timedelta(minutes=1)
ARN = re.compile(r'arn:[a-z0-9-]+:scheduler:[a-z0-9-]+:\d{12}:schedule/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')
TIME = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d\Z')


def class_dates(scheduled):
    """Class day for this occurrence: the same weekday seven days later.

    The schedule fires every day at 00:00 Europe/Rome. The portal opens a class
    exactly seven days ahead, so each day only the class that just opened is
    tried, and only if its weekday is in the preferences (otherwise no claim).
    """
    local = scheduled.astimezone(ZoneInfo('Europe/Rome'))
    return [local.date() + datetime.timedelta(days=7)]


def lambda_handler(event, context):
    expected = os.environ.get('BOT_SCHEDULE_ARN', '')
    table = os.environ.get('BOT_TABLE_NAME', '')
    time = os.environ.get('BOT_BOOKING_TIME', '')
    if not expected or not ARN.fullmatch(expected) or not table or not TIME.fullmatch(time):
        raise ValueError('Scheduler guard is not configured')
    if not isinstance(event, dict) or set(event) != {'schedule_arn', 'scheduled_time'}:
        raise ValueError('Invalid scheduler event')
    arn, timestamp = event['schedule_arn'], event['scheduled_time']
    if not isinstance(arn, str) or arn != expected:
        raise ValueError('Unauthorized schedule ARN')
    if not isinstance(timestamp, str) or not TIMESTAMP.fullmatch(timestamp):
        raise ValueError('Invalid scheduled time')
    try:
        scheduled = datetime.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError('Invalid scheduled time') from None
    now = datetime.datetime.now(UTC)
    if scheduled < now - MAX_DELAY or scheduled > now + FUTURE_TOLERANCE:
        raise ValueError('Scheduler occurrence outside allowed window')
    remaining = getattr(context, 'get_remaining_time_in_millis', None)
    if remaining is not None and remaining() < 120_000:
        raise RuntimeError('Insufficient time for scheduler guard')

    # Credenziali (cache SSM) prima del claim irreversibile dell'occorrenza.
    if not app.get_secret('SPORTRICK_USERNAME') or not app.get_secret('SPORTRICK_PASSWORD'):
        raise RuntimeError('Sportrick credentials are not configured')

    key = 'schedule#' + hashlib.sha256((arn + '\n' + scheduled.isoformat()).encode()).hexdigest()
    dates = [day.isoformat() for day in class_dates(scheduled)]
    import boto3
    from botocore.config import Config
    client = boto3.client('dynamodb', config=Config(connect_timeout=2, read_timeout=3,
                                                    retries={'total_max_attempts': 1}))
    # Preferenze e salti si valutano prima del claim: un'occorrenza saltata non consuma nulla.
    # Un errore di lettura interrompe qui, senza prenotare (meglio una prenotazione mancata).
    if __package__:
        from .bot_store import BotStore
    else:
        from bot_store import BotStore
    state = BotStore(client, table)
    # Interruttore /waitlist: acceso salvo un \`enabled: false\` esplicito; letto prima del claim come le preferenze.
    waitlist = (state.get_state('waitlist_auto') or {}).get('enabled') is not False
    plans = []
    for date in dates:
        if (state.get_state('skip#' + date) or {}).get('skipped'):
            continue
        times = timetable.resolve_times(state.get_state('prefs'), date, time)
        if times:
            plans.append((date, times))
    if not plans:
        if len(dates) == 1 and (state.get_state('skip#' + dates[0]) or {}).get('skipped'):
            return {'status': 'skipped', 'reason': 'date skipped'}
        if len(dates) == 1:
            return {'status': 'skipped', 'reason': 'no preference for this weekday'}
        return {'status': 'skipped', 'reason': 'nothing to book'}
    try:
        client.put_item(TableName=table, Item={
            'pk': {'S': key}, 'status': {'S': 'claimed'}, 'started': {'N': str(int(now.timestamp()))},
            'purge_at': {'N': str(int(now.timestamp()) + 8 * 86400)},
        }, ConditionExpression='attribute_not_exists(pk)')
    except Exception as exc:
        if getattr(exc, 'response', {}).get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            item = client.get_item(TableName=table, Key={'pk': {'S': key}}, ConsistentRead=True).get('Item', {})
            status = item.get('status', {}).get('S')
            return {'status': 'duplicate', 'outcome': 'completed' if status == 'completed' else 'verify'}
        raise
    def record(status):
        client.update_item(TableName=table, Key={'pk': {'S': key}},
                           UpdateExpression='SET #s = :s',
                           ConditionExpression='attribute_exists(pk)',
                           ExpressionAttributeNames={'#s': 'status'},
                           ExpressionAttributeValues={':s': {'S': status}})

    def last_run(status):
        try:  # solo per /status: non deve mai influire sull'esito
            state.put_state('last_run', {'at': now.astimezone(ZoneInfo('Europe/Rome')).strftime('%d/%m %H:%M'),
                                         'date': ', '.join(date for date, _ in plans), 'status': status})
        except Exception:
            pass

    results = []
    for date, times in plans:
        event = {'date': date, 'time': times[0]}
        if waitlist:
            event['waitlist'] = True
        if len(times) > 1:
            event['fallback_times'] = times[1:]
        try:
            results.append(app.lambda_handler(event, context))
        except RuntimeError:
            # Una data sola resta incerta, come prima. Con tre date uno slot chiuso non blocca le altre.
            if len(plans) == 1:
                try:
                    record('uncertain')
                except Exception:
                    pass
                last_run('uncertain')
                raise
            results.append({'date': date, 'status': 'rejected'})
        except Exception:
            try:
                record('uncertain')
            except Exception:
                pass
            last_run('uncertain')
            raise
    record('completed')
    last_run('completed')
    return results[0] if len(results) == 1 else {'status': 'completed', 'results': results}
