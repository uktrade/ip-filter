import re
from schema import Optional
from schema import Or
from schema import Schema
from schema import SchemaError


def constant_time_is_equal(a, b):
    """To prevent timing attacks, check first that strings are of equal length
    and then perform bitwise constant time comparison operation, returning zero
    only if two values are equal."""
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a, b):
        result |= x ^ y
    return result == 0


CONFIG_SCHEMA = Schema(
    {
        "LOG_LEVEL": str,
        "CACHE_TYPE": str,
        "CACHE_DEFAULT_TIMEOUT": int,
        "DEBUG": bool,
        "ENVIRONMENT_NAME": str,
        "SERVER_PROTO": lambda proto: proto in ["http", "https"],
        "SERVER": str,
        "APPCONFIG_URL": lambda url: re.match(r"http(s)?://\w+(:\d+)?", url),
        "EMAIL_NAME": str,
        "EMAIL": str,
        "IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX": int,
        "IPFILTER_ENABLED": bool,
        "APPCONFIG_PROFILES": [str],
        "PUBLIC_PATHS": [str],
        "PROTECTED_PATHS": [str],
        Optional(str): object,
    }
)


def validate_config(config):
    return CONFIG_SCHEMA.validate(dict(config))

