import copy
import json
import unittest
from decimal import Decimal
from unittest.mock import Mock

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from lambda_function.bot_scheduler import BotScheduler, ScheduleConflict, fingerprint


def schedule():
    return {
        "Name": "gym", "GroupName": "default", "Arn": "arn:example",
        "ScheduleExpression": "cron(0 8 ? * MON *)",
        "ScheduleExpressionTimezone": "UTC", "State": "DISABLED",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "Target": {"Arn": "arn:target", "RoleArn": "arn:role", "Input": "{}",
                   "RetryPolicy": {"MaximumRetryAttempts": 0},
                   "DeadLetterConfig": {"Arn": "arn:dlq"}},
        "Description": "gym", "KmsKeyArn": "arn:key",
        "ActionAfterCompletion": "NONE", "ResponseMetadata": {"RequestId": "one"},
    }


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = schedule()
        self.client = Mock()
        self.client.get_schedule.return_value = copy.deepcopy(self.snapshot)
        self.scheduler = BotScheduler(self.client, "gym")

    def test_weekly_update_preserves_all_fields_and_reads_back(self):
        expected = {k: copy.deepcopy(v) for k, v in self.snapshot.items()
                    if k not in ("Arn", "ResponseMetadata")}
        expected.update(ScheduleExpression="cron(30 9 ? * MON,FRI *)",
                        ScheduleExpressionTimezone="Europe/Rome")
        result = dict(expected, Arn="arn:example", ResponseMetadata={"RequestId": "two"})
        self.client.get_schedule.side_effect = [copy.deepcopy(self.snapshot), result]
        self.assertEqual(self.scheduler.set_weekly_time(self.snapshot, ["FRI", "MON"], 9, 30), result)
        self.client.update_schedule.assert_called_once_with(**expected)
        self.assertEqual(self.client.get_schedule.call_count, 2)

    def test_weekly_time_roundtrips_dynamodb_numbers(self):
        serializer, deserializer = TypeSerializer(), TypeDeserializer()
        payload = deserializer.deserialize(serializer.serialize({'hour': 9, 'minute': 30}))
        self.assertIsInstance(payload['hour'], Decimal)
        updated = dict(self.snapshot, ScheduleExpression="cron(30 9 ? * MON *)",
                       ScheduleExpressionTimezone="Europe/Rome")
        self.client.get_schedule.side_effect = [self.snapshot, updated]
        self.scheduler.set_weekly_time(self.snapshot, ['MON'], payload['hour'], payload['minute'])
        self.assertEqual(self.client.update_schedule.call_args.kwargs['ScheduleExpression'],
                         'cron(30 9 ? * MON *)')

    def test_weekly_rejects_invalid_dynamodb_time_values(self):
        serializer, deserializer = TypeSerializer(), TypeDeserializer()
        invalid = [True, '9', Decimal('1.5'), Decimal('-1'), Decimal('60'),
                   Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity')]
        for value in invalid:
            # DynamoDB's serializer itself rejects NaN and positive infinity.
            try:
                value = deserializer.deserialize(serializer.serialize(value))
            except TypeError:
                pass
            for position in ('hour', 'minute'):
                with self.subTest(value=value, position=position), self.assertRaises(ValueError):
                    hour, minute = (value, 0) if position == 'hour' else (0, value)
                    self.scheduler.set_weekly_time(self.snapshot, ['MON'], hour, minute)
        self.client.update_schedule.assert_not_called()

    def test_disable_requires_confirmation_and_preserves_configuration(self):
        with self.assertRaises(ValueError):
            self.scheduler.disable(self.snapshot, confirmed=1)
        self.client.get_schedule.assert_not_called()
        enabled = dict(self.snapshot, State="ENABLED")
        self.client.get_schedule.side_effect = [enabled, self.snapshot]
        self.scheduler.disable(enabled, confirmed=True)
        params = self.client.update_schedule.call_args.kwargs
        self.assertEqual(params["State"], "DISABLED")
        self.assertEqual(params["Target"], enabled["Target"])

    def test_changed_snapshot_fails_before_write(self):
        self.client.get_schedule.return_value["Target"]["Input"] = '{"changed":true}'
        with self.assertRaises(ScheduleConflict):
            self.scheduler.set_weekly_time(self.snapshot, ["MON"], 8, 0)
        self.client.update_schedule.assert_not_called()

    def test_enabled_schedule_rejects_weekly_changes_without_write(self):
        enabled = dict(self.snapshot, State="ENABLED")
        with self.assertRaises(ValueError):
            self.scheduler.set_weekly_time(enabled, ["MON"], 8, 0)
        self.client.get_schedule.assert_not_called()
        self.client.update_schedule.assert_not_called()

    def test_schedule_enabled_after_snapshot_rejects_weekly_changes(self):
        self.client.get_schedule.return_value["State"] = "ENABLED"
        with self.assertRaises(ScheduleConflict):
            self.scheduler.set_weekly_time(self.snapshot, ["MON"], 8, 0)
        self.client.update_schedule.assert_not_called()

    def guarded_schedule(self):
        value = copy.deepcopy(self.snapshot)
        value['ScheduleExpressionTimezone'] = 'Europe/Rome'
        value['Target'] = {
            'Arn': 'arn:guard', 'RoleArn': 'arn:role',
            'Input': json.dumps({'schedule_arn': 'arn:example',
                                 'scheduled_time': '<aws.scheduler.scheduled-time>'}),
            'RetryPolicy': {'MaximumEventAgeInSeconds': 300, 'MaximumRetryAttempts': 0},
        }
        return value

    def test_enable_preserves_configuration_and_reads_back(self):
        snapshot = self.guarded_schedule()
        enabled = dict(snapshot, State='ENABLED')
        self.client.get_schedule.side_effect = [copy.deepcopy(snapshot), enabled]
        self.assertEqual(self.scheduler.enable(snapshot, 'arn:guard', 'arn:example', confirmed=True), enabled)
        expected = {k: v for k, v in enabled.items() if k not in ('Arn', 'ResponseMetadata')}
        self.client.update_schedule.assert_called_once_with(**expected)

    def test_enable_invalid_configuration_never_writes(self):
        cases = []
        for path, value in [
            (('State',), 'ENABLED'), (('Arn',), 'arn:other'),
            (('ScheduleExpressionTimezone',), 'UTC'), (('FlexibleTimeWindow',), {'Mode': 'FLEXIBLE'}),
            (('Target', 'Arn'), 'arn:other'), (('Target', 'RoleArn'), ''),
            (('Target', 'Input'), '{}'), (('Target', 'Input'), '{invalid'),
            (('Target', 'Input'), '{"schedule_arn":"arn:example","schedule_arn":"arn:example",'
                                  '"scheduled_time":"<aws.scheduler.scheduled-time>"}'),
            (('Target', 'RetryPolicy', 'MaximumEventAgeInSeconds'), 301),
            (('Target', 'RetryPolicy', 'MaximumEventAgeInSeconds'), True),
            (('Target', 'RetryPolicy', 'MaximumRetryAttempts'), 1),
            (('Target', 'RetryPolicy', 'MaximumRetryAttempts'), None),
        ]:
            snapshot = self.guarded_schedule()
            current = snapshot
            for key in path[:-1]:
                current = current[key]
            current[path[-1]] = value
            cases.append(snapshot)
        missing = self.guarded_schedule()
        del missing['Target']['RetryPolicy']['MaximumEventAgeInSeconds']
        cases.append(missing)
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                self.scheduler.enable(snapshot, 'arn:guard', 'arn:example', confirmed=True)
        for guard, arn, confirmed in [('arn:guard', 'arn:example', False), ('', 'arn:example', True),
                                      ('arn:guard', '', True), ('arn:guard', 'arn:example', 1)]:
            with self.subTest(guard=guard, arn=arn, confirmed=confirmed), self.assertRaises(ValueError):
                self.scheduler.enable(self.guarded_schedule(), guard, arn, confirmed=confirmed)
        self.client.get_schedule.assert_not_called()
        self.client.update_schedule.assert_not_called()

    def test_enable_detects_snapshot_and_readback_conflicts(self):
        snapshot = self.guarded_schedule()
        self.client.get_schedule.return_value = dict(snapshot, Description='changed')
        with self.assertRaises(ScheduleConflict):
            self.scheduler.enable(snapshot, 'arn:guard', 'arn:example', confirmed=True)
        self.client.update_schedule.assert_not_called()
        self.client.get_schedule.side_effect = [snapshot, snapshot]
        with self.assertRaises(ScheduleConflict):
            self.scheduler.enable(snapshot, 'arn:guard', 'arn:example', confirmed=True)
        self.client.update_schedule.assert_called_once()

    def test_readback_conflict_is_reported(self):
        with self.assertRaises(ScheduleConflict):
            self.scheduler.set_weekly_time(self.snapshot, ["TUE"], 8, 0)
        self.client.update_schedule.assert_called_once()

    def test_invalid_inputs_and_unknown_fields_fail_closed(self):
        for days, hour, minute in [([], 1, 1), (["BAD"], 1, 1), (["MON", "MON"], 1, 1),
                                   (["MON"], True, 1), (["MON"], 24, 1), (["MON"], 1, 60)]:
            with self.subTest(days=days, hour=hour, minute=minute), self.assertRaises(ValueError):
                self.scheduler.set_weekly_time(self.snapshot, days, hour, minute)
        with self.assertRaises(ValueError):
            self.scheduler.disable(dict(self.snapshot, NewField="value"), confirmed=True)
        with self.assertRaises(ValueError):
            self.scheduler.disable(dict(self.snapshot, Name="other"), confirmed=True)
        self.client.update_schedule.assert_not_called()

    def test_errors_propagate_and_metadata_does_not_change_fingerprint(self):
        changed = dict(self.snapshot, ResponseMetadata={"RequestId": "different"})
        self.assertEqual(fingerprint(changed), fingerprint(self.snapshot))
        self.client.get_schedule.side_effect = RuntimeError("AWS failure")
        with self.assertRaisesRegex(RuntimeError, "AWS failure"):
            self.scheduler.disable(self.snapshot, confirmed=True)
        self.client.update_schedule.assert_not_called()


if __name__ == "__main__":
    unittest.main()
