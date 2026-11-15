"""Application paths."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_state_dir

APP_NAME = "mailtui"


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def data_dir() -> Path:
    return Path(user_data_dir(APP_NAME))


def state_dir() -> Path:
    return Path(user_state_dir(APP_NAME))


def config_path() -> Path:
    return Path(os.environ.get("MAILTUI_CONFIG", config_dir() / "config.toml"))


def db_path() -> Path:
    return Path(os.environ.get("MAILTUI_DB", data_dir() / "mail.db"))


def files_dir() -> Path:
    return data_dir() / "files"


def ensure_dirs() -> None:
    for path in (config_dir(), data_dir(), state_dir(), files_dir()):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
