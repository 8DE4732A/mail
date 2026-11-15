"""Credential lookup and storage."""

from __future__ import annotations

import os
import shlex
import subprocess

import keyring

from mailtui.config import Account

SERVICE = "mailtui"


class CredentialError(RuntimeError):
    """Raised when a password cannot be resolved."""


def _env_name(account_key: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in account_key).upper()
    return f"MAILTUI_PASSWORD_{safe}"


def get_password(account: Account) -> str | None:
    env_value = os.environ.get(_env_name(account.key))
    if env_value:
        return env_value

    if account.password_command:
        try:
            result = subprocess.run(
                shlex.split(account.password_command),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialError(
                f"password_command 执行失败: {account.password_command}"
            ) from exc
        password = result.stdout.strip()
        if password:
            return password

    try:
        return keyring.get_password(SERVICE, account.key)
    except Exception:
        return None


def store_password(account_key: str, password: str) -> bool:
    try:
        keyring.set_password(SERVICE, account_key, password)
        return True
    except Exception:
        return False


def delete_password(account_key: str) -> None:
    try:
        keyring.delete_password(SERVICE, account_key)
    except Exception:
        pass


def keyring_backend_name() -> str:
    try:
        backend = keyring.get_keyring()
        return f"{type(backend).__module__}.{type(backend).__name__}"
    except Exception as exc:
        return f"不可用 ({type(exc).__name__})"


def keyring_available() -> bool:
    return not keyring_backend_name().startswith("keyring.backends.fail")
