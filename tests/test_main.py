import base64
import itertools
import json
import logging
import os
import socket
import subprocess
import time
import unittest
import urllib.parse
import uuid
from datetime import datetime
from unittest.mock import patch, MagicMock
from importlib import reload

import ddtrace
import urllib3
from flask import Flask
from flask import Response
from flask import request
from parameterized import parameterized

from asim_formatter import ASIMFormatter
from config import Environ
from tests.conftest import create_appconfig_agent
from tests.conftest import create_filter
from tests.conftest import create_origin
from tests.conftest import wait_until_connectable
from utils import get_package_version

SHARED_HEADER_CONFIG = """
IpRanges:
    - 1.2.3.4/32
SharedTokens:
    - HeaderName: x-cdn-secret
      Value: my-secret
"""
SHARED_HEADER_CONFIG_TWO_VALUES = """
IpRanges:
    - 1.2.3.4/32
SharedTokens:
    - HeaderName: x-cdn-secret
      Value: my-secret
    - HeaderName: x-cdn-secret
      Value: my-other-secret
"""
SHARED_HEADER_CONFIG_TWO_HEADERS = """
IpRanges:
    - 1.2.3.4/32
SharedTokens:
    - HeaderName: x-cdn-secret
      Value: my-secret
    - HeaderName: x-shared-secret
      Value: my-other-secret
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

    def test_ipfilter_enabled_allow_additional_ip_addresses(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("ADDITIONAL_IP_LIST", "1.1.1.1"),
                ("PUBLIC_PATHS", "/public-test"),
            )
        )

        response = self._make_request("/protected-test")
        self.assertEqual(response.status, 200)

    def test_ipfilter_enabled_allow_additional_ip_networks(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("ADDITIONAL_IP_LIST", "1.1.1.0/29"),
                ("PUBLIC_PATHS", "/public-test"),
            )
        )

        response = self._make_request("/protected-test")
        self.assertEqual(response.status, 200)

    def test_pub_host_preferred_when_pub_and_priv(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUB_HOST_LIST", "127.0.0.1:8080"),
                ("PRIV_HOST_LIST", "127.0.0.1:8080"),
            )
        )

        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_host_in_pub_host_list(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUB_HOST_LIST", "127.0.0.1:8080"),
            )
        )

        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_host_in_priv_host_list(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PRIV_HOST_LIST", "127.0.0.1:8081"),
            )
        )

        response = self._make_request()

        self.assertEqual(response.status, 200)

    def test_pub_host_list_and_protected_path(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PUB_HOST_LIST", "127.0.0.1:8080"),
                ("PROTECTED_PATHS", "/admin"),
            )
        )

        response = self._make_request("/admin")

        self.assertEqual(response.status, 403)

    def test_priv_host_list_and_public_path(self):
        self._setup_environment(
            (
                ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ("IPFILTER_ENABLED", "True"),
                ("PRIV_HOST_LIST", "127.0.0.1:8080"),
                ("PUBLIC_PATHS", "/healthcheck"),
            )
        )

        response = self._make_request("/healthcheck")

        self.assertEqual(response.status, 200)


class ProxyTestCase(unittest.TestCase):
    """Tests that cover the ip filter's proxy functionality."""

    def test_meta_wait_until_connectable_raises(self):
        with self.assertRaises(OSError):
            wait_until_connectable(8080, max_attempts=10)

    def test_method_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
        echo_methods = [
            urllib3.PoolManager()
            .request(
                method,
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .headers["x-echo-method"]
            for method in methods
        ]
        self.assertEqual(methods, echo_methods)

    def test_host_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        host = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .headers["x-echo-header-host"]
        )
        self.assertEqual(host, "somehost.com")

    def test_path_and_query_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "127.0.0.1:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        path = urllib.parse.quote("/a/£/💾")
        query = urllib.parse.urlencode(
            [
                ("a", "b"),
                ("🍰", "😃"),
            ]
        )
        raw_uri_expected = f"{path}?{query}"
        response = urllib3.PoolManager().request(
            "GET",
            url=f"http://127.0.0.1:8080/{path}?{query}",
            headers={
                "host": "127.0.0.1:8081",
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        raw_uri_received = response.headers["x-echo-raw-uri"]

        self.assertEqual(raw_uri_expected, raw_uri_received)
        self.assertEqual(response.headers["x-echo-header-Host"], "127.0.0.1:8081")

    def test_path_is_properly_formed(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "127.0.0.1:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        path = urllib.parse.quote("/a/£/💾")
        query = urllib.parse.urlencode(
            [
                ("a", "b"),
            ]
        )
        raw_uri_expected = f"{path}?{query}"
        response = urllib3.PoolManager().request(
            "GET",
            url=f"http://127.0.0.1:8080/{path}?{query}",
            headers={
                "host": "127.0.0.1:8081",
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        raw_uri_received = response.headers["x-echo-raw-uri"]

        self.assertEqual(raw_uri_expected, raw_uri_received)
        self.assertEqual(response.headers["x-echo-header-Host"], "127.0.0.1:8081")

    def test_body_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        method_bodies_expected = [
            ("GET", uuid.uuid4().bytes * 1),
            ("POST", uuid.uuid4().bytes * 10),
            ("PUT", uuid.uuid4().bytes * 100),
            ("PATCH", uuid.uuid4().bytes * 1000),
            ("DELETE", uuid.uuid4().bytes * 10000),
            ("OPTIONS", uuid.uuid4().bytes * 100000),
        ]
        method_bodies_received = [
            (
                method,
                urllib3.PoolManager()
                .request(
                    method,
                    url="http://127.0.0.1:8080/",
                    headers={
                        "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    },
                    body=body,
                )
                .data,
            )
            for method, body in method_bodies_expected
        ]
        self.assertEqual(method_bodies_expected, method_bodies_received)

    def test_status_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        method_statuses_expected = list(
            itertools.product(
                ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
                ["200", "201", "401", "403", "500"],
            )
        )
        method_statuses_received = [
            (
                method,
                str(
                    urllib3.PoolManager()
                    .request(
                        method,
                        url="http://127.0.0.1:8080/",
                        headers={
                            "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                            "x-echo-response-status": status,
                        },
                    )
                    .status
                ),
            )
            for method, status in method_statuses_expected
        ]
        self.assertEqual(method_statuses_expected, method_statuses_received)

    def test_connection_is_not_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                "connection": "close",
            },
            body=b"some-data",
        )
        self.assertEqual(response.status, 200)
        self.assertNotIn("x-echo-header-connection", response.headers)

    def test_no_issue_if_origin_restarted(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        stop_origin_1 = create_origin(8081)
        self.addCleanup(stop_origin_1)
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_1 = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/some-path",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body=b"some-data",
        )
        self.assertEqual(response_1.status, 200)
        self.assertEqual(response_1.data, b"some-data")
        remote_port_1 = response_1.headers["x-echo-remote-port"]

        stop_origin_1()
        stop_origin_2 = create_origin(8081)
        self.addCleanup(stop_origin_2)
        wait_until_connectable(8081)

        response_2 = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/some-path",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body=b"some-more-data",
        )
        self.assertEqual(response_2.status, 200)
        self.assertEqual(response_2.data, b"some-more-data")
        remote_port_2 = response_2.headers["x-echo-remote-port"]

        # A meta test to ensure that we really have
        # restart the origin server. Hopefully not too flaky.
        self.assertNotEqual(remote_port_1, remote_port_2)

    @unittest.skip(
        "This test hangs indefinitely, likely because `gunicorn --timeout 0`"
    )
    def test_no_issue_if_request_unfinished(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        class BodyException(Exception):
            pass

        def body():
            yield b"-" * 100_000
            time.sleep(1)
            raise BodyException()

        # We only send half of the request
        with self.assertRaises(BodyException):
            urllib3.PoolManager().request(
                "POST",
                "http://127.0.0.1:8080/",
                headers={
                    "content-length": "200000",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
                body=body(),
            )

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body="some-data",
        )
        self.assertEqual(response.data, b"some-data")

    def test_request_header_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "some-header": "some-value",
                },
            )
            .headers["x-echo-header-some-header"]
        )
        self.assertEqual(response_header, "some-value")

    def test_content_length_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        headers = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
                body=b"some-data",
            )
            .headers
        )
        self.assertEqual(
            headers["x-echo-header-content-length"], str(len(b"some-data"))
        )
        self.assertNotIn("x-echo-header-transfer-encoding", headers)

    def test_if_no_body_then_no_content_length_and_no_transfer_encoding(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        self.assertNotIn("x-echo-header-content-length", response.headers)
        self.assertNotIn("x-echo-header-transfer-encoding", response.headers)

    def test_body_length_zero_then_content_length_zero_and_no_transfer_encoding(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        headers = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
                body=b"",
            )
            .headers
        )
        self.assertEqual(headers["x-echo-header-content-length"], "0")
        self.assertNotIn("x-echo-header-transfer-encoding", headers)

    def test_response_header_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-some-header": "some-value",
                },
            )
            .headers["some-header"]
        )
        self.assertEqual(response_header, "some-value")

    def test_content_disposition_with_latin_1_character_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-content-disposition": 'attachment; filename="Ö"',
                },
            )
            .headers["content-disposition"]
        )

        self.assertEqual(response_header, 'attachment; filename="Ö"')

    def test_get_content_length_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        content_length = (
            urllib3.PoolManager()
            .request(
                "GET",
                # Make sure test doesn't pass due to "de-chunking" of small bodies
                body=b"Something" * 10000000,
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .headers["content-length"]
        )
        self.assertEqual(content_length, "90000000")

    def test_head_content_length_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        content_length = (
            urllib3.PoolManager()
            .request(
                "HEAD",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-content-length": "12345678",
                },
            )
            .headers["content-length"]
        )
        # This should probably be 12345678
        self.assertEqual(content_length, "0")

    def test_request_cookie_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localtest.me:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "cookie": "my_name=my_value",
                },
            )
            .headers["x-echo-header-cookie"]
        )
        self.assertEqual(response_header, "my_name=my_value")

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "cookie": "my_name=my_value; my_name_b=my_other_value",
                },
            )
            .headers["x-echo-header-cookie"]
        )
        self.assertEqual(response_header, "my_name=my_value; my_name_b=my_other_value")

    def test_response_cookie_is_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localtest.me:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-set-cookie": "my_name=my_value",
                },
            )
            .headers["set-cookie"]
        )
        self.assertEqual(response_header, "my_name=my_value")

        # A full cookie with lots of components
        full_cookie_value = (
            "my_name=my_value; Domain=.localtest.me; "
            "Expires=Wed, 29-Apr-2020 15:06:49 GMT; Secure; "
            "HttpOnly; Path=/path"
        )
        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/path",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-set-cookie": full_cookie_value,
                },
            )
            .headers["set-cookie"]
        )
        self.assertEqual(response_header, full_cookie_value)

        # Checking the treatment of Max-Age (which Python requests can change
        # to Expires)
        response_header = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/path",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-set-cookie": "my_name=my_value; Max-Age=100",
                },
            )
            .headers["set-cookie"]
        )
        self.assertEqual(response_header, "my_name=my_value; Max-Age=100")

    def test_multiple_response_cookies_are_forwarded(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localtest.me:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        # We make sure we don't depend on or are thwarted by magic that an HTTP
        # client in the tests does regarding multiple HTTP headers of the same
        # name, and specifically any handing of multiple Set-Cookie headers
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", 8080))
        sock.send(
            b"GET http://localtest.me:8081/multiple-cookies HTTP/1.1\r\n"
            b"host:127.0.0.1\r\n"
            b"x-forwarded-for:1.2.3.4, 1.1.1.1, 1.1.1.1\r\n"
            b"x-multiple-cookies:name_a=value_a,name_b=value_b\r\n"
            b"\r\n"
        )

        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
        sock.close()

        self.assertIn(b"set-cookie: name_a=value_a\r\n", response)
        self.assertIn(b"set-cookie: name_b=value_b\r\n", response)

    def test_cookie_not_stored(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localtest.me:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        # Ensure that the filter itself don't store cookies set by the origin
        cookie_header = "x-echo-header-cookie"
        set_cookie = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "x-echo-response-header-set-cookie": "my_name=my_value_a; Domain=.localtest.me; Path=/path",
                },
            )
            .headers["set-cookie"]
        )
        self.assertEqual(
            set_cookie, "my_name=my_value_a; Domain=.localtest.me; Path=/path"
        )
        has_cookie = (
            cookie_header
            in urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .headers
        )
        self.assertFalse(has_cookie)

        # Meta test, ensuring that cookie_header is the right header to
        # check for to see if the echo origin received the cookie
        cookie_header_value = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                    "cookie": "my_name=my_value_b",
                },
            )
            .headers[cookie_header]
        )
        self.assertEqual(cookie_header_value, "my_name=my_value_b")

    def test_gzipped(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localtest.me:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/gzipped",
            headers={
                "host": "localtest.me:8081",
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body=b"something-to-zip",
        )
        self.assertEqual(response.data, b"something-to-zip")
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertIn("content-length", response.headers)

    def test_slow_upload_non_chunked(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        num_bytes = 35

        def body():
            for _ in range(0, num_bytes):
                yield b"-"
                time.sleep(1)

        response = urllib3.PoolManager().request(
            "POST",
            "http://127.0.0.1:8080/",
            headers={
                "content-length": str(num_bytes),
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body=body(),
        )
        self.assertEqual(response.data, b"-" * num_bytes)
        self.assertNotIn("x-echo-header-transfer-encoding", response.headers)

    def test_slow_upload_chunked(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        num_bytes = 35

        def body():
            for _ in range(0, num_bytes):
                yield b"-"
                time.sleep(1)

        response = urllib3.PoolManager().request(
            "POST",
            "http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
            body=body(),
        )
        self.assertEqual(response.data, b"-" * num_bytes)
        self.assertEqual(response.headers["x-echo-header-transfer-encoding"], "chunked")

    def test_chunked_response(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/chunked",
            headers={
                "host": "127.0.0.1:8081",
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                "x-chunked-num-bytes": "10000",
            },
            preload_content=False,
            chunked=True,
        )

        self.assertEqual("chunked", response.headers["Transfer-Encoding"])
        self.assertNotIn("content-length", response.headers)
        self.assertEqual(response.data, b"-" * 10000)

    def test_https(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "www.gov.uk"),
                    ("SERVER_PROTO", "https"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        wait_until_connectable(8080)
        wait_until_connectable(2772)

        # On the one hand not great to depend on a 3rd party/external site,
        # but it does test that the filter can connect to a regular/real site
        # that we cannot have customised to make the tests pass. Plus,
        # www.google.com is extremely unlikely to go down
        data = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "www.gov.uk",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .data
        )
        self.assertIn(b"GOV.UK", data)

    def test_https_origin_not_exist_returns_500(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "does.not.exist"),
                    ("SERVER_PROTO", "https"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        wait_until_connectable(8080)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        self.assertEqual(response.status, 500)

    def test_http_origin_not_exist_returns_500(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "does.not.exist"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        wait_until_connectable(8080)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        self.assertEqual(response.status, 500)

    def test_not_running_origin_returns_500(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        wait_until_connectable(8080)
        wait_until_connectable(2772)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "127.0.0.1:8081",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 500)


class IpFilterLogicTestCase(unittest.TestCase):
    """Tests covering the IP filter logic."""

    def test_missing_x_forwarded_for_returns_403_and_origin_not_called(self):
        # Origin not running: if an attempt was made to connect to it, we
        # would get a 500
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig"),
                ),
            )
        )
        wait_until_connectable(8080)
        wait_until_connectable(2772)
        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
        )
        self.assertEqual(response.status, 403)

    def test_incorrect_x_forwarded_for_returns_403_and_origin_not_called(self):
        # Origin not running: if an attempt was made to connect to it, we
        # would get a 500
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ),
            )
        )
        wait_until_connectable(8080)

        x_forwarded_for_headers = [
            "1.2.3.4, 1.1.1.1, 1.1.1.1, 1.1.1.1",
            "3.3.3.3, 1.1.1.1, 1.1.1.1",
            "1.2.3.4, 1.1.1.1",
            "1.2.3.4",
            "",
        ]
        statuses = [
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "x-forwarded-for": x_forwarded_for_header,
                },
            )
            .status
            for x_forwarded_for_header in x_forwarded_for_headers
        ]
        self.assertEqual(statuses, [403] * len(x_forwarded_for_headers))

    def test_x_forwarded_for_index_respected(self):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "mytest:env:iplist": """
IpRanges:
  - 1.2.3.4/32
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-2"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "mytest:env:iplist"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers={
                "host": "somehost.com",
                "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
            },
        )
        self.assertEqual(response.status, 403)
        self.assertIn(b">1.1.1.1<", response.data)
        self.assertIn(b">/<", response.data)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 200)

    def test_ip_matching_cidr_respected(self):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.0/24
BasicAuth: []
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    (
                        "APPCONFIG_PROFILES",
                        "testapp:testenv:testconfig,testapp:testenv:testconfig2",
                    ),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 200)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "1.2.3.5, 1.1.1.1, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 200)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "1.2.4.5, 1.1.1.1, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 403)

    def test_trace_id_is_reported(self):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth: []
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-2"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        response = urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/__some_path",
            headers={
                "host": "somehost.com",
                "x-forwarded-for": "1.1.1.1, 1.1.1.1, 1.1.1.1",
                "x-cdn-secret": "my-mangos",
                "X-B3-Traceid": "1234magictraceid",
            },
        )
        self.assertEqual(response.status, 403)
        self.assertIn(b">1234magictraceid<", response.data)

    def test_can_process_request_with_ipv6_ip(self):
        self.addCleanup(create_appconfig_agent(2772))
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        status = (
            urllib3.PoolManager()
            .request(
                "GET",
                url="http://127.0.0.1:8080/",
                headers={
                    "host": "somehost.com",
                    "x-forwarded-for": "2a00:23c4:ce80:a01:4979:78c8:535c:bc16, 1.1.1.1",
                },
            )
            .status
        )
        self.assertEqual(status, 403)


class BasicAuthTestCase(unittest.TestCase):
    """Tests covering basic auth responses."""

    def get_basic_auth_response(
        self,
        host="somehost.com",
        request_path=None,
        x_forwarded_for="1.2.3.4, 1.1.1.1, 1.1.1.1",
        credentials=b"my-user:my-secret",
    ):
        return urllib3.PoolManager().request(
            "GET",
            url=f"http://127.0.0.1:8080/{request_path}",
            headers={
                "host": host,
                "x-forwarded-for": x_forwarded_for,
                "authorization": "Basic "
                + base64.b64encode(credentials).decode("utf-8"),
            },
        )

    def test_basic_auth_header_respected(self):
        """
        Tests four requests each with whitelisted ip in x-forwarded-for header:
        1. No auth path in url, returns 200.
        2. No auth path in url, invalid password, returns 403: generic access denied.
        3. Auth path in url, invalid password, returns 401: "Login required".
        4. Auth path in url, valid user and password, returns 200.
        """
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth:
    - Path: /__some_path
      Username: my-user
      Password: my-secret
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        status = self.get_basic_auth_response().status

        self.assertEqual(status, 200)

        status = self.get_basic_auth_response(credentials=b"my-user:my-mangos").status

        self.assertEqual(status, 403)

        response = self.get_basic_auth_response(
            request_path="__some_path", credentials=b"my-user:my-mangos"
        )

        self.assertEqual(response.status, 401)
        self.assertEqual(
            response.headers["WWW-Authenticate"], 'Basic realm="Login Required"'
        )

        response = self.get_basic_auth_response(request_path="__some_path")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, b"ok")
        self.assertNotIn("WWW-Authenticate", response.headers)

    def test_basic_auth_second_cred_set_same_path_respected(self):
        """Tests that:
        1. 403 generic access denied message is returned for invalid password when auth path doesn't match request for my-user credentials.
        2. 403 generic access denied message is returned for invalid password when auth path doesn't match request for my-other-user credentials.
        3. 200 is returned when auth headers and path match second set of credentials (my-other-user).
        """
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth:
    - Path: /__some_path
      Username: my-user
      Password: my-secret
    - Path: /__some_path
      Username: my-other-user
      Password: my-other-secret
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )

        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)
        wait_until_connectable(2772)

        status = self.get_basic_auth_response(credentials=b"my-user:my-mangos").status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-mangos"
        ).status

        self.assertEqual(status, 403)

        response = self.get_basic_auth_response(
            request_path="__some_path", credentials=b"my-other-user:my-other-secret"
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, b"ok")

    def test_basic_auth_second_cred_set_different_path_respected(self):
        """Tests that:
        1. 403 generic access denied message is returned for invalid password when auth path doesn't match request for my-user credentials.
        2. 403 generic access denied message is returned for invalid password when auth path doesn't match request for my-other-user credentials.
        3. 200 ok returned when auth headers and path match first set of credentials (my-user).
        4. 401 returned when invalid password header on matching path for first set of credentials (my-user).
        5. 200 ok returned when auth headers and path match second set of credentials (my-other-user).
        6. 401 returned when invalid password header on matching path for second set of credentials (my-other-user).
        7. 200 returned when valid user / password, but no matching path for second set of credentials (my-other-user).
        """
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth:
    - Path: /__some_path
      Username: my-user
      Password: my-secret
    - Path: /__some_other_path
      Username: my-other-user
      Password: my-other-secret
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_basic_auth_response(credentials=b"my-user:my-mangos").status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-mangos"
        ).status

        self.assertEqual(status, 403)

        response = self.get_basic_auth_response(request_path="__some_path")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, b"ok")

        response = self.get_basic_auth_response(
            request_path="__some_path", credentials=b"my-user:my-mangos"
        )

        self.assertEqual(response.status, 401)

        response = self.get_basic_auth_response(
            request_path="__some_other_path",
            credentials=b"my-other-user:my-other-secret",
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, b"ok")

        response = self.get_basic_auth_response(
            request_path="__some_other_path",
            credentials=b"my-other-user:my-other-mangos",
        )

        self.assertEqual(response.status, 401)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-secret"
        ).status

        self.assertEqual(status, 200)

    def test_basic_auth_second_route_respected(self):
        """
        Test that while auth path doesn't match request:

        1. 403 returned for valid basic auth credentials when ip not whitelisted.
        2. 403 returned for first set of invalid basic auth credentials when ip is whitelisted.
        3. 403 returned for second set of invalid basic auth credentials when ip is whitelisted.
        4. 200 returned for second set of valid basic auth credentials when ip is whitelisted.
        """
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth:
    - Path: /__some_path
      Username: my-user
      Password: my-secret
    - Path: /__some_path
      Username: my-other-user
      Password: my-other-secret
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )

        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_basic_auth_response(
            x_forwarded_for="5.5.5.5, 1.1.1.1, 1.1.1.1"
        ).status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(credentials=b"my-user:my-mangos").status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-mangos"
        ).status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-secret"
        ).status

        self.assertEqual(status, 200)

    def test_basic_auth_second_route_same_path_respected(self):
        """
        Test that:
        1. 403 returned for first set of invalid credentials where path doesn't match request.
        2. 403 returned for second set of invalid credentials where path doesn't match request.
        3. 200 ok returned for second set of valid credentials with matching path.
        """
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {
                    "testapp:testenv:testconfig2": """
IpRanges:
    - 1.2.3.4/32
BasicAuth:
    - Path: /__some_path
      Username: my-user
      Password: my-secret
    - Path: /__some_path
      Username: my-other-user
      Password: my-other-secret
"""
                },
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )

        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_basic_auth_response(credentials=b"my-user:my-mangos").status

        self.assertEqual(status, 403)

        status = self.get_basic_auth_response(
            credentials=b"my-other-user:my-other-mangos"
        ).status

        self.assertEqual(status, 403)

        response = self.get_basic_auth_response(
            request_path="__some_path", credentials=b"my-other-user:my-other-secret"
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.data, b"ok")


class SharedTokenTestCase(unittest.TestCase):
    def get_shared_token_response(self, custom_headers=None):
        if custom_headers == None:
            custom_headers = {"x-cdn-secret": "my-secret"}

        headers = {
            "x-cf-forwarded-url": "http://somehost.com/",
            "x-forwarded-for": "1.2.3.4, 1.1.1.1, 1.1.1.1",
        } | custom_headers

        return urllib3.PoolManager().request(
            "GET",
            url="http://127.0.0.1:8080/",
            headers=headers,
        )

    @parameterized.expand(
        [
            ({}, 403),
            ({"x-cdn-secret": "not-my-secret"}, 403),
            (None, 200),
        ]
    )
    def test_shared_token_header_respected(self, custom_headers, expected_status):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {"testapp:testenv:testconfig2": SHARED_HEADER_CONFIG},
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_shared_token_response(custom_headers=custom_headers).status

        self.assertEqual(status, expected_status)

    @parameterized.expand(
        [
            ({"x-cdn-secret": "my-mangos"}, 403),
            ({"x-cdn-secret": "my-other-secret"}, 200),
        ]
    )
    def test_second_shared_token_header_respected(
        self, custom_headers, expected_status
    ):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {"testapp:testenv:testconfig2": SHARED_HEADER_CONFIG_TWO_VALUES},
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_shared_token_response(custom_headers=custom_headers).status

        self.assertEqual(status, expected_status)

    @parameterized.expand(
        [
            ({"x-cdn-secret": "my-mangos"}, 403),
            ({"x-cdn-secret": "my-other-secret"}, 200),
        ]
    )
    def test_shared_token_second_route_respected(self, custom_headers, expected_status):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {"testapp:testenv:testconfig2": SHARED_HEADER_CONFIG_TWO_VALUES},
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        status = self.get_shared_token_response(custom_headers=custom_headers).status

        self.assertEqual(status, expected_status)

    def test_shared_token_header_removed(self):
        self.addCleanup(
            create_appconfig_agent(
                2772,
                {"testapp:testenv:testconfig2": SHARED_HEADER_CONFIG_TWO_HEADERS},
            )
        )
        self.addCleanup(
            create_filter(
                8080,
                (
                    ("SERVER", "localhost:8081"),
                    ("SERVER_PROTO", "http"),
                    ("COPILOT_ENVIRONMENT_NAME", "staging"),
                    ("APPCONFIG_PROFILES", "testapp:testenv:testconfig2"),
                    ("IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX", "-3"),
                ),
            )
        )
        self.addCleanup(create_origin(8081))
        wait_until_connectable(8080)
        wait_until_connectable(8081)

        response = self.get_shared_token_response(
            custom_headers={
                "x-cdn-secret": "my-mangos",
                "x-shared-secret": "my-other-secret",
            }
        )

        self.assertEqual(response.status, 200)
        self.assertNotIn("x-echo-header-x-shared-secret", response.headers)
        self.assertNotIn("x-echo-header-my-other-secret", response.headers)


class LoggingTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["poetry", "version"], stdout=subprocess.PIPE, text=True
        )
        cls.ip_filter_version = result.stdout.split()[1]

        # env vars needed to instantiate app
        os.environ["COPILOT_ENVIRONMENT_NAME"] = "test"
        os.environ["SERVER"] = "localhost:8081"
        os.environ["EMAIL"] = "testemail"
        os.environ["DD_ENV"] = "test"
        os.environ["DD_SERVICE"] = "ip-filter"
        os.environ["DD_VERSION"] = "1.0.0"
        os.environ["ECS_CONTAINER_METADATA_URI"] = (
            "http://169.254.170.2/v3/709d1c10779d47b2a84db9eef2ebd041-0265927825"
        )

        reload(ddtrace)

    def test_asim_formatter_get_log_dict(self):
        formatter = ASIMFormatter()
        log_record = logging.LogRecord(
            name=__name__,
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="This is a test log message",
            args=(),
            exc_info=None,
        )
        log_time = datetime.utcfromtimestamp(log_record.created).isoformat()

        log_dict = formatter.get_log_dict(log_record)

        assert log_dict == {
            "EventMessage": log_record.msg,
            "EventCount": 1,
            "EventStartTime": log_time,
            "EventEndTime": log_time,
            "EventType": "HTTPsession",
            "EventSeverity": "Informational",
            "EventOriginalSeverity": log_record.levelname,  # duplicate of above?
            "EventSchema": "WebSession",
            "EventSchemaVersion": "0.2.6",
            "IpFilterVersion": self.ip_filter_version,
        }

    def test_asim_formatter_get_request_dict(self):
        self.app = Flask(__name__)
        with self.app.test_request_context(
            method="GET",
            path="/example_route",
            query_string="param1=value1&param2=value2",
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": "1.1.1.1",
                "X-Amzn-Trace-Id": "123testid",
            },
            data='{"key": "value"}',
        ):
            request_dict = ASIMFormatter().get_request_dict(request)

            assert request_dict == {
                "Url": request.url,
                "UrlOriginal": request.url,
                "HttpVersion": request.environ.get("SERVER_PROTOCOL"),
                "HttpRequestMethod": request.method,
                "HttpContentType": request.content_type,
                "HttpContentFormat": request.mimetype,
                "HttpReferrer": request.referrer,
                "HttpUserAgent": str(request.user_agent),
                "HttpRequestXff": request.headers["X-Forwarded-For"],
                "HttpResponseTime": "N/A",
                "HttpHost": request.host,
                "AdditionalFields": {
                    "TraceHeaders": {"X-Amzn-Trace-Id": "123testid"},
                },
            }

    def test_asim_formatter_get_response_dict(self):
        response = Response(
            status=200,
            headers={
                "Content-Type": "application/json",
                "Content-Disposition": "attachment; filename=dummy.rtf",
            },
            response='{"key": "value"}',
        )

        response_dict = ASIMFormatter().get_response_dict(response)

        assert response_dict == {
            "EventResult": "Success",
            "EventResultDetails": response.status_code,
            "FileName": "dummy.rtf",
            "HttpStatusCode": response.status_code,
        }

    @patch("ddtrace.trace.tracer.current_span")
    def test_datadog_trace_dict(self, mock_ddtrace_span):
        mock_ddtrace_span_response = MagicMock()
        mock_ddtrace_span_response.trace_id = 5735492756521486600
        mock_ddtrace_span_response.span_id = 12448338029536640280
        mock_ddtrace_span.return_value = mock_ddtrace_span_response

        result = ASIMFormatter()._datadog_trace_dict()

        assert result == {
            "service": "ip-filter",
            "env": "test",
            "version": "1.0.0",
            "container_id": "709d1c10779d47b2a84db9eef2ebd041-0265927825",
            "dd.trace_id": "5735492756521486600",
            "dd.span_id": "12448338029536640280",
        }

    @patch("ddtrace.trace.tracer.current_span")
    def test_get_first_64_bits_of(self, mock_ddtrace_span):
        trace_id = 5735492756521486600

        mock_ddtrace_span_response = MagicMock()
        mock_ddtrace_span_response.trace_id = trace_id
        mock_ddtrace_span_response.span_id = 12448338029536640280
        mock_ddtrace_span.return_value = mock_ddtrace_span_response

        result = ASIMFormatter()._get_first_64_bits_of(trace_id)

        assert result == str(trace_id)

    def test_asim_formatter_get_container_id(self):
        result = ASIMFormatter()._get_container_id()

        assert result == "709d1c10779d47b2a84db9eef2ebd041-0265927825"

    @patch("ddtrace.trace.tracer.current_span")
    def test_asim_formatter_format(self, mock_ddtrace_span):
        mock_ddtrace_span_response = MagicMock()
        mock_ddtrace_span_response.trace_id = 5735492756521486600
        mock_ddtrace_span_response.span_id = 12448338029536640280
        mock_ddtrace_span.return_value = mock_ddtrace_span_response

        log_record = logging.LogRecord(
            name=__name__,
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="This is a test log message",
            args=(),
            exc_info=None,
        )
        response = Response(
            status=200,
            headers={"Content-Type": "application/json"},
            response='{"key": "value"}',
        )
        log_record.response = response
        self.app = Flask(__name__)
        log_time = datetime.utcfromtimestamp(log_record.created).isoformat()

        with self.app.test_request_context(
            method="GET",
            path="/example_route",
            query_string="param1=value1&param2=value2",
            headers={"Content-Type": "application/json", "X-Forwarded-For": "1.1.1.1"},
            data='{"key": "value"}',
        ):
            formatted_log = ASIMFormatter().format(log_record)
            print(formatted_log)
            assert formatted_log == json.dumps(
                {
                    "EventMessage": log_record.msg,
                    "EventCount": 1,
                    "EventStartTime": log_time,
                    "EventEndTime": log_time,
                    "EventType": "HTTPsession",
                    "EventSeverity": "Informational",
                    "EventOriginalSeverity": log_record.levelname,  # duplicate of above?
                    "EventSchema": "WebSession",
                    "EventSchemaVersion": "0.2.6",
                    "IpFilterVersion": self.ip_filter_version,
                    "Url": request.url,
                    "UrlOriginal": request.url,
                    "HttpVersion": request.environ.get("SERVER_PROTOCOL"),
                    "HttpRequestMethod": request.method,
                    "HttpContentType": request.content_type,
                    "HttpContentFormat": request.mimetype,
                    "HttpReferrer": request.referrer,
                    "HttpUserAgent": str(request.user_agent),
                    "HttpRequestXff": request.headers["X-Forwarded-For"],
                    "HttpResponseTime": "N/A",
                    "HttpHost": request.host,
                    "AdditionalFields": {
                        "TraceHeaders": {"X-Amzn-Trace-Id": None},
                    },
                    "EventResult": "Success",
                    "EventResultDetails": response.status_code,
                    "FileName": "N/A",
                    "HttpStatusCode": response.status_code,
                    "dd.trace_id": "5735492756521486600",
                    "dd.span_id": "12448338029536640280",
                    "env": "test",
                    "service": "ip-filter",
                    "version": "1.0.0",
                    "container_id": "709d1c10779d47b2a84db9eef2ebd041-0265927825",
                }
            )

    @patch("main.cache")
    def test_get_package_version_no_cache(self, cache):
        cache.get.return_value = None

        assert get_package_version() == self.ip_filter_version

    @patch("main.cache")
    def test_get_package_version_cache(self, cache):
        cache.get.return_value = "6.6.6"

        assert get_package_version() == "6.6.6"
