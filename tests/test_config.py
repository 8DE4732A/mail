from pathlib import Path

from mailtui.config import add_account, load_config, remove_account


def test_add_and_load_account(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    add_account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        display_name="Me",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_security="ssl",
        path=path,
    )
    config = load_config(path)
    assert config.accounts[0].key == "work"
    assert config.accounts[0].imap.host == "imap.example.com"

    remove_account("work", path)
    assert load_config(path).accounts == []
