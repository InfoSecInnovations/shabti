from pythonjsonlogger.core import RESERVED_ATTRS
from pythonjsonlogger.jsonlogger import JsonFormatter
import time
import os


class UtcFormatter(JsonFormatter):
    converter = time.gmtime


def logging_config():
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "format": "%(levelprefix)s %(message)s",
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "format": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
            "shabti": {
                "()": UtcFormatter,
                "format": "%(asctime)s %(levelname)s %(message)s %(name)s",
                "json_ensure_ascii": False,
                "rename_fields": {
                    "levelname": "severity",
                    "asctime": "eventtime",
                    "name": "log_source",
                },
                "reserved_attrs": [*RESERVED_ATTRS, "color_message"],
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "shabti": {
                "formatter": "shabti",
                "class": "logging.FileHandler",
                "filename": os.path.join(os.getenv("SHABTI_LOG_DIR"), "shabti_log.json")
                if os.getenv("SHABTI_LOG_DIR")
                else "./shabti_log.json",
                "mode": "a",
                "encoding": None,
                "delay": False,
                "errors": None,
            },
        },
        "loggers": {
            "uvicorn.error": {
                "level": "INFO",
                "handlers": ["default", "shabti"],
                "propagate": False,
            },
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["access"],
                "propagate": False,
            },
            "shabti": {"level": "INFO", "handlers": ["shabti"], "propagate": False},
        },
    }
