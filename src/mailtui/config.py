"""TOML configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

from mailtui.paths import config_path


class ConfigError(ValueError):
    """Raised when the config file is invalid."""


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int = 993
    security: str = "ssl"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int = 465
    security: str = "ssl"


@dataclass(frozen=True)
class Account:
    key: str
    email: str
    username: str
    imap: ImapConfig
    smtp: SmtpConfig
    display_name: str = ""
    password_command: str | None = None
    sent_copy: str = "append"

    @property
    def from_header(self) -> str:
        if self.display_name:
            return f"{self.display_name} <{self.email}>"
        return self.email


@dataclass
class AppConfig:
    accounts: list[Account] = field(default_factory=list)
    page_size: int = 200
    initial_fetch: int = 200
    poll_interval: int = 60
    theme: str = "textual-dark"

    def account(self, key: str | None) -> Account:
        if not self.accounts:
            raise ConfigError("没有配置账号，请先运行 `mail account add`")
        if key is None:
            return self.accounts[0]
        for account in self.accounts:
            if account.key == key:
                return account
        raise ConfigError(f"账号不存在: {key}")


def _security(value: Any, name: str) -> str:
    security = str(value or "ssl").lower()
    if security not in ("ssl", "starttls"):
        raise ConfigError(f"{name}.security 必须是 ssl 或 starttls")
    return security


def _port(value: Any, default: int, name: str) -> int:
    try:
        port = int(value if value is not None else default)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name}.port 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name}.port 超出范围")
    return port


def _account_from_dict(data: dict[str, Any]) -> Account:
    key = str(data.get("key", "")).strip()
    email = str(data.get("email", "")).strip()
    username = str(data.get("username", "") or email).strip()
    if not key or not email or not username:
        raise ConfigError("账号缺少 key/email/username")
    imap_data = data.get("imap") or {}
    smtp_data = data.get("smtp") or {}
    if not imap_data.get("host") or not smtp_data.get("host"):
        raise ConfigError(f"账号 {key} 缺少 IMAP/SMTP host")
    sent_copy = str(data.get("sent_copy", "append")).lower()
    if sent_copy not in ("append", "none"):
        raise ConfigError(f"账号 {key} 的 sent_copy 必须是 append 或 none")
    return Account(
        key=key,
        email=email,
        username=username,
        display_name=str(data.get("display_name", "")),
        password_command=data.get("password_command"),
        sent_copy=sent_copy,
        imap=ImapConfig(
            host=str(imap_data["host"]),
            port=_port(imap_data.get("port"), 993, f"{key}.imap"),
            security=_security(imap_data.get("security"), f"{key}.imap"),
        ),
        smtp=SmtpConfig(
            host=str(smtp_data["host"]),
            port=_port(smtp_data.get("port"), 465, f"{key}.smtp"),
            security=_security(smtp_data.get("security"), f"{key}.smtp"),
        ),
    )


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    if not path.exists():
        return AppConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取配置 {path}: {exc}") from exc

    accounts = [_account_from_dict(item) for item in raw.get("accounts", [])]
    keys = [account.key for account in accounts]
    if len(keys) != len(set(keys)):
        raise ConfigError("账号 key 必须唯一")

    ui = raw.get("ui", {})
    sync = raw.get("sync", {})
    return AppConfig(
        accounts=accounts,
        page_size=max(10, int(ui.get("page_size", 200))),
        initial_fetch=max(1, int(sync.get("initial_fetch", 200))),
        poll_interval=max(10, int(sync.get("poll_interval", 60))),
        theme=str(ui.get("theme", "textual-dark")),
    )


def load_raw(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def write_raw(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def add_account(
    *,
    key: str,
    email: str,
    username: str,
    imap_host: str,
    imap_port: int,
    imap_security: str,
    smtp_host: str,
    smtp_port: int,
    smtp_security: str,
    display_name: str = "",
    password_command: str | None = None,
    path: Path | None = None,
) -> None:
    if not key.strip() or not email.strip() or not username.strip():
        raise ConfigError("账号 key/email/username 不能为空")
    if not imap_host.strip() or not smtp_host.strip():
        raise ConfigError("IMAP/SMTP host 不能为空")
    data = load_raw(path)
    accounts = list(data.get("accounts", []))
    if any(item.get("key") == key for item in accounts):
        raise ConfigError(f"账号 key 已存在: {key}")
    accounts.append(
        {
            "key": key,
            "email": email,
            "username": username,
            "display_name": display_name,
            "sent_copy": "append",
            "imap": {
                "host": imap_host,
                "port": imap_port,
                "security": imap_security,
            },
            "smtp": {
                "host": smtp_host,
                "port": smtp_port,
                "security": smtp_security,
            },
            **({"password_command": password_command} if password_command else {}),
        }
    )
    data["accounts"] = accounts
    data.setdefault("ui", {"page_size": 200})
    data.setdefault("sync", {"poll_interval": 60, "initial_fetch": 200})
    write_raw(data, path)


def remove_account(key: str, path: Path | None = None) -> None:
    data = load_raw(path)
    accounts = [item for item in data.get("accounts", []) if item.get("key") != key]
    if len(accounts) == len(data.get("accounts", [])):
        raise ConfigError(f"账号不存在: {key}")
    data["accounts"] = accounts
    write_raw(data, path)
