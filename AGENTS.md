# Repository Guidelines

This repository contains a personal investment-decision support package. Use the
project skills for investment advice, single-stock analysis, Telegram briefings,
monthly Notion reviews, and OpenDART research instead of recreating those
workflows in a prompt.

At the start of each session, activate the installed `caveman` skill at `full`
intensity. In Codex use `$caveman:caveman full`; in Claude Code use
`/caveman:caveman full`. Keep code, commit messages, and PR descriptions in normal
professional language.

## Project Structure

Core package code lives under `src/invagent/`. Keep shared configuration and
Telegram client setup in `src/invagent/core/`, Telegram fetch logic in
`src/invagent/telegram/`, and stock tracking in `src/invagent/tracking/`. The
CLI entry point is `src/invagent/cli.py`. Tests mirror the package in `tests/`.
Runtime outputs go to `output/`, reusable templates to `template/`, reference
context to `context/`, and design notes to `docs/`. Curated reference files in
`context/` are checked into the repository: `context/my_rules.md` holds the
user's personal risk-management rules and `context/interested_stocks.md` holds
the tracked holdings list. Skills read `context/my_rules.md` directly, so keep
the rule numbering stable.

The `analyze-stock` skill reads the local analyst-report archive at
`~/1_Investment/리포트/<초성>/<종목명>/` (PDF only, outside this repository). Override
the location with `INVAGENT_REPORT_ARCHIVE`; never hardcode an absolute path in
skill files. It also searches the accumulated briefing archive under
`output/telegram-daily/` (index, theme files, daily briefings) through
`.agents/skills/analyze-stock/scripts/find_mentions.py`, which emits `path:line`
locators rather than content — the archive's index and theme lines run to
several kilobytes each and must be read from the original file, never truncated.
Reports are filed under `output/reports/` in three branches:
`종목/<초성>/<종목명>_<yyyy-mm-dd>.md` for single-stock analyses (the 초성 folder follows
the same rule as the PDF archive, via `find_reports.chosung_dir()`),
`산업/<yyyy-mm-dd>_<주제>.md` for sector or theme comparisons, and
`기타/<yyyy-mm-dd>_<주제>.md` for everything else. The target path is computed by
`.agents/skills/analyze-stock/scripts/find_prior_report.py`, not by hand, and
the section layout is owned by `template/stock_analysis.md`. When a report for
the same stock already exists, the skill inherits it instead of starting over:
it reads the prior report, collects only what is newer than that report's date,
corrects errors it finds, rewrites the investment call from scratch, and moves
the file to today's date — writing the new file first and deleting the old one
only afterwards, since `output/` is gitignored and has no recovery path.

Quote, multiples, per-broker target-price history, consensus estimates, EPS
consensus revisions, and stock news come from StockEasy through
`.agents/skills/analyze-stock/scripts/fetch_stock_info.py`, which calls the
`stockeasy.intellio.kr/stockdata/api/v1/**` JSON endpoints (`stock-search`,
`stock-info/info-tab`, `news/by-stock-code`, `securities-reports`) and prints a
compact summary — the raw `info-tab` payload is ~128 KB because it embeds three
years of chart data. **All of them except `stock-search` now require a login**
(as of 2026-08 `info-tab` and `news/by-stock-code`, previously open, return HTTP
401 without a session), so the script reads the `STOCKEASY_COOKIE` variable (a
browser Cookie header, environment first and then the repository-root `.env`)
once and sends it verbatim on every call. Without it the script cannot resolve
quote, multiples, consensus, or news at all and exits 1 naming the cookie as the
cause; the report-summary section alone stays non-blocking. Keep the real value
in the gitignored `.env`, never in a tracked file, and never print it. To refresh
it, copy the `cookie:` request header from a logged-in `securities-reports` call
in Chrome DevTools' Network tab and replace the `.env` line, quoted, on one line.

The `[컨센 요약]` line aggregates **the latest report per broker**, not every row
in `target_price_history`. One broker publishing six times a year would otherwise
count six times, and targets cut since publication would drag the average toward
stale highs — the blend in step 9 needs today's consensus. The all-history simple
average is still printed on the following `↳` line, labelled as not being the
blend input.

Broker consensus is an input to the target price, not the verdict, and the target
price is always a **range, never a single number** — a consensus average carries
the centre but discards the dispersion, so twelve brokers clustered in a narrow
band and twelve brokers three-way split collapse to the same figure. The skill
therefore blends consensus and its own bull/base/bear scenarios at three points
in parallel — low (consensus minimum × bear), centre (consensus average ×
probability-weighted), high (consensus maximum × bull) — under one dynamic weight
that moves with consensus quality (target dispersion first, then broker coverage
and report recency, then EPS revision direction). Range width and the two
dispersion ratios are reported alongside the targets and drive the confidence
grade. The verdict is tied to `context/my_rules.md`: the centre against the
50~100% expectation of 「기본 원칙 2」 including its 30% capped-downside proviso,
and the low point against the -15% stop of 「매매규칙 6」, which together set the
effective stop width used for the reward/risk ratio and the 「기본 원칙 4」 2%-rule
position cap. The calculation rules live in `analyze-stock/SKILL.md` step 9; the
output layout lives in `template/stock_analysis.md` §5.

The Telegram daily briefing also reads the user's live holdings from the Google
Sheets file `주식 포트폴리오` (sheet `포트폴리오`) through the Google Drive MCP
connector, and stores a parsed snapshot in `output/portfolio/<yyyy-mm-dd>.md`.
`output/` is gitignored, so portfolio data never enters the repository.

Canonical project skills live in `.agents/skills/`. Each `.claude/skills/<name>`
is a symlink to the canonical directory — edit the canonical files only. Every
skill needs `SKILL.md` with `name`/`description` frontmatter and
`agents/openai.yaml` with `display_name`, `short_description`, and its `$<name>`
invocation. `tests/test_agent_configuration.py` enforces all of that and fails on
any credential or `/Users/...` path committed under `.agents/skills/`.

## Build and Development

Use `uv` for local development.

- `uv sync`: install runtime and dev dependencies.
- `uv run invagent --help`: inspect the CLI surface.
- `uv run pytest -q`: run the full test suite.
- `uv run pytest tests/test_cli.py -q`: run a focused test file.
- `uv run invagent fetch-messages --days 1`: manually verify message fetching.

Analyst report PDFs are read straight through the agent's file-read tool, which
renders the pages — no local PDF toolchain needed. Past 10 pages the read needs an
explicit page range (`analyze-stock/SKILL.md` step 4).

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
stock tracking. Prefer small unit tests with mocks over live network calls. Name
test modules `tests/test_<area>.py` and functions `test_<behavior>()`.

## Git and Security

Use short imperative commit subjects focused on one logical change. Pull requests
should summarize behavior, configuration or output-path changes, related issues,
and user-facing CLI examples where applicable.

Preserve unrelated work in a dirty worktree. Do not commit `.env`, API credentials,
Telegram sessions, generated outputs, or agent-local settings. Treat `context/`
as local working data unless it is intentionally curated for the repository;
`context/my_rules.md` and `context/interested_stocks.md` are curated and tracked.
