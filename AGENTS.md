# Repository Guidelines

## Project Structure & Module Organization

MailTUI is a Python 3.12+ terminal mail client built with Textual and SQLite.

- `src/mailtui/` — application package and the `mail` CLI entry point.
- `src/mailtui/ui/` — Textual screens, widgets, and `app.tcss` styles.
- `src/mailtui/mail/` — IMAP, SMTP, and MIME adapters.
- `src/mailtui/db/` — SQLite store and `schema.sql`.
- `src/mailtui/services/` — sync and mail-operation orchestration.
- `tests/` — pytest suite mirroring module names (`test_sync.py`, `test_config.py`).
- `docs/` — design and intent documents; `config.example.toml` — sample config.

## Build, Test, and Development Commands

Use `uv` for all Python workflows:

```bash
uv sync                 # Install runtime and dev dependencies
uv run mail --demo      # Run the TUI with demo data
uv run mail account add # Configure a real account
uv run pytest -q        # Run the full test suite
uv run pytest tests/test_sync.py -q  # Run one module
uv build                # Build distribution artifacts
```

## Coding Style & Naming Conventions

- Use 4-space indentation, type hints, and `from __future__ import annotations`.
- Follow `snake_case` for functions, variables, and modules; `PascalCase` for classes; `UPPER_CASE` for constants.
- Prefer small dataclasses and explicit exceptions such as `ConfigError`.
- Keep TUI styling in `ui/app.tcss`, not inline where avoidable.
- No formatter or linter is configured; match surrounding code and keep imports sorted.

## Testing Guidelines

- Tests use `pytest`; configuration lives in `pyproject.toml` under `testpaths = ["tests"]`.
- Name tests `test_<behavior>` and place them in the matching `tests/test_<module>.py`.
- Use `tmp_path` for filesystem/config tests and fakes for network adapters; tests must not contact real IMAP/SMTP servers.
- Add or update tests for every behavior change and run the full suite before submitting.

## Commit & Pull Request Guidelines

The repository has no commit history yet. Use concise imperative subjects, optionally with Conventional Commit prefixes, for example `feat(ui): add attachment preview`.

- Keep commits focused and explain non-obvious behavior in the body.
- PRs should describe the change, list test commands run, and link related issues.
- Include screenshots or terminal recordings for visible TUI changes.
- Call out configuration, database-schema, or credential-handling impacts.

## Security & Configuration Tips

Never commit credentials or real mailbox data. Passwords belong in keyring, `password_command`, or temporary environment variables such as `MAILTUI_PASSWORD_WORK`. Keep local config at `~/.config/mailtui/config.toml`; runtime data lives in `~/.local/share/mailtui/`. Do not edit or commit `dist/`, `.venv/`, or caches.
