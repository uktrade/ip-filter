from collections import UserDict
from ipaddress import ip_network
from urllib.parse import urljoin

import urllib3
import yaml
from schema import Optional
from schema import Schema
from schema import SchemaError


class Environ(UserDict):
    """Handle app configuration from os.environ with support for copilot
    environment specific configuration and type conversion."""

    def get_value(self, key, /, default=None, allow_environment_override=False):
        environment = self.data["COPILOT_ENVIRONMENT_NAME"].upper()

        overridden_key = f"{environment.upper()}_{key}"

        if allow_environment_override and overridden_key in self.data:
            return self.data[overridden_key]

        if key in self.data:
            return self.data[key]

        if default is not None:
            return default

        raise KeyError(f"{key} not found")

    def int(self, key, /, default=None, allow_environment_override=False):
        return int(
            self.get_value(
                key,
                default=default,
                allow_environment_override=allow_environment_override,
            )
        )

    def list(self, key, /, default=None, allow_environment_override=False):
        value = self.get_value(
            key, default=default, allow_environment_override=allow_environment_override
        )

        if not isinstance(value, str):
            return value

        if not value:
            return []

        return [v.strip() for v in value.split(",")]

    def bool(self, key, /, default=None, allow_environment_override=False):
        value = self.get_value(
            key, default=default, allow_environment_override=allow_environment_override
        )

        if isinstance(value, str):
            return value.strip().lower() == "true"

        return value


def get_appconfig_configuration(appconfig_path):
    """
    Retrieve appconfig data from a local appconfig agent. `appconfig_path`
    should be in the format:

    {application}:{environment}:{configuration}

    Note, environment refers to the AppConfig environment, not the local
    application environment.
    """
    from main import app

    application, environment, configuration = appconfig_path.split(":")

    url = urljoin(
        app.config["APPCONFIG_URL"],
        f"/applications/{application}/environments/{environment}/configurations/{configuration}",
    )

    try:
        response = urllib3.PoolManager().request(
            "GET",
            url=url,
        )
    except Exception as ex:
        raise AppConfigError(ex)

    if response.status == 200:
        return yaml.safe_load(response.data)

    raise AppConfigError(f"AppConfig for {appconfig_path} not found.")


APPCONFIG_SCHEMA = Schema(
    {
        Optional("IpRanges"): [ip_network],
        Optional("BasicAuth"): [
            {
                "Path": str,
                "Username": str,
                "Password": str,
            }
        ],
        Optional("SharedTokens"): [
            {
                "HeaderName": str,
                "Value": str,
            }
        ],
        Optional(str): object
    }
)


class ValidationError(Exception):
    pass


class AppConfigError(Exception):
    pass


def get_ipfilter_config(appconfig_paths: list[str]):
    """Retrieve a list of app config configurations and combine them into a
    single dict."""
    ips = []
    auth = []
    shared_tokens = []
    for config_path in appconfig_paths:
        config = get_appconfig_configuration(config_path)
        try:
            APPCONFIG_SCHEMA.validate(config)
        except SchemaError as ex:
            raise ValidationError(f'AppConfig validation error: "{ex}" for path {config_path}')

        ips.extend(config.get("IpRanges", []))
        auth.extend(config.get("BasicAuth", []))
        shared_tokens.extend(config.get("SharedTokens", []))

    return {
        "ips": ips,
        "auth": auth,
        "shared_tokens": shared_tokens,
    }
