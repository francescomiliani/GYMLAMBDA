import unittest
from unittest.mock import Mock, patch

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from lambda_function.bot_store import BotStore, RETENTION_SECONDS, StoreConflict


def error(code, reasons=None):
    response = {'Error': {'Code': code, 'Message': code}}
    if reasons is not None:
        response['CancellationReasons'] = reasons
    return ClientError(response, 'test')


class FakeDynamo:
    def __init__(self):
        self.items = {}
        self.serializer = TypeSerializer()
        self.deserializer = TypeDeserializer()
        self.before_transaction = None

    def enc(self, item):
        return {k: self.serializer.serialize(v) for k, v in item.items()}

    def dec(self, item):
        return {k: self.deserializer.deserialize(v) for k, v in item.items()}

    def put_item(self, **kwargs):
        item = self.dec(kwargs['Item'])
        if item['pk'] in self.items:
            raise error('ConditionalCheckFailedException')
        self.items[item['pk']] = item

    def get_item(self, **kwargs):
        assert kwargs['ConsistentRead'] is True
        item = self.items.get(self.dec(kwargs['Key'])['pk'])
        return {'Item': self.enc(item)} if item else {}

    def transact_write_items(self, **kwargs):
        if self.before_transaction:
            callback, self.before_transaction = self.before_transaction, None
            callback()
        if len(kwargs['TransactItems']) == 2:
            items = [self.dec(entry['Put']['Item']) for entry in kwargs['TransactItems']]
            if any(item['pk'] in self.items for item in items):
                raise error('TransactionCanceledException', [{'Code': 'ConditionalCheckFailed'}])
            for item in items:
                self.items[item['pk']] = item
            return
        update, first, second = kwargs['TransactItems']
        operation = update['Update']
        key = self.dec(operation['Key'])['pk']
        values = self.dec(operation['ExpressionAttributeValues'])
        confirmation = self.items.get(key)
        items = [self.dec(first['Put']['Item']), self.dec(second['Put']['Item'])]
        if (not confirmation or confirmation['owner'] != values[':owner'] or
                confirmation['expires_at'] <= values[':now'] or 'job_id' in confirmation or
                any(item['pk'] in self.items for item in items)):
            raise error('TransactionCanceledException', [{'Code': 'ConditionalCheckFailed'}])
        confirmation['job_id'] = values[':job']
        for item in items:
            self.items[item['pk']] = item

    def update_item(self, **kwargs):
        item = self.items.get(self.dec(kwargs['Key'])['pk'])
        values = self.dec(kwargs['ExpressionAttributeValues'])
        if ':now' in values:
            assert 'expires_at > :now' in kwargs['ConditionExpression']
        if (not item or item['status'] != values[':old'] or
                (':now' in values and item['expires_at'] <= values[':now'])):
            raise error('ConditionalCheckFailedException')
        item['status'] = values[':new']
        if ':result' in values:
            item['result'] = values[':result']
        return {'Attributes': self.enc(item)}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDynamo()
        self.now = 100
        self.store = BotStore(self.db, 'table', lambda: self.now)
        self.token = self.store.create_confirmation('user', {'action': 'book'}, 110)

    def test_pending_redelivery_and_single_claim(self):
        job = self.store.consume_confirmation(self.token, 'user', 1)
        self.assertEqual(job['status'], 'pending')
        self.assertEqual(self.store.get_update(1)['job_id'], self.token)
        # Crash after commit and before dispatch: redelivery recovers pending.
        self.assertEqual(self.store.consume_confirmation(self.token, 'user', 1), job)
        self.assertEqual(self.store.consume_confirmation(self.token, 'user', 2), job)
        self.assertEqual(self.store.get_update(2)['job_id'], self.token)
        self.assertEqual(self.store.claim_job(self.token)['status'], 'running')
        with self.assertRaises(StoreConflict):
            self.store.claim_job(self.token)
        self.assertEqual(self.store.consume_confirmation(self.token, 'user', 1)['status'], 'running')
        self.assertEqual(self.store.finish_job(self.token, 'uncertain', {'message': 'timeout'})['status'], 'uncertain')
        with self.assertRaises(StoreConflict):
            self.store.claim_job(self.token)

    def test_concurrent_clicks_and_update_collision(self):
        self.db.before_transaction = lambda: self.store.consume_confirmation(self.token, 'user', 1)
        self.assertEqual(self.store.consume_confirmation(self.token, 'user', 1)['status'], 'pending')
        other = self.store.create_confirmation('user', {}, 110)
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(other, 'user', 1)

    def test_expiry_owner_and_completion(self):
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(self.token, 'other', 1)
        self.now = 110
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(self.token, 'user', 1)
        self.now = 100
        self.store.consume_confirmation(self.token, 'user', 1)
        self.store.claim_job(self.token)
        self.assertEqual(self.store.finish_job(self.token, 'completed', {'ok': True})['result'], {'ok': True})
        self.assertIsNone(self.store.get_job('missing'))

    def test_nonconditional_transaction_error_propagates(self):
        self.db.transact_write_items = Mock(side_effect=error('TransactionCanceledException', [{'Code': 'TransactionConflict'}]))
        with self.assertRaises(ClientError):
            self.store.consume_confirmation(self.token, 'user', 1)

    def test_expired_pending_redelivery_and_atomic_claim(self):
        job = self.store.consume_confirmation(self.token, 'user', 1)
        self.assertTrue(self.store.should_dispatch(job))
        self.now = 110
        redelivered = self.store.consume_confirmation(self.token, 'user', 2)
        self.assertEqual(redelivered['status'], 'pending')
        self.assertFalse(self.store.should_dispatch(redelivered))
        with self.assertRaises(StoreConflict):
            self.store.claim_job(self.token)
        self.assertEqual(self.store.get_job(self.token)['status'], 'pending')

    def test_retention_on_every_record_and_redelivery(self):
        job = self.store.consume_confirmation(self.token, 'user', 1)
        confirmation = self.db.items['confirmation#' + self.token]
        self.assertEqual(job['expires_at'], 110)
        for item in (confirmation, job, self.store.get_update(1)):
            self.assertEqual(item['purge_at'], 110 + RETENTION_SECONDS)
        self.now = 120
        self.store.consume_confirmation(self.token, 'user', 2)
        self.assertEqual(self.store.get_update(2)['purge_at'], 120 + RETENTION_SECONDS)
        self.assertEqual(self.store.consume_confirmation(self.token, 'user', 2), job)

    def test_mixed_transaction_failure_is_not_hidden_by_existing_job(self):
        original = self.db.transact_write_items
        def commit_then_fail(**kwargs):
            original(**kwargs)
            raise error('TransactionCanceledException', [{'Code': 'ConditionalCheckFailed'}, {'Code': 'TransactionConflict'}])
        self.db.transact_write_items = commit_then_fail
        with self.assertRaises(ClientError):
            self.store.consume_confirmation(self.token, 'user', 1)

    def test_command_redelivery_race_and_status(self):
        payload = {'action': 'book', 'id': '42'}
        self.db.before_transaction = lambda: self.store.create_confirmation_once('user', payload, 110, 'command')
        token = self.store.create_confirmation_once('user', payload, 111, 'command')
        self.assertEqual(self.store.get_update('command')['token'], token)
        confirmation = self.db.items['confirmation#' + token]
        self.assertEqual(confirmation['expires_at'], 110)
        self.assertEqual(confirmation['purge_at'], 110 + RETENTION_SECONDS)
        self.assertEqual(self.store.get_update('command')['purge_at'], confirmation['purge_at'])
        self.store.consume_confirmation(token, 'user', 'click')
        self.store.claim_job(token)
        self.now = 120
        self.assertEqual(self.store.create_confirmation_once('user', payload, 110, 'command'), token)
        self.assertEqual(self.store.get_job(token)['status'], 'running')
        with self.assertRaises(StoreConflict):
            self.store.claim_job(token)

    def test_command_collisions_and_expiry(self):
        payload = {'action': 'book'}
        token = self.store.create_confirmation_once('user', payload, 110, 'command')
        for owner, other in [('other', payload), ('user', {'action': 'cancel'})]:
            with self.assertRaises(StoreConflict):
                self.store.create_confirmation_once(owner, other, 110, 'command')
        with self.assertRaises(StoreConflict):
            self.store.consume_confirmation(token, 'user', 'command')
        self.store.consume_confirmation(token, 'user', 'click')
        with self.assertRaises(StoreConflict):
            self.store.create_confirmation_once('user', payload, 110, 'click')
        with patch('lambda_function.bot_store.secrets.token_urlsafe', return_value=token):
            with self.assertRaises(StoreConflict):
                self.store.create_confirmation_once('user', payload, 110, 'new')
        self.now = 110
        with self.assertRaises(ValueError):
            self.store.create_confirmation_once('user', payload, 110, 'expired')
        self.assertEqual(self.store.create_confirmation_once('user', payload, 110, 'command'), token)

    def test_command_transaction_errors_propagate(self):
        for reasons in ([{'Code': 'TransactionConflict'}], [],
                        [{'Code': 'ConditionalCheckFailed'}, {'Code': 'TransactionConflict'}]):
            self.db.transact_write_items = Mock(side_effect=error('TransactionCanceledException', reasons))
            with self.assertRaises(ClientError):
                self.store.create_confirmation_once('user', {}, 110, 'command')


if __name__ == '__main__':
    unittest.main()
