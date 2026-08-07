# Repository Guidelines

This repository contains a personal investment-decision support package. Use the
project skills for investment advice, Telegram briefings, monthly Notion reviews,
and OpenDART research instead of recreating those workflows in a prompt.

At the start of each session, activate the installed `caveman` skill at `full`
intensity. In Codex use `$caveman:caveman full`; in Claude Code use
`/caveman:caveman full`. Keep code, commit messages, and PR descriptions in normal
professional language.

## Project Structure

Core package code lives under `src/invagent/`. Keep shared configuration and
Telegram client setup in `src/invagent/core/`, Telegram fetch/download logic in
`src/invagent/telegram/`, filename parsing in `src/invagent/parsers/`, and stock
tracking in `src/invagent/tracking/`. The CLI entry point is
`src/invagent/cli.py`. Tests mirror the package in `tests/`. Runtime outputs go
to `output/`, reusable templates to `template/`, reference context to `context/`,
and design notes to `docs/`. Curated reference files in `context/` are checked into
the repository: `context/my_rules.md` holds the user's personal risk-management
rules and `context/interested_stocks.md` holds the tracked holdings list. Skills
read `context/my_rules.md` directly, so keep the rule numbering stable.

Canonical project skills live in `.agents/skills/`. Claude compatibility entries
under `.claude/skills/` point to the same directories. Edit the canonical files
only.

## Build and Development

Use `uv` for local development.

- `uv sync`: install runtime and dev dependencies.
- `uv run invagent --help`: inspect the CLI surface.
- `uv run pytest -q`: run the full test suite.
- `uv run pytest tests/test_cli.py -q`: run a focused test file.
- `uv run invagent fetch-messages --days 1`: manually verify message fetching.

Copy `.env.example` to `.env` for local configuration. Telegram commands require
`TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. OpenDART agent access requires private
per-client MCP configuration; never put its credential in repository files.

## Coding and Testing Style

Target Python 3.12+. Use 4-space indentation, type hints on public functions, and
concise docstrings for modules, classes, and non-trivial helpers. Use `snake_case`
for functions, variables, and modules, and `PascalCase` for classes. Match existing
Click patterns in `src/invagent/cli.py`. No formatter or linter is configured, so
keep imports and surrounding style consistent.

Tests use `pytest` and `pytest-asyncio`. Add or update tests for every behavior
change, especially CLI flows, configuration parsing, Telegram integrations, and
parser rules. Prefer small unit tests with mocks over live network calls. Name
test modules `tests/test_<area>.py` and functions `test_<behavior>()`.

## Git and Security

Use short imperative commit subjects focused on one logical change. Pull requests
should summarize behavior, configuration or output-path changes, related issues,
and user-facing CLI examples where applicable.

Preserve unrelated work in a dirty worktree. Do not commit `.env`, API credentials,
Telegram sessions, generated outputs, or agent-local settings. Treat `context/`
as local working data unless it is intentionally curated for the repository;
`context/my_rules.md` and `context/interested_stocks.md` are curated and tracked.
