"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from mailtui import __version__
from mailtui.config import AppConfig, ConfigError, add_account, load_config, remove_account
from mailtui.credentials import (
    CredentialError,
    delete_password,
    get_password,
    keyring_available,
    keyring_backend_name,
    store_password,
)
from mailtui.db.store import Store
from mailtui.logging import setup_logging
from mailtui.lock import InstanceLock
from mailtui.paths import config_path, db_path, ensure_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mail", description="MailTUI")
    parser.add_argument("--config", type=Path, help="配置文件路径")
    parser.add_argument("--account", help="启动时使用的账号 key")
    parser.add_argument("--demo", action="store_true", help="使用内置演示数据")
    parser.add_argument("--version", action="version", version=__version__)

    sub = parser.add_subparsers(dest="command")

    account = sub.add_parser("account", help="管理账号")
    account_sub = account.add_subparsers(dest="account_command")
    add = account_sub.add_parser("add", help="添加账号")
    add.add_argument("--key")
    add.add_argument("--email")
    add.add_argument("--username")
    add.add_argument("--display-name", default=None)
    add.add_argument("--imap-host")
    add.add_argument("--imap-port", type=int, default=993)
    add.add_argument("--imap-security", choices=["ssl", "starttls"], default="ssl")
    add.add_argument("--smtp-host")
    add.add_argument("--smtp-port", type=int, default=465)
    add.add_argument("--smtp-security", choices=["ssl", "starttls"], default="ssl")
    add.add_argument("--password-command")
    account_sub.add_parser("list", help="列出账号")
    remove = account_sub.add_parser("remove", help="删除账号")
    remove.add_argument("key")
    remove.add_argument("--purge-data", action="store_true")

    db = sub.add_parser("db", help="数据库维护")
    db_sub = db.add_subparsers(dest="db_command")
    db_sub.add_parser("migrate", help="执行迁移")
    db_sub.add_parser("rebuild-fts", help="重建全文索引")
    db_sub.add_parser("vacuum", help="压缩数据库")

    doctor = sub.add_parser("doctor", help="诊断配置与数据库")
    doctor.add_argument("--stats", action="store_true")
    doctor.add_argument("--check", action="store_true", help="测试 IMAP 登录")
    return parser


def _prompt(label: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def command_account_add(args: argparse.Namespace) -> None:
    key = args.key or _prompt("账号 key（例如 work）")
    email = args.email or _prompt("邮箱地址")
    username = args.username or _prompt("登录用户名", email)
    display_name = (
        args.display_name
        if args.display_name is not None
        else _prompt("显示名称", "")
    )
    domain = email.split("@")[-1] if "@" in email else ""
    imap_host = args.imap_host or _prompt(
        "IMAP host", f"imap.{domain}" if domain else ""
    )
    smtp_host = args.smtp_host or _prompt(
        "SMTP host", f"smtp.{domain}" if domain else ""
    )

    password = ""
    if not args.password_command and sys.stdin.isatty():
        password = getpass.getpass(
            "邮箱密码/应用专用密码（直接回车跳过）: "
        )
    add_account(
        key=key,
        email=email,
        username=username,
        display_name=display_name,
        imap_host=imap_host,
        imap_port=args.imap_port,
        imap_security=args.imap_security,
        smtp_host=smtp_host,
        smtp_port=args.smtp_port,
        smtp_security=args.smtp_security,
        password_command=args.password_command,
        path=args.config,
    )
    if password:
        if store_password(key, password):
            print("密码已保存到系统钥匙串。")
        else:
            print("系统钥匙串不可用；请配置 password_command 或环境变量。")
    print(f"账号已添加: {key} <{email}>")
    print(f"配置文件: {args.config or config_path()}")


def command_account_list(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    if not config.accounts:
        print("还没有账号。运行 `mail account add` 添加。")
        return
    for account in config.accounts:
        print(
            f"{account.key:12} {account.email:30} "
            f"imap={account.imap.host}:{account.imap.port} "
            f"smtp={account.smtp.host}:{account.smtp.port} "
            f"sent={account.sent_copy}"
        )


def command_account_remove(args: argparse.Namespace) -> None:
    remove_account(args.key, args.config)
    delete_password(args.key)
    if args.purge_data:
        store = Store()
        try:
            store.purge_account(args.key)
        finally:
            store.close()
        print(f"已删除账号及其本地数据: {args.key}")
    else:
        print(f"已删除账号配置: {args.key}（本地数据保留）")


def command_db(args: argparse.Namespace) -> None:
    store = Store()
    try:
        if args.db_command == "rebuild-fts":
            store.rebuild_fts()
            print("全文索引已重建。")
        elif args.db_command == "vacuum":
            store.vacuum()
            print("数据库已压缩。")
        else:
            print(f"数据库已迁移: {db_path()}")
    finally:
        store.close()


def command_doctor(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    print(f"配置文件: {args.config or config_path()}")
    print(f"数据库:   {db_path()}")
    print(f"keyring:  {keyring_backend_name()}")
    if not keyring_available():
        print("  提示: 当前 keyring 不可用，密码不会持久化；请配置 password_command。")
    print(f"账号数:   {len(config.accounts)}")
    for account in config.accounts:
        print(
            f"  - {account.key}: {account.email} "
            f"({account.imap.host}:{account.imap.port}, "
            f"{account.smtp.host}:{account.smtp.port})"
        )
    if args.stats:
        store = Store()
        try:
            for account in config.accounts:
                print(f"[{account.key}]")
                for folder in store.list_folders(account.key):
                    print(
                        f"  {folder['name']:<24} "
                        f"total={folder.get('total', 0):<5} "
                        f"unread={folder.get('unread', 0)}"
                    )
        finally:
            store.close()
    if args.check:
        _check_accounts(config)


def _check_accounts(config: AppConfig) -> None:
    from mailtui.mail.imap import ImapClient

    for account in config.accounts:
        password = get_password(account)
        if not password:
            print(f"[{account.key}] 跳过：没有可用密码")
            continue

        async def check() -> int:
            client = ImapClient(account, password)
            try:
                folders = await client.list_folders()
                return len(folders)
            finally:
                await client.close()

        try:
            print(f"[{account.key}] IMAP 登录成功，文件夹数: {asyncio.run(check())}")
        except Exception as exc:
            print(f"[{account.key}] IMAP 检查失败: {exc}")


def launch(args: argparse.Namespace) -> None:
    if args.demo:
        from mailtui.ui.demo_app import DemoApp

        DemoApp(demo=True).run()
        return

    config = load_config(args.config)
    if not config.accounts:
        from mailtui.ui.onboarding import OnboardingApp

        added = OnboardingApp(args.config or config_path()).run()
        if not added:
            return
        config = load_config(args.config)
        if not config.accounts:
            print("仍没有可用账号，已退出。", file=sys.stderr)
            return
    account = config.account(args.account)
    password = get_password(account)
    if not password:
        password = getpass.getpass(
            f"{account.key} <{account.email}> 的密码/应用专用密码: "
        )
        if not password:
            print("没有密码，无法连接邮箱。", file=sys.stderr)
            raise SystemExit(2)
        if store_password(account.key, password):
            print("密码已保存到系统钥匙串。")
        else:
            print(
                "警告: 系统钥匙串不可用，密码不会持久化；"
                "请配置 password_command 或 MAILTUI_PASSWORD_<KEY>。",
                file=sys.stderr,
            )

    from mailtui.ui.app import MailApp

    try:
        with InstanceLock():
            MailApp(
                account,
                password,
                config,
                config_file=args.config or config_path(),
            ).run()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def main(argv: list[str] | None = None) -> None:
    ensure_dirs()
    setup_logging()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "account":
            if args.account_command == "add":
                command_account_add(args)
            elif args.account_command == "list":
                command_account_list(args)
            elif args.account_command == "remove":
                command_account_remove(args)
            else:
                print("用法: mail account {add,list,remove}", file=sys.stderr)
                raise SystemExit(2)
        elif args.command == "db":
            command_db(args)
        elif args.command == "doctor":
            command_doctor(args)
        else:
            launch(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except CredentialError as exc:
        print(f"凭据错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
