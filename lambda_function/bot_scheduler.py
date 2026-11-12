"""Restricted schedule editing; fingerprint checks are not atomic against any writers.

Concurrent bot jobs are not serialized; callers need an externally enforced lock.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal


_UPDATE_FIELDS = frozenset({
    "Name", "GroupName", "ScheduleExpression", "ScheduleExpressionTimezone",
    "StartDate", "EndDate", "FlexibleTimeWindow", "Target", "State",
    "Description", "KmsKeyArn", "ActionAfterCompletion",
})
_READ_ONLY_FIELDS = frozenset({
    "Arn", "CreationDate", "LastModificationDate", "ResponseMetadata",
})
_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


class ScheduleConflict(RuntimeError):
    """The schedule changed since it was read, or readback differs."""


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Unsupported schedule value: {type(value).__name__}")


def fingerprint(snapshot):
    """Fingerprint the complete schedule response, excluding SDK metadata."""
    data = {key: value for key, value in snapshot.items() if key != "ResponseMetadata"}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _time_part(value, maximum):
    if type(value) is int:
        number = value
    elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        if not 0 <= value <= maximum:
            raise ValueError("Invalid time")
        number = int(value)
    else:
        raise ValueError("Invalid time")
    if not 0 <= number <= maximum:
        raise ValueError("Invalid time")
    return number


class BotScheduler:
    def __init__(self, client, name, group_name="default"):
        if not isinstance(name, str) or not name or not isinstance(group_name, str) or not group_name:
            raise ValueError("Schedule name and group must be non-empty strings")
        self.client = client
        self.name = name
        self.group_name = group_name

    def get_schedule(self):
        return self.client.get_schedule(Name=self.name, GroupName=self.group_name)

    def _update(self, snapshot, changes):
        if not isinstance(snapshot, dict):
            raise ValueError("A schedule snapshot is required")
        unknown = snapshot.keys() - _UPDATE_FIELDS - _READ_ONLY_FIELDS
        if unknown:
            raise ValueError(f"Unsupported schedule fields: {sorted(unknown)}")
        if snapshot.get("Name") != self.name or snapshot.get("GroupName") != self.group_name:
            raise ValueError("Snapshot identifies a different schedule")
        if not {"ScheduleExpression", "FlexibleTimeWindow", "Target", "State"} <= snapshot.keys():
            raise ValueError("Incomplete schedule snapshot")
        if snapshot["State"] not in ("ENABLED", "DISABLED"):
            raise ValueError("Invalid schedule state")
        if fingerprint(self.get_schedule()) != fingerprint(snapshot):
            raise ScheduleConflict("Schedule changed since snapshot")
        params = {key: value for key, value in snapshot.items() if key in _UPDATE_FIELDS}
        params.update(changes)
        self.client.update_schedule(**params)
        current = self.get_schedule()
        if ({key: value for key, value in current.items() if key not in _READ_ONLY_FIELDS}
                != params):
            raise ScheduleConflict("Schedule readback differs from requested update")
        return current

    def set_weekly_time(self, snapshot, days, hour, minute):
        if not isinstance(snapshot, dict) or snapshot.get("State") != "DISABLED":
            raise ValueError("Weekly changes require a disabled schedule")
        if not isinstance(days, (list, tuple)) or not days or any(day not in _DAYS for day in days):
            raise ValueError("Days must be a non-empty list of weekday abbreviations")
        if len(set(days)) != len(days):
            raise ValueError("Duplicate weekdays")
        hour = _time_part(hour, 23)
        minute = _time_part(minute, 59)
        weekdays = ",".join(day for day in _DAYS if day in days)
        return self._update(snapshot, {
            "ScheduleExpression": f"cron({minute} {hour} ? * {weekdays} *)",
            "ScheduleExpressionTimezone": "Europe/Rome",
        })

    def disable(self, snapshot, *, confirmed=False):
        if confirmed is not True:
            raise ValueError("Disabling requires explicit confirmation")
        return self._update(snapshot, {"State": "DISABLED"})

    def enable(self, snapshot, expected_guard_arn, expected_schedule_arn, confirmed=False):
        """Prepare guarded activation; IAM, Lambda configuration and retries are not verified."""
        if (confirmed is not True or not isinstance(snapshot, dict)
                or not isinstance(expected_guard_arn, str) or not expected_guard_arn.strip()
                or not isinstance(expected_schedule_arn, str) or not expected_schedule_arn.strip()):
            raise ValueError("Confirmed activation and expected ARNs are required")
        target = snapshot.get("Target")
        window = snapshot.get("FlexibleTimeWindow")
        if (snapshot.get("State") != "DISABLED" or snapshot.get("Arn") != expected_schedule_arn
                or snapshot.get("ScheduleExpressionTimezone") != "Europe/Rome"
                or not isinstance(window, dict) or window.get("Mode") != "OFF"
                or not isinstance(target, dict) or target.get("Arn") != expected_guard_arn
                or not isinstance(target.get("RoleArn"), str) or not target["RoleArn"].strip()):
            raise ValueError("Schedule is not configured for guarded activation")
        retry = target.get("RetryPolicy")
        if (not isinstance(retry, dict) or type(retry.get("MaximumEventAgeInSeconds")) is not int
                or not 0 <= retry["MaximumEventAgeInSeconds"] <= 300
                or type(retry.get("MaximumRetryAttempts")) is not int
                or retry["MaximumRetryAttempts"] != 0):
            raise ValueError("Unsafe scheduler retry policy")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate payload key")
                result[key] = value
            return result

        try:
            payload = json.loads(target.get("Input"), object_pairs_hook=unique_object)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid guard payload") from exc
        if payload != {"schedule_arn": expected_schedule_arn,
                       "scheduled_time": "<aws.scheduler.scheduled-time>"}:
            raise ValueError("Invalid guard payload")
        return self._update(snapshot, {"State": "ENABLED"})
