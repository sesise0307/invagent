# Repository Guidelines

This repository contains a personal investment-decision support package. Use the
project skills for investment advice, single-stock analysis, price-stage
judgement, the daily briefing, the quick session-only holdings brief, monthly Notion reviews, and OpenDART research
instead of recreating those workflows in a prompt. When the question is where a
piece of external data comes from — an endpoint, a credential, the response cache —
read the `market-data` skill rather than tracing the scripts.

At the start of each session, activate the installed `caveman` skill at `full`
intensity. In Codex use `$caveman:caveman full`; in Claude Code use
`/caveman:caveman full`. Keep code, commit messages, and PR descriptions in normal
professional language.

## Project Structure

For direct Naver Blog reading requests, use the existing `LinkExtractor` through
[docs/naver-blog-fetch.md](docs/naver-blog-fetch.md). A web-tool access error or
an iframe title is a retry signal; verify the local body fetch before reporting
that the post is unavailable.

Core package code lives under `src/invagent/`. Keep shared configuration and
Telegram client setup in `src/invagent/core/`, Telegram fetch logic in
`src/invagent/telegram/`, stock tracking in `src/invagent/tracking/`, and every
other external-data path in `src/invagent/datafeed/`. The CLI entry point is
`src/invagent/cli.py`. Tests mirror the package in `tests/`. Runtime outputs go to
`output/`, reusable templates to `template/`, reference context to `context/`, and
design notes to `docs/`.

`src/invagent/datafeed/` is the only place that talks to a market data source, and
`http.get_json` is the single request path — the response cache (`src/invagent/datafeed/cache.py`)
and the per-host rate limit sit inside it, so every caller gets both without asking. Its modules
are `stockeasy`, `fsc`, `naver`, `daily` (the judgement bars every price verdict reads), `cache`,
`ratelimit`, `env`, `series`, `tickers`, `stockinfo` (the `info-tab` pair), `opendart` (the
OpenDART filing list the briefing matches against holdings; bodies stay on the MCP) and `snapshot`.
**Add a new source here, never in a skill script.** A skill script fetches nothing itself; it calls
this package and spends its own lines on judgement and output. Nothing under `.agents/skills/` may
import another skill's scripts — a script that needs another skill's verdict runs it by path as a
subprocess with `--json`, as `entry_decision.py` does.

Which endpoint serves which value, the `STOCKEASY_COOKIE` refresh, cache and cool-down behaviour,
and the Telegram, Google Sheets and OpenDART paths are documented once, in the `market-data` skill;
other skills cite it rather than restating collection mechanics. Before changing code under
`datafeed/`, read `docs/datafeed.md` — module behaviour and the incidents behind each constant
(official KRX bars, split adjustment, the preferred-to-common mapping, the 15분 cache TTL, the
StockEasy budget and cool-down).

Curated reference files in `context/` are checked into the repository:
`context/my_rules.md` holds the user's personal risk-management rules, `context/interested_stocks.md` holds the
tracked holdings list, and `context/ticker_overrides.md` pins stock names to
tickers the StockEasy search cannot resolve. Skills read `context/my_rules.md`
directly, so keep the rule numbering stable.

Design notes for the skill pipelines sit in `docs/`, one file per area. Read the matching
one before changing that area's scripts, thresholds, output layout or archive paths — each records
the user decisions and incidents the constants came from, which the SKILL.md procedures do not repeat:

- `docs/analysis-design.md` — `analyze-stock` and `stage-analysis`: report filing and inheritance,
  `fetch_stock_info`, stage and turn scans, entry paths, consensus blend, CAN SLIM, overhang.
- `docs/digest-design.md` — `daily-digest`: `invagent daily-prep`, the briefing archive, holding
  triggers, the portfolio snapshot and dated layout, peak drawdown and account MDD.
- `docs/telegram-fetch.md` — image downloads, link bodies, per-source link rewrites, and the
  pending-marker contracts between `src/invagent/telegram/fetch.py` and `daily-digest/SKILL.md`.

Canonical project skills live in `.agents/skills/`: `advice`, `analyze-stock`,
`daily-digest`, `market-data`, `monthly-investment-review`, `opendart`,
`quick-brief`, `stage-analysis`, and `weekly-investment-review`. Each `.claude/skills/<name>` is a symlink to the canonical
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
- `uv run pytest tests/test_datafeed_http.py -q`: the collection layer, one file per
  module. No test reaches the network — the request path is faked at
  `datafeed.http.read_url`, so the whole suite runs with sockets blocked.
- `uv run pytest tests/test_agent_configuration.py -q`: check skill wiring,
  doc-sync, and the credential/absolute-path guard after editing anything under
  `.agents/skills/` or `template/`.
- `uv run pytest tests/test_peak_drawdown.py -q`: one skill script's behaviour —
  each script has its own `tests/test_<script>.py`.
- `uv run invagent fetch-messages --days 1`: manually verify message fetching.
- `uv run invagent daily-prep`: run the briefing's independent prep steps at once.

Skill scripts under `.agents/skills/**/scripts/` are invoked directly by path rather than
through a console entry point, so each one must own its own `argparse` and exit codes.
They run **inside the project environment** — `uv run python .agents/skills/.../x.py`, which
is the form every SKILL.md documents — because they import `invagent.datafeed` for anything
that touches the network or reads a shared file format. That import is the point: a script
that reimplements a fetch, a cookie read, or a snapshot parse locally is the defect this
layout exists to prevent. Beyond the package they are not restricted to the standard
library: a script may depend on third-party packages, declaring them inline with
`uv run --with <pkg>` in the command the SKILL.md documents. Keep the dependency list in
the script's docstring in step with the command in its SKILL.md.

Every script must be named by a SKILL.md with the command that runs it — a script no
document calls is one the model never runs, and `tests/test_agent_configuration.py` fails
on one.

Analyst report PDFs are read straight through the agent's file-read tool, which
renders the pages — no local PDF toolchain needed. Past 10 pages the read needs an
explicit page range (`analyze-stock/SKILL.md` step 4).

Copy `.env.example` to `.env` for local configuration. Telegram commands require
`TELEGRAM_API_ID` and `TELEGRAM_API_HASH`; `TELEGRAM_SESSION_PATH`,
`INVAGENT_OUTPUT_DIR`, and `INVAGENT_DEFAULT_CHANNELS` override the defaults in
`src/invagent/core/config.py`. `invagent.datafeed.env` reads `STOCKEASY_COOKIE`
(StockEasy session) and `DATA_GO_KR_SERVICE_KEY` (official daily bars) from the same file, and it reads any key — not just that one — so a
new credential needs no new parser. `INVAGENT_REPORT_ARCHIVE` (analyst-PDF root),
`INVAGENT_HTTP_CACHE` and `INVAGENT_HTTP_CACHE_TTL` are read there too, as is `OPENDART_API_KEY` for
the filing list. OpenDART agent access additionally requires private per-client MCP configuration;
never put its credential in repository files.

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

Per-source rewrites (Naver blog PostView, StreamDocs, DART frames, PDFs) and the link-body
files are described in `docs/telegram-fetch.md`; every rewritten address still goes through the same
checks.

Write every implementation — a new feature, a bug fix, a behavior change in a
skill script — through the installed `tdd` skill (mattpocock's, at
`~/.claude/skills/tdd`; invoke it with the Skill tool as `tdd`). Invoke it before
touching implementation code, not after, and follow its loop: agree the seams
under test first, then one failing test, one minimal implementation, repeat.
Refactoring is a separate pass, not part of the loop. The rule holds regardless
of how small the change looks; the package's own seam discipline (`datafeed.http.read_url`
for the network, `parse_holdings` for a snapshot) exists so this is cheap.

Tests use `pytest` and `pytest-asyncio`. Add or update tests for every behavior
change, especially CLI flows, configuration parsing, Telegram integrations, and
stock tracking. Prefer small unit tests with mocks over live network calls. Name
test modules `tests/test_<area>.py` and functions `test_<behavior>()`. The collection
layer is tested as an ordinary package — `tests/test_datafeed_*.py`, one file per module,
importing it normally and faking the network at `datafeed.http.read_url`, which is the
single seam every request passes through. Skill scripts are tested one file per script,
`tests/test_<script>.py` (`test_fetch_stock_info.py`, `test_entry_policy.py`,
`test_peak_drawdown.py`, `test_stage_analysis.py` for `stage_scan`, and so on), each loading
its script by path through `load_script(skill, name)` in `tests/conftest.py`; cache behaviour
that crosses several scripts sits in `tests/test_http_cache.py`, and a helper more than one
file needs goes in `tests/_helpers.py`. `tests/test_agent_configuration.py` holds only skill
wiring (SKILL.md frontmatter, `openai.yaml`, symlinks, every script named by a document), the
doc-sync checks that compare a script constant or rule text with SKILL.md, AGENTS.md or
`context/my_rules.md`, and the credential/absolute-path guard — a new behaviour test goes in
its script's file, not there. Keep assertions about documented thresholds and output rules in
step with the SKILL.md files they mirror. A test that needs to stop a network call patches
`invagent.datafeed`, never a script's own name for it.

## Git and Security

Use short imperative commit subjects focused on one logical change. Pull requests
should summarize behavior, configuration or output-path changes, related issues,
and user-facing CLI examples where applicable.

Preserve unrelated work in a dirty worktree. Do not commit `.env`, API credentials,
Telegram sessions, generated outputs, or agent-local settings — `.gitignore` already
excludes `output/`, `.env*` (except `.env.example`), `docs/superpowers/`,
`.claude/settings.local.json`, `.claude/RESUME.md`, and `.scratch/`, so new working files belong
under a path that is already covered rather than in a fresh tracked directory.
Treat `context/` as local working data unless it is intentionally curated for the
repository;
`context/my_rules.md`, `context/interested_stocks.md`, and
`context/ticker_overrides.md` are curated and tracked.

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature>/` (gitignored). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five triage roles, recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `GLOSSARY.md` plus `docs/adr/`, created lazily. See `docs/agents/domain.md`.
