import logging
import os
import sys

from config import Environ

env = Environ(os.environ)

LOG_LEVEL = env.get("LOG_LEVEL", "INFO")

logging.basicConfig(stream=sys.stdout, level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Flask-Caching related configs
CACHE_TYPE = "SimpleCache"
CACHE_DEFAULT_TIMEOUT = 300
DEBUG = env.bool("DEBUG", default=False)
ENVIRONMENT_NAME = env["COPILOT_ENVIRONMENT_NAME"]

SERVER_PROTO = env.get("SERVER_PROTO", "http")
SERVER = env["SERVER"]

APPCONFIG_URL = env.get("APPCONFIG_URL", "http://localhost:2772")

EMAIL_NAME = env.get("EMAIL_NAME", "DBT")
EMAIL = env["EMAIL"]

IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX = env.int(
    "IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", default=-2
)

# These settings can be overridden per environment, e.g. if $COPILOT_ENVIRONMENT is set to "staging", then
# $STAGING_IPFITER_ENABLED will take precedence over $IPFILTER_ENABLED.
IPFILTER_ENABLED = env.bool(
    "IPFILTER_ENABLED", default=True, allow_environment_override=True
)
APPCONFIG_PROFILES = env.list(
    "APPCONFIG_PROFILES", default=[], allow_environment_override=True
)
PUBLIC_PATHS = env.list("PUBLIC_PATHS", default=[], allow_environment_override=True)
PROTECTED_PATHS = env.list(
    "PROTECTED_PATHS", default=[], allow_environment_override=True
)
PUB_HOST_LIST = env.list("PUB_HOST_LIST", default=[], allow_environment_override=True)
PRIV_HOST_LIST = env.list("PRIV_HOST_LIST", default=[], allow_environment_override=True)

ENABLE_XRAY = env.bool("ENABLE_XRAY", default=False)

if ENABLE_XRAY:
    OTEL_PROPAGATORS = env.get("OTEL_PROPAGATORS")
    OTEL_PYTHON_ID_GENERATOR = env.get("OTEL_PYTHON_ID_GENERATOR")
    OTEL_SERVICE_NAME = env.get("OTEL_SERVICE_NAME")
    OTEL_METRICS_EXPORTER = env.get("OTEL_METRICS_EXPORTER")
    OTEL_TRACES_EXPORTER = env.get("OTEL_TRACES_EXPORTER")
    OTEL_TRACES_SAMPLER = env.get("OTEL_TRACES_SAMPLER")
    OTEL_TRACES_SAMPLER_ARG = env.get("OTEL_TRACES_SAMPLER_ARG")

ADDITIONAL_IP_LIST = env.list(
    "ADDITIONAL_IP_LIST", default=[], allow_environment_override=True
)
