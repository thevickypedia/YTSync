"""This is an entrypoint specific for docker containers."""

import json
import os
import pathlib
from datetime import datetime

db_filename = lambda key, default: os.environ.get(key) or os.environ.get(key.upper()) or default  # noqa: E731
logs_dir = pathlib.Path(__file__).parent / "logs"
data_dir = pathlib.Path(__file__).parent / "data"

DEFAULT_LOG_FILENAME: str = datetime.now().strftime(str(logs_dir / "ytsync_%d-%m-%Y.log"))
data_dir.mkdir(parents=True, exist_ok=True)
logs_dir.mkdir(parents=True, exist_ok=True)

log_level = os.environ.get("LOG_LEVEL", "DEBUG")
assert log_level in (
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
), "log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"

log_config = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s %(levelprefix)-9s %(name)s - [%(funcName)s:%(lineno)d] - %(message)s",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(asctime)s %(levelprefix)-9s %(name)s -: %(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
        "error": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s %(levelprefix)-9s %(name)s - [%(funcName)s:%(lineno)d] - %(message)s",
            "use_colors": False,
        },
    },
    "handlers": {
        "default": {
            "class": "logging.FileHandler",
            "formatter": "default",
            "filename": DEFAULT_LOG_FILENAME,
        },
        "access": {
            "class": "logging.FileHandler",
            "formatter": "access",
            "filename": DEFAULT_LOG_FILENAME,
        },
        "error": {
            "class": "logging.FileHandler",
            "formatter": "error",
            "filename": DEFAULT_LOG_FILENAME,
        },
    },
    "loggers": {
        "ytsync": {
            "propagate": False,
            "level": log_level,
            "handlers": ["default"],
        },
        "uvicorn": {
            "propagate": False,
            "level": log_level,
            "handlers": ["default"],
        },
        "uvicorn.error": {
            "propagate": False,
            "level": log_level,
            "handlers": ["error"],
        },
        "uvicorn.access": {
            "propagate": False,
            "level": log_level,
            "handlers": ["access"],
        },
    },
}

os.environ["log_config"] = json.dumps(log_config)


if __name__ == "__main__":
    import ytsync

    ytsync.start()
