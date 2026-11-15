"""Single-instance lock."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from mailtui.paths import state_dir


class InstanceLock:
    """Prevent two processes from writing the same SQLite database."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_dir() / "mail.lock"
        self._file: object | None = None

    def __enter__(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except OSError as exc:
            self._file.close()
            self._file = None
            raise RuntimeError("另一个 MailTUI 实例正在运行") from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        try:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self._file.close()
        self._file = None
