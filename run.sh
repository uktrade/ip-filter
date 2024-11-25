#/bin/bash

DD_TRACE_HTTPLIB_ENABLED=true ddtrace-run gunicorn main:app -b 0.0.0.0:$PORT
