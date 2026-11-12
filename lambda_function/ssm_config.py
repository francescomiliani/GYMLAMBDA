"""Runtime secret lookup from SSM Parameter Store (SecureString).

`SSM_<NAME>` holds the parameter *name*; the value is never placed in os.environ
or logged. Without `SSM_<NAME>` the plain env var `<NAME>` is used (local/tests).
"""
import os

_cache = {}


class SecretError(RuntimeError):
    pass


def get_secret(name):
    parameter = os.environ.get('SSM_' + name)
    if not parameter:
        return os.environ.get(name)
    if parameter in _cache:
        return _cache[parameter]
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client('ssm', config=Config(connect_timeout=3, read_timeout=5,
                                                   retries={'total_max_attempts': 1}))
        value = client.get_parameter(Name=parameter, WithDecryption=True)['Parameter']['Value']
    except Exception:
        raise SecretError('Could not read secret from SSM') from None
    if not isinstance(value, str):
        raise SecretError('Could not read secret from SSM')
    _cache[parameter] = value
    return value
