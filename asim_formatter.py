import json
import logging
from datetime import datetime

from flask import has_request_context
from flask import request


class ASIMFormatter(logging.Formatter):
    def _get_event_result(self, response) -> str:
        event_result = "Success" if response.status_code < 400 else "Failure"

        return event_result

    def _get_file_name(self, request) -> str:
        if not request.files:
            return "N/A"
        if len(request.files.keys()) == 1:
            return request.files.keys()[0]

        return ";".join(request.files.keys()[0])

    def _get_event_severity(self, log_level):
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
            # Event fields...
            "EventMessage": record.msg,
            "EventCount": 1,
            "EventStartTime": log_time,
            "EventEndTime": log_time,
            "EventType": "HTTPsession",
            "EventSeverity": self._get_event_severity(record.levelname),
            "EventOriginalSeverity": record.levelname,  # duplicate of above?
            "EventSchema": "WebSession",
            "EventSchemaVersion": "0.2.6",
            # Other fields...
            "AdditionalFields": {
                "TraceHeaders": {},
            },
        }

        # Missing EventUid, EventOriginalUid, EventOriginalType, EventOriginalSubType, EventOriginalResultDetails, EventProduct

    def get_request_dict(self, request):
        return {
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
            # TODO: add better support for multi-file upload and other file fields e.g. FileSize
            "FileName": self._get_file_name(request),
        }

    def get_response_dict(self, response):
        return {
            "EventResult": self._get_event_result(response),
            "EventResultDetails": response.status_code,
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

        return json.dumps(log_dict)
