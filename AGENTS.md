# Repository Guidelines

This repository contains a personal investment-decision support package. Use the
project skills for investment advice, single-stock analysis, price-stage
judgement, Telegram briefings, monthly Notion reviews, and OpenDART research
instead of recreating those workflows in a prompt.

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
user's personal risk-management rules, `context/interested_stocks.md` holds the
tracked holdings list, and `context/ticker_overrides.md` pins stock names to
tickers the StockEasy search cannot resolve. Skills read `context/my_rules.md`
directly, so keep the rule numbering stable.

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
the same stock already exists, the skill inherits it instead of starting over.
It reads the prior report, collects only what is newer than that report's date,
renames that existing file to today's target path, and then edits the renamed
file in place. Keep one rolling report per stock; do not create or retain a
separate dated snapshot. The report adds dated update blocks only where facts or
judgments changed. Existing analysis must not be silently shortened or replaced;
corrections keep the old claim and mark it superseded with evidence. The current
investment call may be rewritten, but the prior call and its reasoning remain
visible inside the rolling report. Because `output/` is gitignored, resolve the
exact source and target before renaming and never overwrite an existing target.

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

The `stage-analysis` skill judges which of the four price-maturity stages a stock
sits in, following DB Securities' 2026-08-25 「Stage Analysis 마스터하기」 integrated
version — Weinstein's price/150-day-moving-average axis plus Minervini's operating
-profit-growth axis. `.agents/skills/stage-analysis/scripts/stage_scan.py` fixes the
stage **deterministically**; the model interprets boundaries and maps the verdict to
`context/my_rules.md`, it does not re-grade the number. Daily OHLCV comes from Naver
Finance's open `api.finance.naver.com/siseJson.naver` endpoint (no auth, response is
a single-quoted literal rather than strict JSON), and the quarterly operating-profit
series comes from the same StockEasy `info-tab` payload described above, so a missing
`STOCKEASY_COOKIE` degrades the run to a price-only verdict instead of failing it.
Ticker resolution, cookie loading, and financial-row selection are imported from
`analyze-stock/scripts/fetch_stock_info.py` rather than reimplemented. The judgement
thresholds (150-day line, ±1.5%/20-day flat band, 80/20% position ratio, 10-day
pivot radius, 60-day box, 250-day cycle window) are this skill's own operating
choices, not the report's — they live as constants at the top of the script and are
documented with their rationale in `stage-analysis/SKILL.md`; change both together.
The optional `--project` flag answers "when could this reach stage 2" arithmetically
rather than by guesswork: the closes about to roll out of the 150-day average are already
fixed, so holding the price constant determines the average's future path, and the price
needed to cross within N (< 150) trading days has the closed form
`(MA - S/150) / (1 - N/150)`. It is a calculation under a stated price assumption, not a
forecast, and the skill requires saying so alongside any date it produces.
`analyze-stock` step 6 runs the same script and quotes its verdict verbatim in §9.

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

The `summarize-telegram` skill owns everything under `output/telegram-daily/`.
`uv run invagent fetch-messages` writes the raw export to
`raw/<yyyy-mm-dd>_raw.md`; the finished briefing goes to
`<yyyy-mm>/<yyyy-mm-dd>.md`, the rolling cross-day index to
`monthly_context.md`, and the full text of each running theme to
`themes/<slug>.md`, with sub-bullets older than 30 days rolled off to
`themes/archive/`. The index and theme files are the accumulated memory that
`analyze-stock` searches, so they are appended to and rolled off, never rewritten
from scratch. At the end of a run the skill deletes every raw file except today's
(`raw/` only, `-maxdepth 1`); nothing outside `raw/` is ever deleted. Market
indices come from `.agents/skills/summarize-telegram/scripts/fetch_market_signals.py`,
which calls the same StockEasy `stockdata/api/v1` host as `fetch_stock_info.py`.

The Telegram daily briefing also reads the user's live holdings from the Google
Sheets file `주식 포트폴리오` (sheet `포트폴리오`) through the Google Drive MCP
connector, parses it with
`.agents/skills/summarize-telegram/scripts/extract_portfolio.py`, and stores the
snapshot in `output/portfolio/<yyyy-mm-dd>.md`. `analyze-stock` and `advice` read
the latest snapshot instead of re-fetching the sheet. `output/` is gitignored, so
portfolio data never enters the repository.

`extract_portfolio.py` grades holdings on the **average-cost** axis only, so a
position that ran up and then rolled over stays silent while it is still in
profit. `.agents/skills/summarize-telegram/scripts/peak_drawdown.py` adds the
**peak** axis: it reads the snapshot that script just wrote, computes each
holding's drawdown from its highest close over the last 250 trading days, and
flags the -10 / -15 / -20 / -30% bands that map to 「매매규칙 3·15」. The same run
derives the account MDD of 「기본 원칙 13」 from the balance line of every past
snapshot in `output/portfolio/`, noting that this peak covers only the snapshot
window and is not adjusted for deposits or withdrawals. `--append` writes the
result back into the snapshot, replacing its own section so reruns stay
idempotent. Daily bars come from the same unauthenticated Naver `siseJson`
endpoint as `stage-analysis`, reusing its `fetch_bars`, and ticker resolution
reuses `analyze-stock`'s `resolve_stock` — neither is reimplemented. The peak
window and the band list are this skill's own operating choices and live as
constants at the top of the script; the band-to-rule mapping is documented in
`summarize-telegram/SKILL.md` step 1-3-1, and the two must change together.
Because the -15% band is measured from the peak while 「매매규칙 6」's -15% is
measured from average cost, the skill is required to keep the two axes apart.
Names the StockEasy search cannot resolve (a new listing, or a sheet label that
differs from the official name) are pinned in the tracked
`context/ticker_overrides.md` as `종목명 = <6-digit code>` lines, which the script
consults before the API.

Canonical project skills live in `.agents/skills/`: `advice`, `analyze-stock`,
`monthly-investment-review`, `opendart`, `stage-analysis`, and
`summarize-telegram`. Each `.claude/skills/<name>` is a symlink to the canonical
directory — edit the canonical files only. Client-side setup that cannot live in
the repository (Codex plugins, the local OpenDART MCP server, Notion) is
documented in `docs/agent-setup.md`; keep its skill list in sync when adding a
skill. Every skill needs `SKILL.md` with `name`/`description` frontmatter and
`agents/openai.yaml` with `display_name`, `short_description`, and its `$<name>`
invocation. `tests/test_agent_configuration.py` enforces all of that and fails on
any credential or `/Users/...` path committed under `.agents/skills/`.

## Build and Development

Use `uv` for local development.

- `uv sync`: install runtime and dev dependencies.
- `uv run invagent --help`: inspect the CLI surface.
- `uv run pytest -q`: run the full test suite.
- `uv run pytest tests/test_cli.py -q`: run a focused test file.
- `uv run pytest tests/test_agent_configuration.py -q`: check skill wiring,
  skill-script behavior, and the credential/absolute-path guard after editing
  anything under `.agents/skills/` or `template/`.
- `uv run invagent fetch-messages --days 1`: manually verify message fetching.

Analyst report PDFs are read straight through the agent's file-read tool, which
renders the pages — no local PDF toolchain needed. Past 10 pages the read needs an
explicit page range (`analyze-stock/SKILL.md` step 4).

Copy `.env.example` to `.env` for local configuration. Telegram commands require
`TELEGRAM_API_ID` and `TELEGRAM_API_HASH`; `TELEGRAM_SESSION_PATH`,
`INVAGENT_OUTPUT_DIR`, and `INVAGENT_DEFAULT_CHANNELS` override the defaults in
`src/invagent/core/config.py`. Skills read `STOCKEASY_COOKIE` (StockEasy session)
and `INVAGENT_REPORT_ARCHIVE` (analyst-PDF root) from the same file. OpenDART
agent access requires private per-client MCP configuration; never put its
credential in repository files.

## Coding and Testing Style

Target Python 3.12+. Use 4-space indentation, type hints on public functions, and
concise docstrings for modules, classes, and non-trivial helpers. Use `snake_case`
for functions, variables, and modules, and `PascalCase` for classes. Match existing
Click patterns in `src/invagent/cli.py`. No formatter or linter is configured, so
keep imports and surrounding style consistent.

`src/invagent/telegram/link_extractor.py` fetches URLs that come out of saved
Telegram messages, which is untrusted input, so it is the one place in the
package exposed to SSRF. It allows only `http`/`https`, resolves the host and
rejects every non-global address (loopback, private, link-local — including the
`169.254.169.254` metadata address), follows redirects manually so each hop is
re-validated (`MAX_REDIRECTS`), keeps trafilatura from following redirects of its
own, and caps both response size (`MAX_RESPONSE_BYTES`) and per-URL wall time
(`hard_timeout`). Keep those checks when changing the module, and add cases to
`tests/test_telegram_link_extractor.py` for any new fetch path.

Tests use `pytest` and `pytest-asyncio`. Add or update tests for every behavior
change, especially CLI flows, configuration parsing, Telegram integrations, and
stock tracking. Prefer small unit tests with mocks over live network calls. Name
test modules `tests/test_<area>.py` and functions `test_<behavior>()`. Skill
behavior is covered by two files that call the skill scripts directly:
`tests/test_agent_configuration.py` (skill wiring plus the script behavior that
matters, against mocked HTTP payloads) and `tests/test_stage_analysis.py` (stage
grading, against synthetic price series). Keep their assertions about documented
thresholds and output rules in step with the SKILL.md files they mirror.

## Git and Security

Use short imperative commit subjects focused on one logical change. Pull requests
should summarize behavior, configuration or output-path changes, related issues,
and user-facing CLI examples where applicable.

Preserve unrelated work in a dirty worktree. Do not commit `.env`, API credentials,
Telegram sessions, generated outputs, or agent-local settings — `.gitignore` already
excludes `output/`, `.env*` (except `.env.example`), `docs/superpowers/`,
`.claude/settings.local.json`, and `.claude/RESUME.md`, so new working files belong
under a path that is already covered rather than in a fresh tracked directory.
Treat `context/` as local working data unless it is intentionally curated for the
repository;
`context/my_rules.md`, `context/interested_stocks.md`, and
`context/ticker_overrides.md` are curated and tracked.
