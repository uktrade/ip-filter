import gzip
import os
import signal
import socket
import subprocess
import sys
import time
import urllib
from io import BytesIO

from flask import Flask
from flask import Response
from flask import request
from flask import abort
from multiprocess import Process
from werkzeug.routing import Rule
from werkzeug.serving import WSGIRequestHandler


def create_filter(port, env=()):
    def stop():
        process.terminate()
        process.wait()

    with open("Procfile", "r") as f:
        lines = f.readlines()
    for line in lines:
        name, _, command = line.partition(":")
        if name.strip() == "web":
            break

    default_env = {
        "PORT": str(port),
        "EMAIL_NAME": "the Department for International Trade WebOps team",
        "EMAIL": "test@test.test",
        "LOG_LEVEL": "DEBUG",
        "DEBUG": "True",
    }

    process = subprocess.Popen(
        ["bash", "-c", command.strip()],
        env={
            **os.environ,
            **default_env,
            **dict(env),
        },
    )

    return stop


def create_origin(port):
    def start():
        # Avoid warning about this not a prod server
        os.environ["FLASK_ENV"] = "development"
        origin_app = Flask("origin")

        origin_app.endpoint("chunked")(chunked)
        origin_app.url_map.add(Rule("/chunked", endpoint="chunked"))

        origin_app.endpoint("multiple-cookies")(multiple_cookies)
        origin_app.url_map.add(Rule("/multiple-cookies", endpoint="multiple-cookies"))

        origin_app.endpoint("gzipped")(gzipped)
        origin_app.url_map.add(Rule("/gzipped", endpoint="gzipped"))

        origin_app.endpoint("echo")(echo)
        origin_app.url_map.add(Rule("/", endpoint="echo"))
        origin_app.url_map.add(Rule("/<path:path>", endpoint="echo"))

        def _stop(_, __):
            sys.exit()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        WSGIRequestHandler.protocol_version = "HTTP/1.1"

        try:
            origin_app.run(host="", port=port, debug=False)
        except SystemExit:
            # origin_app.run doesn't seem to have a good way of killing the
            # server, and need to exit cleanly for code coverage to be saved
            pass

    def chunked():
        num_bytes = int(request.headers["x-chunked-num-bytes"])

        def data():
            chunk = b"-" * num_bytes
            yield chunk

        # transfer-encoding: chunked is set by the Flask server
        return Response(
            data(),
            status=200,
        )

    def multiple_cookies():
        cookies = request.headers["x-multiple-cookies"].split(",")
        return Response(
            b"", headers=[("set-cookie", cookie) for cookie in cookies], status=200
        )

    def gzipped():
        gzip_buffer = BytesIO()
        gzip_file = gzip.GzipFile(mode="wb", compresslevel=9, fileobj=gzip_buffer)
        gzip_file.write(request.stream.read())
        gzip_file.close()
        zipped = gzip_buffer.getvalue()

        return Response(
            zipped,
            headers=[
                ("content-encoding", "gzip"),
                ("content-length", str(len(zipped))),
            ],
            status=200,
        )

    def echo(path="/"):
        # Echo via headers to be able to assert more on HEAD requests that
        # have no response body

        def _extract_path(url):
            parts = urllib.parse.urlparse(url)
            return parts.path + "?" + parts.query

        response_header_prefix = "x-echo-response-header-"
        headers = (
                [
                    ("x-echo-method", request.method),
                    ("x-echo-raw-uri", _extract_path(request.environ["RAW_URI"])),
                    ("x-echo-remote-port", request.environ["REMOTE_PORT"]),
                ]
                + [("x-echo-header-" + k, v) for k, v in request.headers.items()]
                + [
                    (k[len(response_header_prefix) :], v)
                    for k, v in request.headers.items()
                    if k.lower().startswith(response_header_prefix)
                ]
        )

        return Response(
            request.stream.read(),
            headers=headers,
            status=int(request.headers.get("x-echo-response-status", "200")),
        )

    def stop():
        process.terminate()
        process.join()

    process = Process(target=start)
    process.start()

    return stop


def wait_until_connectable(port, max_attempts=1000):
    for i in range(0, max_attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except (OSError, ConnectionRefusedError):
            if i == max_attempts - 1:
                raise
            time.sleep(0.01)

def create_appconfig_agent(port, config_map=None):
    default_config_map = {
        "testapp:testenv:testconfig": """
IpRanges:
  - 1.1.1.1/32
BasicAuth: []
"""
    }

    def start():
        # Avoid warning about this not a prod server
        os.environ["FLASK_ENV"] = "development"
        origin_app = Flask("appconfig")

        origin_app.endpoint("config")(config_view)
        origin_app.url_map.add(
            Rule(
                "/applications/<string:application>/environments/<string:environment>/configurations/<string:configuration>/",
                endpoint="config",
            )
        )

        def _stop(_, __):
            sys.exit()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        WSGIRequestHandler.protocol_version = "HTTP/1.1"

        try:
            origin_app.run(host="", port=port, debug=False)
        except SystemExit:
            # origin_app.run doesn't seem to have a good way of killing the
            # server, and need to exit cleanly for code coverage to be saved
            pass

    def config_view(application, environment, configuration):
        key = f"{application}:{environment}:{configuration}"

        config = default_config_map | (config_map or {})

        if key not in config:
            abort(404)

        return Response(
            config[key],
            headers={},
            status=200,
        )

    def stop():
        process.terminate()
        process.join()

    process = Process(target=start)
    process.start()

    return stop


