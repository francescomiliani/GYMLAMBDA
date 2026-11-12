import os
import unittest
from unittest.mock import Mock, patch

from lambda_function import app, ssm_config, telegram_worker


class SsmConfigTests(unittest.TestCase):
    def setUp(self):
        ssm_config._cache.clear()
        self.addCleanup(ssm_config._cache.clear)

    def test_reads_decrypted_parameter_with_short_timeouts_and_caches(self):
        with patch.dict(os.environ, {'SSM_SPORTRICK_PASSWORD': '/gym/pw'}, clear=True), \
                patch('boto3.client') as factory:
            factory.return_value.get_parameter.return_value = {'Parameter': {'Value': 'pw-value'}}
            self.assertEqual(ssm_config.get_secret('SPORTRICK_PASSWORD'), 'pw-value')
            self.assertEqual(ssm_config.get_secret('SPORTRICK_PASSWORD'), 'pw-value')
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args, ('ssm',))
            config = factory.call_args.kwargs['config']
            self.assertEqual((config.connect_timeout, config.read_timeout), (3, 5))
            factory.return_value.get_parameter.assert_called_once_with(Name='/gym/pw', WithDecryption=True)
            self.assertNotIn('SPORTRICK_PASSWORD', os.environ)
            self.assertNotIn('pw-value', os.environ.values())

    def test_ssm_failure_raises_without_env_fallback_or_leak(self):
        env = {'SSM_TELEGRAM_BOT_TOKEN': '/gym/tok', 'TELEGRAM_BOT_TOKEN': 'plain-env'}
        with patch.dict(os.environ, env, clear=True), patch('boto3.client') as factory:
            factory.return_value.get_parameter.side_effect = RuntimeError('boom /gym/tok')
            with self.assertRaises(ssm_config.SecretError) as caught:
                ssm_config.get_secret('TELEGRAM_BOT_TOKEN')
            self.assertNotIn('plain-env', str(caught.exception))
            self.assertNotIn('/gym/tok', str(caught.exception))

    def test_env_fallback_when_ssm_not_configured(self):
        with patch.dict(os.environ, {'SPORTRICK_USERNAME': 'u'}, clear=True), patch('boto3.client') as factory:
            self.assertEqual(ssm_config.get_secret('SPORTRICK_USERNAME'), 'u')
            self.assertIsNone(ssm_config.get_secret('SPORTRICK_PASSWORD'))
            factory.assert_not_called()


class NonBlockingTests(unittest.TestCase):
    def setUp(self):
        ssm_config._cache.clear()
        self.addCleanup(ssm_config._cache.clear)

    def test_notification_survives_ssm_outage(self):
        env = {'SSM_TELEGRAM_BOT_TOKEN': '/gym/tok', 'TELEGRAM_CHAT_ID': '1'}
        with patch.dict(os.environ, env, clear=True), patch('boto3.client') as factory, \
                patch.object(app.requests, 'post') as post:
            factory.return_value.get_parameter.side_effect = RuntimeError('down')
            result = app.notify({'date': '2026-11-12', 'time': '18:15', 'course': 'X'})
            self.assertEqual(result['notifications'], {'telegram': 'failed'})
            post.assert_not_called()

    def test_worker_outcome_returned_when_token_lookup_fails(self):
        env = {'BOT_TABLE_NAME': 'jobs', 'SSM_TELEGRAM_BOT_TOKEN': '/gym/tok', 'TELEGRAM_CHAT_ID': '1'}
        with patch.dict(os.environ, env, clear=True), \
                patch('boto3.client') as factory, patch('lambda_function.bot_store.BotStore'), \
                patch.object(telegram_worker, 'run_job', return_value={'status': 'confirmed'}), \
                patch.object(telegram_worker.requests, 'post') as post:
            factory.return_value.get_parameter.side_effect = RuntimeError('down')
            self.assertEqual(telegram_worker.lambda_handler({'job_id': 'j'}, None), {'status': 'confirmed'})
            post.assert_not_called()

    def test_job_not_claimed_when_credentials_unavailable(self):
        store = Mock()
        env = {'SSM_SPORTRICK_USERNAME': '/gym/u', 'SPORTRICK_USERNAME': 'x', 'SPORTRICK_PASSWORD': 'y'}
        with patch.dict(os.environ, env, clear=True), patch('boto3.client') as factory:
            factory.return_value.get_parameter.side_effect = RuntimeError('down')
            with self.assertRaises(ssm_config.SecretError):
                telegram_worker.run_job('job', store)
        store.claim_job.assert_not_called()


if __name__ == '__main__':
    unittest.main()
