import sys
import logging
from ipaddress import ip_address, ip_network
import string

from flask import request, Response, render_template
from random import choices
import urllib3

from config import get_ipfilter_config

from flask import Flask

from pathlib import Path

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

    # TODO: add shared header if enabled
    headers_to_remove = ["connection"]

    protected_paths = app.config["PROTECTED_PATHS"]
    public_paths = app.config["PUBLIC_PATHS"]

    if public_paths and protected_paths:
        # public and protected path settings are mutually exclusive. So if both are enabled, ignore PROTECTED_PATHS
        # so that paths must be explicitly whitelisted.  We also emit a log message to indicate the that the IP filter is 
        # misconfigured.
        logging.warning("Configuration error: PROTECTED_PATHS and PUBLIC_PATHS are mutually exclusive; ignoring PROTECTED_PATHS")
        protected_paths = []

    protected_paths_enabled = bool(protected_paths)
    public_paths_enabled = bool(public_paths)  
    path_is_protected = any(request.path.startswith(path) for path in protected_paths)
    path_is_public = any(request.path.startswith(path) for path in public_paths)    

    print("ip filter enabled" + str(app.config["IPFILTER_ENABLED"]))
    print(f"protected paths: {protected_paths}")
    print(f"public paths: {public_paths}")
    print("path: " + request.path)
    print(f"protected_paths_enabled: {protected_paths_enabled}")
    print(f"public_paths_is_enabled: {public_paths_enabled}")
    print(f"path_is_protected: {path_is_protected}")
    print(f"path_is_public: {path_is_public}")

    ip_filter_enabled_and_required_for_path = app.config["IPFILTER_ENABLED"]

    if bool(protected_paths) and not path_is_protected:
        ip_filter_enabled_and_required_for_path = False

    if bool(public_paths) and path_is_public:
        ip_filter_enabled_and_required_for_path = False

    if ip_filter_enabled_and_required_for_path:
        ip_filter_rules = get_ipfilter_config(app.config["APPCONFIG_PROFILES"])

        ip_in_whitelist = any(
            ip_address(client_ip) in ip_network(ip_range)
            for ip_range in ip_filter_rules["ips"]
        )

        # TODO: reintroduce shared token and basic auth checks
        all_checks_passed = ip_in_whitelist

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
        request.url,
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
