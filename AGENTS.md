# Repository Guidelines

## Project Structure & Module Organization
Core package code lives under `src/invagent/`. Keep shared configuration and Telegram client setup in `src/invagent/core/`, Telegram fetch/download logic in `src/invagent/telegram/`, filename parsing in `src/invagent/parsers/`, and stock tracking in `src/invagent/tracking/`. The CLI entry point is `src/invagent/cli.py`. Tests mirror the package in `tests/` with files such as `tests/test_core_config.py` and `tests/test_parsers_pdf_namer.py`. Runtime outputs go to `output/`, reusable templates to `template/`, reference context to `context/`, and design notes to `docs/`.

## Build, Test, and Development Commands
Use `uv` for local development.

- `uv sync`: install runtime and dev dependencies from `pyproject.toml` and `uv.lock`.
- `uv run invagent --help`: inspect the CLI surface.
- `uv run pytest -q`: run the full test suite.
- `uv run pytest tests/test_cli.py -q`: run a focused test file during iteration.
- `uv run invagent fetch-messages --days 1`: example command for manual verification.

Set up local config with `cp .env.example .env`, then export `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` before Telegram-related commands.

## Coding Style & Naming Conventions
Target Python 3.12+ and follow the existing style: 4-space indentation, type hints on public functions, and concise docstrings for modules, classes, and non-trivial helpers. Use `snake_case` for functions, variables, and module names; use `PascalCase` for classes like `TelegramClientManager` and `PDFNamer`. Keep CLI commands and options consistent with current Click patterns in `src/invagent/cli.py`. No formatter or linter is configured here, so match the surrounding code closely and keep imports/order clean.

## Testing Guidelines
Tests use `pytest` and `pytest-asyncio`. Add or update tests with every behavior change, especially for CLI flows, config parsing, Telegram integrations, and parser rules. Name new tests `tests/test_<area>.py` and individual test functions `test_<behavior>()`. Prefer small unit tests with mocks over live network calls.

## Commit & Pull Request Guidelines
Recent commits use short, imperative subjects such as `Implement PDFNamer` and `Change channel names for downloading PDFs`. Keep commit messages focused on one logical change. Pull requests should include a clear summary, note any config or output-path changes, link related issues if present, and include sample CLI output when user-facing behavior changes.

## Security & Configuration Tips
Do not commit `.env`, Telegram credentials, or generated session files. Keep generated data in `output/` and treat `context/` content as local working data unless intentionally curated for the repo.
