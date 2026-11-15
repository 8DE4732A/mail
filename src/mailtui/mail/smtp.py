"""SMTP sending."""

from __future__ import annotations

import ssl
from email import policy
from email.parser import BytesParser

import aiosmtplib

from mailtui.config import Account


class SmtpError(RuntimeError):
    """Raised when sending fails."""


class TransientSmtpError(SmtpError):
    """A retryable SMTP failure."""


class PermanentSmtpError(SmtpError):
    """A non-retryable SMTP failure."""


class AmbiguousSmtpError(SmtpError):
    """The connection failed during/after DATA; the server may have accepted."""


def _raise_response_error(exc: aiosmtplib.SMTPResponseException) -> None:
    message = f"SMTP {exc.code}: {exc.message}"
    if 400 <= int(exc.code) < 500:
        raise TransientSmtpError(message) from exc
    raise PermanentSmtpError(message) from exc


async def send_message(
    account: Account,
    password: str,
    raw: bytes,
    recipients: list[str],
    *,
    tls_context: ssl.SSLContext | None = None,
) -> None:
    smtp = account.smtp
    client = aiosmtplib.SMTP(
        hostname=smtp.host,
        port=smtp.port,
        timeout=30,
        use_tls=smtp.security == "ssl",
        start_tls=smtp.security == "starttls",
        validate_certs=True,
        tls_context=tls_context,
    )
    try:
        await client.connect()
    except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
        raise TransientSmtpError(f"SMTP 连接失败: {exc}") from exc

    try:
        try:
            if account.username:
                await client.login(account.username, password)
        except aiosmtplib.SMTPResponseException as exc:
            _raise_response_error(exc)
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            raise TransientSmtpError(f"SMTP 登录失败: {exc}") from exc

        try:
            message = BytesParser(policy=policy.SMTP).parsebytes(raw)
            await client.send_message(
                message,
                sender=account.email,
                recipients=recipients,
            )
        except aiosmtplib.SMTPResponseException as exc:
            _raise_response_error(exc)
        except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
            raise AmbiguousSmtpError(
                f"SMTP 断连且发送结果不确定，可能已送达: {exc}"
            ) from exc
    finally:
        try:
            await client.quit()
        except Exception:
            pass
