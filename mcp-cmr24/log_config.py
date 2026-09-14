"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re

SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,&\"]+)"),
    re.compile(r"(?i)(authkey\s*[:=]\s*)([^\s,&\"]+)"),
    re.compile(r"(?i)(bearer\s+)([^\s,&\"]+)"),
)


def redact(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return text


class JsonFormatter(logging.Formatter):
    FIELDS = (
        "operation",
        "request_id",
        "duration_ms",
        "upstream_status",
        "result_code",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for field in self.FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
