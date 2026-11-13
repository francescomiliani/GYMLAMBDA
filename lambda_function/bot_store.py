"""DynamoDB-backed confirmation and at-most-once worker state.

The table has a string partition key named ``pk``; configure TTL on ``purge_at``,
never on ``expires_at``. Records remain available at least seven days after
 expiry (or redelivery for its update); Telegram does not guarantee a retry window.
TTL is asynchronous and is not used to enforce expiry. On every delivery the
consumer dispatches only if should_dispatch(job); claim_job checks again
atomically. Never retry running jobs, even after a worker crash.
"""

import secrets
import time

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError


if __package__:
    from .store_conflict import StoreConflict
else:
    from store_conflict import StoreConflict


RETENTION_SECONDS = 7 * 24 * 60 * 60


class BotStore:
    def __init__(self, client, table_name, clock=time.time):
        self.client = client
        self.table_name = table_name
        self.clock = clock
        self.serializer = TypeSerializer()
        self.deserializer = TypeDeserializer()

    def _encode(self, item):
        return {key: self.serializer.serialize(value) for key, value in item.items()}

    def _get(self, key):
        response = self.client.get_item(TableName=self.table_name, Key=self._encode({'pk': key}), ConsistentRead=True)
        return {key: self.deserializer.deserialize(value) for key, value in response.get('Item', {}).items()} or None

    def create_confirmation(self, owner, payload, expires_at):
        """Persist an opaque token for owner and payload until Unix expires_at."""
        if not isinstance(owner, str) or not owner or not isinstance(payload, dict) or expires_at <= int(self.clock()):
            raise ValueError('Invalid confirmation')
        token = secrets.token_urlsafe(24)
        self.client.put_item(TableName=self.table_name, Item=self._encode({
            'pk': 'confirmation#' + token, 'owner': owner, 'payload': payload,
            'expires_at': int(expires_at), 'purge_at': int(expires_at) + RETENTION_SECONDS,
        }), ConditionExpression='attribute_not_exists(pk)')
        return token

    def create_confirmation_once(self, owner, payload, expires_at, update_id):
        """Create one confirmation per command update; return its original token.

        owner and update_id are nonempty strings. A redelivery must use the
        same owner and payload; its expires_at is ignored, even after expiry or
        consumption. Payload must identify the complete command and arguments.
        The mapping is retained through the original expiry plus seven days.
        After TTL deletion, deduplication is no longer guaranteed.
        """
        if (not isinstance(owner, str) or not owner or not isinstance(payload, dict)
                or not isinstance(update_id, str) or not update_id):
            raise ValueError('Invalid confirmation')

        def existing_token(update):
            if (update.get('kind') != 'confirmation' or update.get('owner') != owner
                    or update.get('payload') != payload):
                raise StoreConflict('Update already used')
            return update['token']

        update = self.get_update(update_id)
        if update:
            return existing_token(update)
        if expires_at <= int(self.clock()):
            raise ValueError('Invalid confirmation expiry')
        token = secrets.token_urlsafe(24)
        purge_at = int(expires_at) + RETENTION_SECONDS
        confirmation = {'pk': 'confirmation#' + token, 'owner': owner, 'payload': payload,
                        'expires_at': int(expires_at), 'purge_at': purge_at}
        update = {'pk': 'update#' + update_id, 'kind': 'confirmation', 'owner': owner,
                  'payload': payload, 'token': token, 'purge_at': purge_at}
        try:
            self.client.transact_write_items(TransactItems=[
                {'Put': {'TableName': self.table_name, 'Item': self._encode(item),
                         'ConditionExpression': 'attribute_not_exists(pk)'}}
                for item in (confirmation, update)
            ])
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'TransactionCanceledException':
                raise
            codes = {reason.get('Code') for reason in exc.response.get('CancellationReasons', [])} - {None, 'None'}
            if not codes or codes - {'ConditionalCheckFailed'}:
                raise
            update = self.get_update(update_id)
            if update:
                return existing_token(update)
            raise StoreConflict('Confirmation token collision') from exc
        return token

    def get_update(self, update_id):
        """Return the recorded update, or None (strongly consistent)."""
        return self._get('update#' + str(update_id))

    def get_job(self, job_id):
        """Return job state, payload and optional result, or None."""
        return self._get('job#' + job_id)

    def should_dispatch(self, job):
        """Check pending and unexpired before dispatch; claim still checks atomically."""
        return bool(job and job['status'] == 'pending' and job['expires_at'] > int(self.clock()))

    def _record_redelivery(self, update_id, job_id, purge_at):
        try:
            self.client.put_item(TableName=self.table_name,
                Item=self._encode({'pk': 'update#' + str(update_id), 'job_id': job_id,
                                   'purge_at': max(purge_at, int(self.clock()) + RETENTION_SECONDS)}),
                ConditionExpression='attribute_not_exists(pk)')
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'ConditionalCheckFailedException':
                raise
            update = self.get_update(update_id)
            if not update or update.get('job_id') != job_id:
                raise StoreConflict('Update already used') from exc
        return self.get_job(job_id)

    def consume_confirmation(self, token, owner, update_id):
        """Atomically consume a token and record update and pending job.

        Repeated clicks return the same job, even if expired. Dispatch only when
        should_dispatch returns true; claim_job is the authoritative check.
        StoreConflict denotes rejection.
        """
        confirmation = self._get('confirmation#' + token)
        if not confirmation or confirmation['owner'] != owner:
            raise StoreConflict('Invalid confirmation')
        existing_update = self.get_update(update_id)
        if existing_update and existing_update.get('job_id') != token:
            raise StoreConflict('Update already used')
        if confirmation.get('job_id'):
            return self._record_redelivery(update_id, confirmation['job_id'], confirmation['purge_at'])
        if existing_update or confirmation['expires_at'] <= int(self.clock()):
            raise StoreConflict('Expired or used confirmation')
        values = self._encode({':owner': owner, ':now': int(self.clock()), ':job': token})
        try:
            self.client.transact_write_items(TransactItems=[
                {'Update': {'TableName': self.table_name, 'Key': self._encode({'pk': 'confirmation#' + token}),
                            'UpdateExpression': 'SET job_id = :job',
                            'ConditionExpression': 'attribute_exists(pk) AND #owner = :owner AND expires_at > :now AND attribute_not_exists(job_id)',
                            'ExpressionAttributeNames': {'#owner': 'owner'}, 'ExpressionAttributeValues': values}},
                {'Put': {'TableName': self.table_name, 'Item': self._encode({'pk': 'update#' + str(update_id), 'job_id': token, 'purge_at': confirmation['purge_at']}),
                         'ConditionExpression': 'attribute_not_exists(pk)'}},
                {'Put': {'TableName': self.table_name, 'Item': self._encode({'pk': 'job#' + token, 'status': 'pending', 'payload': confirmation['payload'], 'expires_at': confirmation['expires_at'], 'purge_at': confirmation['purge_at']}),
                         'ConditionExpression': 'attribute_not_exists(pk)'}},
            ])
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'TransactionCanceledException':
                raise
            reasons = exc.response.get('CancellationReasons', [])
            codes = {reason.get('Code') for reason in reasons} - {None, 'None'}
            if not codes or codes - {'ConditionalCheckFailed'}:
                raise
            # Another delivery can have committed between our reads and write.
            confirmation = self._get('confirmation#' + token)
            update = self.get_update(update_id)
            if confirmation and confirmation.get('owner') == owner and confirmation.get('job_id') == token and (not update or update.get('job_id') == token):
                return self._record_redelivery(update_id, token, confirmation['purge_at'])
            raise StoreConflict('Confirmation or update already used or expired') from exc
        return self.get_job(token)

    def _transition(self, job_id, old, new, result=None, require_unexpired=False):
        values = {':old': old, ':new': new}
        expression = 'SET #status = :new'
        if result is not None:
            values[':result'] = result
            expression += ', #result = :result'
        names = {'#status': 'status'}
        condition = '#status = :old'
        if require_unexpired:
            values[':now'] = int(self.clock())
            condition += ' AND expires_at > :now'
        if result is not None:
            names['#result'] = 'result'
        try:
            response = self.client.update_item(TableName=self.table_name, Key=self._encode({'pk': 'job#' + job_id}),
                UpdateExpression=expression, ConditionExpression=condition,
                ExpressionAttributeNames=names, ExpressionAttributeValues=self._encode(values), ReturnValues='ALL_NEW')
        except ClientError as exc:
            if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise StoreConflict('Job state changed') from exc
            raise
        return {key: self.deserializer.deserialize(value) for key, value in response['Attributes'].items()}

    def claim_job(self, job_id):
        """Atomically claim an unexpired pending job; never reclaim running."""
        return self._transition(job_id, 'pending', 'running', require_unexpired=True)

    def finish_job(self, job_id, status, result):
        """Persist a running job's completed or uncertain outcome before notifying."""
        if status not in ('completed', 'uncertain'):
            raise ValueError('Invalid final status')
        return self._transition(job_id, 'running', status, result)
