import unittest
from unittest.mock import patch

import urllib3
from parameterized import parameterized

from config import Environ, get_ipfilter_config, ValidationError
from tests.conftest import create_filter, create_origin, wait_until_connectable, create_appconfig_agent

BAD_APP_CONFIG = """
IpRanges:
    - 1.2.hello.4/32
SharedTokens:
    - HeaderName: x-cdn-secret
    - HeaderName: x-shared-secret
      Value: my-other-secret
"""

MINIMAL_APP_CONFIG = """
IpRanges:
    - 1.1.1.1/32
"""

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

    def _make_request(self, request_path="/", additional_headers={}):
        headers = {
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                "connection": "close",
            }
        headers.update(additional_headers)
        response = urllib3.PoolManager().request(
            "GET",
            url=f"http://127.0.0.1:8080{request_path}",
            headers=headers,
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
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
            )
        )
        self.addCleanup(create_appconfig_agent(2772, {"testapp:testenv:testconfig": MINIMAL_APP_CONFIG}))
        wait_until_connectable(2772)

        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_ipfilter_enabled_and_misconfigured_app_config_rejects_traffic(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUBLIC_PATHS", "/public-test"),
                ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
            ),
        )
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {"testapp:testenv:testconfig": BAD_APP_CONFIG},
            )
        )
        wait_until_connectable(2772)
        response = self._make_request("/private-test")
        self.assertEqual(response.status, 403)

    def test_ipfilter_enabled_and_missing_app_config_agent_rejects_traffic(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUBLIC_PATHS", "/public-test"),
                ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
            ),
        )

        response = self._make_request("/private-test")
        self.assertEqual(response.status, 403)


    def test_ipfilter_enabled_and_missing_app_config_rejects_traffic(self):
        """
        Missing app config will get a 404 back from the appconfig agent. This is
        simulated here by passing in empty appconfig to the agent during setup.
        """
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUBLIC_PATHS", "/public-test"),
                ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
            ),
        )
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {},
                True
            )
        )
        wait_until_connectable(2772)

        response = self._make_request("/private-test")
        self.assertEqual(response.status, 403)


def good_config():
    return {
        "IpRanges": ["1.1.1.1/32", "2.2.2.2", "2001:db8:abcd:0012::0/64"],
        "BasicAuth": [
            {
                "Path": "/__some_path",
                "Username": "my-user",
                "Password": "my-secret"
            }
        ],
        "SharedTokens": [
            {
                "HeaderName": "x-cdn-secret",
                "Value": "my-secret"
            },
        ]
    }


@patch("config.get_appconfig_configuration")
class AppConfigValidationTestCase(unittest.TestCase):
    def test_get_ipfilter_config_success(self, appconfig):
        config = good_config()
        appconfig.return_value = config

        actual = get_ipfilter_config(["a"])

        self.assertEqual(actual, {"ips": config["IpRanges"], "auth": config["BasicAuth"], "shared_tokens": config["SharedTokens"]})

    def test_get_ipfilter_config_multiple_paths_aggregate_results(self, appconfig):
        config_a = good_config()
        config_b = {"IpRanges": ["3.3.3.0/24"]}
        config_c = {"BasicAuth": [
            {
                "Path": "/__some_other_path",
                "Username": "my-user-2",
                "Password": "my-secret-2"
            }
        ]}
        config_d = {"SharedTokens": [
            {
                "HeaderName": "x-shared-secret",
                "Value": "my-other-secret"
            }
        ]}
        appconfig.side_effect = lambda path: {"a": config_a, "b": config_b, "c": config_c, "d": config_d}[path]

        actual = get_ipfilter_config(["a", "b", "c", "d"])

        self.assertEqual(actual["ips"], ["1.1.1.1/32", "2.2.2.2", "2001:db8:abcd:0012::0/64", "3.3.3.0/24"])
        self.assertEqual(actual["auth"], [config_a["BasicAuth"][0], config_c["BasicAuth"][0]])
        self.assertEqual(actual["shared_tokens"], [config_a["SharedTokens"][0], config_d["SharedTokens"][0]])

    def test_get_ipfilter_config_ignores_additional_keys(self, appconfig):
        config = good_config()
        config["BOGUS"] = True
        config["SAMOSA"] = "Mmm"
        appconfig.return_value = config

        actual = get_ipfilter_config(["a"])
        self.assertEqual(actual, {"ips": config["IpRanges"], "auth": config["BasicAuth"], "shared_tokens": config["SharedTokens"]})

    def test_get_ipfilter_config_all_keys_optional(self, appconfig):
        config = {}
        appconfig.return_value = config

        actual = get_ipfilter_config(["a"])
        self.assertEqual(actual, {"ips": [], "auth": [], "shared_tokens": []})

    @parameterized.expand(
        [
            ("not-an-ip-range", "does not appear to be an IPv4 or IPv6 network"),
            ("1.1.1.1/16", "has host bits set"),
            ("2001:db8:abcd:12:bad::/32", "has host bits set"),
        ]
    )
    def test_get_ipfilter_config_bad_ip_range_raises_exception(self, appconfig, ip_range, exp_error):
        conf = good_config()
        conf["IpRanges"].append(ip_range)
        appconfig.return_value = conf

        try:
            get_ipfilter_config(["a"])
            self.fail("Validation should have failed")
        except ValidationError as ex:
            self.assertTrue("Key 'IpRanges'" in str(ex))
            self.assertTrue(f"ip_network('{ip_range}') raised ValueError" in str(ex))
            self.assertTrue(exp_error in str(ex))

    @parameterized.expand(
        [
            ("Path", 1, "1 should be instance of 'str'"),
            ("Username", 2, "2 should be instance of 'str'"),
            ("Password", 3, "3 should be instance of 'str'"),
            ("Path", None, "Missing key: 'Path'"),
            ("Username", None, "Missing key: 'Username'"),
            ("Password", None, "Missing key: 'Password'"),
        ]
    )
    def test_get_ipfilter_config_bad_auth_data_raises_exception(self, appconfig, key, data, message):
        conf = good_config()
        if data is not None:
            conf["BasicAuth"][0][key] = data
        else:
            del conf["BasicAuth"][0][key]
        appconfig.return_value = conf

        try:
            get_ipfilter_config(["a"])
            self.fail("Validation should have failed")
        except ValidationError as ex:
            self.assertTrue(message in str(ex))
