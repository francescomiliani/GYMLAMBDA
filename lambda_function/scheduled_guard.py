"""At-most-once guard for an explicitly identified Scheduler occurrence."""
import datetime
import hashlib
import os
import re
from zoneinfo import ZoneInfo

if __package__:
    from . import app
else:
    import app

UTC = datetime.timezone.utc
MAX_DELAY = datetime.timedelta(minutes=5)
FUTURE_TOLERANCE = datetime.timedelta(minutes=1)
ARN = re.compile(r'arn:[a-z0-9-]+:scheduler:[a-z0-9-]+:\d{12}:schedule/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z')
TIME = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d\Z')


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
    date = (scheduled.astimezone(ZoneInfo('Europe/Rome')).date() + datetime.timedelta(days=7)).isoformat()
    import boto3
    from botocore.config import Config
    client = boto3.client('dynamodb', config=Config(connect_timeout=2, read_timeout=3,
                                                    retries={'total_max_attempts': 1}))
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

    try:
        result = app.lambda_handler({'date': date, 'time': time}, context)
    except Exception:
        try:
            record('uncertain')
        except Exception:
            pass
        raise
    record('completed')
    return result
