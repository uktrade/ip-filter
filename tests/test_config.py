import unittest

import urllib3
from config import Environ
from flask.config import Config
from parameterized import parameterized
from tests.conftest import create_filter, create_origin, wait_until_connectable, create_appconfig_agent
from utils import validate_config


class EnvironTestCase(unittest.TestCase):
    def test_missing_key_raises_keyerror(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
            }
        )

        with self.assertRaises(KeyError):
            env.get_value("MISSING")

        with self.assertRaises(KeyError):
            env.list("MISSING")

        with self.assertRaises(KeyError):
            env.bool("MISSING")

        with self.assertRaises(KeyError):
            env.int("MISSING")

    def test_default_values(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
            }
        )

        self.assertEqual(env.get_value("MISSING", default="missing"), "missing")
        self.assertEqual(env.bool("MISSING", default=True), True)
        self.assertEqual(env.list("MISSING", default=[]), [])
        self.assertEqual(env.list("MISSING", default=["test"]), ["test"])
        self.assertEqual(env.int("MISSING", default=-1), -1)

    def test_type_conversion_bool(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
                "IS_TRUE": "True",
                "IS_FALSE": "False",
            }
        )

        self.assertEqual(env.bool("IS_TRUE"), True)
        self.assertEqual(env.bool("IS_FALSE"), False)

    def test_type_conversion_list(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
                "MULTIPLE": "profile1, profile2, profile3",
                "SINGLE_ITEM": "False",
                "EMPTY": "",
            }
        )
        self.assertEqual(env.list("MULTIPLE"), ["profile1", "profile2", "profile3"])
        self.assertEqual(env.list("SINGLE_ITEM"), ["False"])
        self.assertEqual(env.list("EMPTY"), [])

    def test_type_conversion_int(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
                "TEST": "-1",
            }
        )
        self.assertEqual(env.int("TEST"), -1)

    def test_type_with_environment_overrides(self):
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
                "RANDOM_FIELD": "base-value",
                "STAGING_RANDOM_FIELD": "environment-override",
            }
        )

        self.assertEqual(env.get_value("RANDOM_FIELD"), "base-value")
        self.assertEqual(
            env.get_value("RANDOM_FIELD", allow_environment_override=True),
            "environment-override",
        )

    def test_can_unset_env_var(self):
        """
        Can a variable that is set globally be unset with an environment level override:

        PROTECTED_PATHS=/aaa,/bbb/,ccc

        # This should unset PROTECTED_PATHS in the staging environment
        STAGING_PROTECTED_PATHS=
        """
        env = Environ(
            {
                "COPILOT_ENVIRONMENT_NAME": "staging",
                "PROTECTED_PATHS": "one,two,three",
                "STAGING_PROTECTED_PATHS": "",
            }
        )

        self.assertEqual(
            env.list("PROTECTED_PATHS", allow_environment_override=False),
            ["one", "two", "three"],
        )
        self.assertEqual(
            env.list("PROTECTED_PATHS", allow_environment_override=True), []
        )


class ConfigurationTestCase(unittest.TestCase):
    """Tests covering the configuration logic."""

    def _setup_environment(
            self,
            env=(),
    ):
        default_env = (
            ("SERVER", "localhost:8081"),
            ("SERVER_PROTO", "http"),
        )
        self.addCleanup(create_filter(8080, default_env + env))
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

    def _make_request(self, request_path="/"):
        response = urllib3.PoolManager().request(
            "GET",
            url=f"http://127.0.0.1:8080{request_path}",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                "connection": "close",
            },
            body=b"some-data",
        )

        return response

    def test_ipfilter_disabled(self):
        """If the IP filter is disabled requests pass through to the origin."""
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "False"),
            )
        )
        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_ipfilter_enabled_globally_but_disabled_for_environment(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("STAGING_IPFILTER_ENABLED", "False"),
            )
        )

        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_ipfilter_enabled_and_path_is_in_public_paths(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUBLIC_PATHS", "/public-test"),
            ),
        )
        response = self._make_request("/public-test")
        self.assertEqual(response.status, 200)

        response = self._make_request("/public-test/some/sub/path")
        self.assertEqual(response.status, 200)

        # must match the start of the path
        response = self._make_request("/not-valid/public-test/")
        self.assertEqual(response.status, 403)

    def test_ipfilter_enabled_and_path_is_not_in_public_paths(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUBLIC_PATHS", "/public-test"),
            ),
        )
        response = self._make_request("/not-public")
        self.assertEqual(response.status, 403)

    def test_ipfilter_enabled_and_path_is_in_protected_paths(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PROTECTED_PATHS", "/protected-test"),
            ),
        )
        response = self._make_request("/protected-test")
        self.assertEqual(response.status, 403)

        response = self._make_request("/protected-test/some/sub/path")
        self.assertEqual(response.status, 403)

        # The protected path must match the start of the url
        response = self._make_request("/should-be-public/protected-test/")
        self.assertEqual(response.status, 200)

    def test_ipfilter_enabled_and_path_is_not_in_protected_paths(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PROTECTED_PATHS", "/protected-test"),
            ),
        )
        response = self._make_request("/not-protected")
        self.assertEqual(response.status, 200)

    def test_protected_paths_and_public_paths_are_mutually_exclusive(self):
        # We aren't checking for log output as the log is emitted from another process so `TestCase.assertLogs` does not
        # capture them.

        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PROTECTED_PATHS", "/protected-test"),
                ("PUBLIC_PATHS", "/healthcheck"),
            ),
        )

        response = self._make_request("/healthcheck")
        self.assertEqual(response.status, 200)

        response = self._make_request("/protected-test")
        self.assertEqual(response.status, 403)

        response = self._make_request("/some-random-path")
        self.assertEqual(response.status, 403)

        response = self._make_request("/another-random-path")
        self.assertEqual(response.status, 403)

    def test_appconfig_agent_with_valid_ip(self):
        self.addCleanup(create_appconfig_agent(2772))

        wait_until_connectable(2772)

        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
            )
        )
        response = self._make_request()

        self.assertEqual(response.status, 200)


def good_config():
    return {
        "LOG_LEVEL": "WARN",
        "CACHE_TYPE": "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 300,
        "DEBUG": False,
        "ENVIRONMENT_NAME": "dev",
        "SERVER_PROTO": "http",
        "SERVER": "localhost",
        "APPCONFIG_URL": "http://localhost:2772",
        "EMAIL_NAME": "DBT",
        "EMAIL": "test@test.test",
        "IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX": -1,
        "IPFILTER_ENABLED": True,
        "APPCONFIG_PROFILES": [],
        "PUBLIC_PATHS": [],
        "PROTECTED_PATHS": [],
    }

class ConfigurationValidationTestCase(unittest.TestCase):
    def test_validate_config_success(self):
        config = Config(".", good_config())

        self.assertTrue(validate_config(config))

    def test_validate_ignores_additional_keys(self):
        config = Config(".", good_config())
        config["BOGUS"] = True
        config["SAMOSA"] = "Mmm"

        self.assertTrue(validate_config(config))

    @parameterized.expand(
        [
            "LOG_LEVEL",
            "CACHE_TYPE",
            "CACHE_DEFAULT_TIMEOUT",
            "DEBUG",
            "ENVIRONMENT_NAME",
            "SERVER_PROTO",
            "SERVER",
            "APPCONFIG_URL",
            "EMAIL_NAME",
            "EMAIL",
            "IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX",
            "IPFILTER_ENABLED",
            "APPCONFIG_PROFILES",
            "PUBLIC_PATHS",
            "PROTECTED_PATHS",
        ]
    )
    def test_validate_config_missing_key_raises_exception(self, key):
        conf = good_config()
        del conf[key]
        config = Config(".", conf)

        try:
            validate_config(config)
            self.fail("Validation should have failed")
        except Exception as ex:
            self.assertTrue("Missing key:" in str(ex))
            self.assertTrue(key in str(ex))
    @parameterized.expand(
        [
            ("LOG_LEVEL", 6, "should be instance of 'str'"),
            ("CACHE_TYPE", False, "should be instance of 'str'"),
            ("CACHE_DEFAULT_TIMEOUT", "some time", "int"),
            ("DEBUG", "True", "bool"),
            ("ENVIRONMENT_NAME", False, "should be instance of 'str'"),
            ("SERVER_PROTO", "cheese", "<lambda>('cheese') should evaluate to True"),
            ("SERVER", 77, "should be instance of 'str'"),
            ("APPCONFIG_URL", "not-a-url", "<lambda>('not-a-url') should evaluate to True"),
            ("EMAIL_NAME", 20, "20 should be instance of 'str'"),
            ("EMAIL", ["someone@an.address"], "['someone@an.address'] should be instance of 'str'"),
            ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "one", "int"),
            ("IPFILTER_ENABLED", "yes", "'yes' should be instance of 'bool'"),
            ("APPCONFIG_PROFILES", "profile_z", "'profile_z' should be instance of 'list'"),
            ("PUBLIC_PATHS", 99, "99 should be instance of 'list'"),
            ("PROTECTED_PATHS", [7, 2], "7 should be instance of 'str'"),
        ]
    )
    def test_validate_config_bad_data_raises_exception(self, key, data, message):
        conf = good_config()
        conf[key] = data
        config = Config(".", conf)

        try:
            validate_config(config)
            self.fail("Validation should have failed")
        except Exception as ex:
            self.assertTrue(f"Key '{key}' error:" in str(ex))
            self.assertTrue(message in str(ex))
