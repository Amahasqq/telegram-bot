import json
import logging
import sys
from typing import Any


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id
        if hasattr(record, "update_id"):
            log_entry["update_id"] = record.update_id
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms
        if hasattr(record, "correlation_id"):
            log_entry["correlation_id"] = record.correlation_id
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
