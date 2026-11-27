import json
import logging


class RequestJsonFormatter(logging.Formatter):
    """Only allowlisted operational request metadata is emitted as JSON."""

    fields = (
        'request_id',
        'method',
        'path',
        'status_code',
        'latency_ms',
        'error_class',
    )

    def format(self, record):
        payload = {
            'event': record.getMessage(),
            'level': record.levelname,
            'logger': record.name,
            'timestamp': self.formatTime(record, self.datefmt),
        }
        for field in self.fields:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
