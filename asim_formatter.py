import json
import logging
import os
from datetime import datetime

import ddtrace
from ddtrace import tracer
from flask import Request
from flask import Response
from flask import has_request_context
from flask import request

from utils import get_package_version


class ASIMFormatter(logging.Formatter):



    def _get_container_id(self):
        """
        The dockerId (container Id) is available via the metadata endpoint. However, the it looks like it is embedded in the
        metadata URL,eg:
        ECS_CONTAINER_METADATA_URI=http://169.254.170.2/v3/709d1c10779d47b2a84db9eef2ebd041-0265927825
        See: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-metadata-endpoint-v4-response.html
        """
        try:
            return os.environ["ECS_CONTAINER_METADATA_URI"].split("/")[-1]
        except (KeyError, IndexError):
            return ""

    def _datadog_trace_dict(self):

        # source: https://docs.datadoghq.com/tracing/other_telemetry/connect_logs_and_traces/python/

        event_dict = {}

        span = tracer.current_span()
        trace_id, span_id = (str((1 << 64) - 1 & span.trace_id), span.span_id) if span else (None, None)

        # add ids to structlog event dictionary
        event_dict['dd.trace_id'] = str(trace_id or 0)
        event_dict['dd.span_id'] = str(span_id or 0)

        # add the env, service, and version configured for the tracer
        event_dict['env'] = ddtrace.config.env or ""
        event_dict['service'] = ddtrace.config.service or ""
        event_dict['version'] = ddtrace.config.version or ""

        event_dict['container_id'] = self._get_container_id()

        return event_dict

    def _get_event_result(self, response: Response) -> str:
        event_result = "Success" if response.status_code < 400 else "Failure"

        return event_result

    def _get_file_name(self, response: Response) -> str:
        content_disposition = response.headers.get("Content-Disposition")

        if content_disposition:
            return content_disposition.split("filename=")[-1].strip('"')

        return "N/A"

    def _get_event_severity(self, log_level: str) -> str:
        map = {
            "DEBUG": "Informational",
            "INFO": "Informational",
            "WARNING": "Low",
            "ERROR": "Medium",
            "CRITICAL": "High",
        }
        return map[log_level]

    def get_log_dict(self, record: logging.LogRecord) -> dict:
        log_time = datetime.utcfromtimestamp(record.created).isoformat()

        return {
            "EventMessage": record.msg,
            "EventCount": 1,
            "EventStartTime": log_time,
            "EventEndTime": log_time,
            "EventType": "HTTPsession",
            "EventSeverity": self._get_event_severity(record.levelname),
            "EventOriginalSeverity": record.levelname,  # duplicate of above?
            "EventSchema": "WebSession",
            "EventSchemaVersion": "0.2.6",
            "IpFilterVersion": get_package_version(),
        }

        # TODO: look at expanding to include other fields from schema: https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-web

    def get_request_dict(self, request: Request) -> dict:
        request_dict = {
            "Url": request.url,
            "UrlOriginal": request.url,
            "HttpVersion": request.environ.get("SERVER_PROTOCOL"),
            "HttpRequestMethod": request.method,
            "HttpContentType": request.content_type,
            "HttpContentFormat": request.mimetype,
            "HttpReferrer": request.referrer,
            "HttpUserAgent": str(request.user_agent),
            "HttpRequestXff": request.headers.get("X-Forwarded-For"),
            "HttpResponseTime": "N/A",
            "HttpHost": request.host,
            "AdditionalFields": {
                "TraceHeaders": {},
            },
        }

        for trace_header in os.environ.get("DLFA_TRACE_HEADERS", ("X-Amzn-Trace-Id",)):
            request_dict["AdditionalFields"]["TraceHeaders"][
                trace_header
            ] = request.headers.get(trace_header, None)

        return request_dict

    def get_response_dict(self, response: Response) -> dict:
        return {
            "EventResult": self._get_event_result(response),
            "EventResultDetails": response.status_code,
            "FileName": self._get_file_name(response),
            "HttpStatusCode": response.status_code,
        }

    def format(self, record: logging.LogRecord) -> str:
        log_dict = self.get_log_dict(record)

        if has_request_context():
            request_dict = self.get_request_dict(request)
            log_dict = log_dict | request_dict

        if hasattr(record, "response"):
            response_dict = self.get_response_dict(record.response)
            log_dict = log_dict | response_dict

        log_dict.update(self._datadog_trace_dict())

        return json.dumps(log_dict)
