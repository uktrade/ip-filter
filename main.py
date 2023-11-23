import logging
import string
import sys
from ipaddress import ip_address
from ipaddress import ip_network
from pathlib import Path
from random import choices

import urllib3
from flask import Flask
from flask import Response
from flask import render_template
from flask import request

from config import get_ipfilter_config
from utils import constant_time_is_equal

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

app = Flask(__name__, template_folder=Path(__file__).parent, static_folder=None)
app.config.from_object("settings")


PoolClass = (
    urllib3.HTTPConnectionPool
    if app.config["SERVER_PROTO"] == "http"
    else urllib3.HTTPSConnectionPool
)
http = PoolClass(app.config["SERVER"], maxsize=1000)

logging.basicConfig(stream=sys.stdout, level=app.config["LOG_LEVEL"])
logger = logging.getLogger(__name__)

request_id_alphabet = string.ascii_letters + string.digits


def render_access_denied(client_ip, forwarded_url, request_id):
    return (
        render_template(
            "access-denied.html",
            client_ip=client_ip,
            email_name=app.config["EMAIL_NAME"],
            email=app.config["EMAIL"],
            request_id=request_id,
            forwarded_url=forwarded_url,
        ),
        403,
    )


@app.route(
    "/",
    defaults={"u_path": ""},
    methods=HTTP_METHODS,
)
@app.route(
    "/<path:u_path>",
    methods=HTTP_METHODS,
)
def handle_request(u_path):
    request_id = request.headers.get("X-B3-TraceId") or "".join(
        choices(request_id_alphabet, k=8)
    )

    logger.info("[%s] Start", request_id)

    forwarded_url = request.path
    logger.info("[%s] Forwarded URL: %s", request_id, forwarded_url)

    # Find x-forwarded-for
    try:
        x_forwarded_for = request.headers["X-Forwarded-For"]
    except KeyError:
        if request.headers.get("user-agent", "").startswith("ELB-HealthChecker"):
            return "OK"

        logger.error("[%s] X-Forwarded-For header is missing", request_id)
        return render_access_denied("Unknown", forwarded_url, request_id)

    try:
        client_ip = x_forwarded_for.split(",")[
            app.config["IP_DETERMINED_BY_X_FORWARDED_FOR_INDEX"]
        ].strip()
    except IndexError:
        logger.error(
            "[%s] Not enough addresses in x-forwarded-for %s",
            request_id,
            x_forwarded_for,
        )
        return render_access_denied("Unknown", forwarded_url, request_id)

    protected_paths = app.config["PROTECTED_PATHS"]
    public_paths = app.config["PUBLIC_PATHS"]

    if public_paths and protected_paths:
        # public and protected path settings are mutually exclusive. If both are enabled, we ignore the PROTECTED_PATHS
        # setting and emit a log message to indicate the that the IP Filter is
        # misconfigured.
        logger.warning(
            "Configuration error: PROTECTED_PATHS and PUBLIC_PATHS are mutually exclusive; ignoring PROTECTED_PATHS"
        )
        protected_paths = []

    ip_filter_enabled_and_required_for_path = app.config["IPFILTER_ENABLED"]

    # Paths are public by default unless listed in the PROTECTED_PATHS env var
    if bool(protected_paths) and not any(
        request.path.startswith(path) for path in protected_paths
    ):
        ip_filter_enabled_and_required_for_path = False

    # Paths are protected by default unless listed in the PUBLIC_PATHS env var
    if bool(public_paths) and any(
        request.path.startswith(path) for path in public_paths
    ):
        ip_filter_enabled_and_required_for_path = False

    headers_to_remove = []

    if ip_filter_enabled_and_required_for_path:
        ip_filter_rules = get_ipfilter_config(app.config["APPCONFIG_PROFILES"])

        ip_in_whitelist = any(
            ip_address(client_ip) in ip_network(ip_range)
            for ip_range in ip_filter_rules["ips"]
        )

        shared_tokens = ip_filter_rules["shared_tokens"]
        shared_token_ok = [
            shared_token["HeaderName"] in request.headers
            and constant_time_is_equal(
                shared_token["Value"].encode(),
                request.headers[shared_token["HeaderName"]].encode(),
            )
            for shared_token in shared_tokens
        ]

        def verify_credentials(app_auth: dict) -> bool:
            return (
                request.authorization
                and constant_time_is_equal(
                    app_auth["Username"].encode(),
                    request.authorization.username.encode(),
                )
                and constant_time_is_equal(
                    app_auth["Password"].encode(),
                    request.authorization.password.encode(),
                )
            )

        # TODO: reintroduce shared token check

        basic_auths = ip_filter_rules["auth"]
        basic_auths_ok = [verify_credentials(auth) for auth in basic_auths]

        # Add boolean values from basic_auths_ok to new list, if basic auth path matches current request path
        on_auth_path_and_ok = []
        for i, basic_auth_ok in enumerate(basic_auths_ok):
            if basic_auths[i]["Path"] == forwarded_url:
                on_auth_path_and_ok.append(basic_auth_ok)

        any_on_auth_path_and_ok = any(on_auth_path_and_ok)

        headers_to_remove = tuple(
            set(shared_token["HeaderName"].lower() for shared_token in shared_tokens)
        ) + ("connection",)

        # Valid basic auth username and password were supplied, but basic auth path doesn't match request url
        should_request_auth = not any_on_auth_path_and_ok and (
            ip_in_whitelist
            and (not shared_tokens or any(shared_token_ok))
            and len(on_auth_path_and_ok)
            and all(not ok for ok in on_auth_path_and_ok)
        )

        should_respond_ok_to_auth_request = (
            any_on_auth_path_and_ok
            and ip_in_whitelist
            and (not shared_tokens or any(shared_token_ok))
            and len(on_auth_path_and_ok)
        )

        if should_request_auth:
            return Response(
                "Could not verify your access level for that URL.\n"
                "You have to login with proper credentials",
                401,
                {"WWW-Authenticate": 'Basic realm="Login Required"'},
            )

        if should_respond_ok_to_auth_request:
            return "ok"

        all_checks_passed = (
            ip_in_whitelist
            and (not shared_tokens or any(shared_token_ok))
            and (not any(basic_auths) or any(basic_auths_ok))
        )

        if not all_checks_passed:
            logger.warning("[%s] Request blocked for %s", request_id, client_ip)
            return render_access_denied(client_ip, forwarded_url, request_id)

    # Proxy the request to the upstream service

    logger.info("[%s] Making request to origin", request_id)

    def downstream_data():
        while True:
            contents = request.stream.read(65536)
            if not contents:
                break
            yield contents

    origin_response = http.request(
        request.method,
        request.full_path,  #  This should be request.full_path not request.url as the latter causes issues in some cases.
        headers={
            k: v for k, v in request.headers if k.lower() not in headers_to_remove
        },
        preload_content=False,
        redirect=False,
        assert_same_host=False,
        body=downstream_data(),
    )
    logger.info("[%s] Origin response status: %s", request_id, origin_response.status)

    def release_conn():
        origin_response.release_conn()
        logger.info("[%s] End", request_id)

    downstream_response = Response(
        origin_response.stream(65536, decode_content=False),
        status=origin_response.status,
        headers=[
            (k, v)
            for k, v in origin_response.headers.items()
            if k.lower() != "connection"
        ],
    )
    downstream_response.autocorrect_location_header = False
    downstream_response.call_on_close(release_conn)

    logger.info("[%s] Starting response to client", request_id)

    return downstream_response
