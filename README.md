# Invagent

Personal investment-decision support for a single user. Two halves:

- a small Python CLI that pulls saved Telegram messages and tracks target prices;
- a set of agent skills (Claude Code / Codex) that turn those notes, local
  analyst PDFs, DART filings, and market data into dated markdown reports.

The CLI is the plumbing; the skills are where the daily work happens.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Telegram API credentials from https://my.telegram.org
- Claude Code or Codex, for the skills

## Setup

```bash
uv sync
cp .env.example .env
```

Required:

| Variable | Used by |
| --- | --- |
| `TELEGRAM_API_ID` | `invagent authenticate`, `fetch-messages` |
| `TELEGRAM_API_HASH` | same |

Optional:

| Variable | Default | Used by |
| --- | --- | --- |
| `TELEGRAM_SESSION_PATH` | `~/.telegram_session` | Telegram login session |
| `INVAGENT_OUTPUT_DIR` | `output` | every command that writes output |
| `INVAGENT_DEFAULT_CHANNELS` | see `core/config.py` | comma-separated channel list |
| `STOCKEASY_COOKIE` | — | `analyze-stock` / `stage-analysis`: quote, multiples, consensus, news |
| `INVAGENT_REPORT_ARCHIVE` | `~/1_Investment/리포트` | `analyze-stock`: local analyst PDF archive |
| `OPENDART_API_KEY` | — | only while registering the OpenDART MCP server (see `docs/agent-setup.md`) |

`.env` is gitignored. Never commit a real cookie or API key. `STOCKEASY_COOKIE`
is a whole browser `Cookie` request header copied from a logged-in tab; without
it every StockEasy endpoint except stock search returns HTTP 401.

## CLI

Authenticate once:

```bash
uv run invagent authenticate
```

Export saved messages from the last day:

```bash
uv run invagent fetch-messages --days 1        # link fetching is on by default
```

Linked pages are fetched and summarized into the export. Only public `http(s)`
addresses are fetched — private, loopback, and link-local targets are refused,
redirects are re-checked at every hop, and both response size and per-URL time
are capped.

Track a stock and target price:

```bash
uv run invagent add-stock AAPL --name Apple --sector Technology
uv run invagent set-target AAPL --price 250 --date 2026-04-04
uv run invagent show-stock AAPL
```

## Skills

Invoke in Claude Code as `/<name>`, in Codex as `$<name>`. Canonical sources are
in `.agents/skills/`; `.claude/skills/` holds symlinks to them.

| Skill | What it does |
| --- | --- |
| `analyze-stock` | Full single-stock workup — local analyst PDFs, DART report deltas, web news, past briefings, portfolio fit — into one rolling report per stock under `output/reports/종목/`. Target price is always a range. |
| `stage-analysis` | Decides which of the four price-maturity stages a stock is in, from the 150-day moving average and operating-profit growth. Deterministic: a script fixes the stage, the model only interprets it. |
| `advice` | Investor-perspective advice (value, trend, macro, second-level thinking) checked against the user's own rules. |
| `market-data` | Single reference for where every external data source comes from: endpoints, the StockEasy cookie and how to refresh it, the response cache, and ticker overrides. Collection only — the judgement stays with the skill that asked. |
| `summarize-telegram` | Daily briefing: fetch, classify by sector/theme, extract signals, roll into the accumulated theme archive. |
| `opendart` | Korean disclosure lookups (financials, ownership, dividends, filings) through the OpenDART MCP server. |
| `monthly-investment-review` | Reads the Notion investment journal for a month and writes back the retrospective. |

Skills read `context/my_rules.md` (personal risk-management rules) directly, so
its rule numbering is a stable interface — renumbering it changes skill output.

MCP servers, plugins, and other client-side setup the repository cannot hold:
`docs/agent-setup.md`.

## Layout

```text
src/invagent/        # CLI package: core/ (config, auth, client), telegram/, tracking/
.agents/skills/      # canonical skill definitions + their scripts
.claude/skills/      # symlinks to .agents/skills
template/            # report and briefing templates
context/             # my_rules.md, interested_stocks.md (tracked), stocks.json (local)
tests/               # pytest, mirrors the package + skill-script tests
docs/                # agent setup notes
output/              # everything generated (gitignored)
```

Generated output:

```text
output/
  telegram-daily/
    raw/YYYY-MM-DD_raw.md         # 원본 (오늘자만 유지, 브리핑 후 자동 정리)
    YYYY-MM/YYYY-MM-DD.md         # 일일 브리핑 (월별 서브 디렉토리)
    monthly_context.md            # 월간 누적 인덱스
    themes/<slug>.md              # 테마 전문 (30일 경과분은 themes/archive/로 롤오프)
  portfolio/YYYY-MM-DD.md         # 구글시트 보유 현황 스냅샷
  reports/
    종목/<초성>/<종목명>_YYYY-MM-DD.md   # 종목당 1개 롤링 보고서 (analyze-stock)
    산업/YYYY-MM-DD_<주제>.md
    기타/YYYY-MM-DD_<주제>.md
```

## Test

```bash
uv run pytest -q
uv run pytest tests/test_agent_configuration.py -q   # skill wiring + credential guard
```

## Contributing

Working agreements for this repository — structure, skill invariants, coding
style, security rules — live in `AGENTS.md` (imported by `CLAUDE.md`). Edit
`AGENTS.md`, not `CLAUDE.md`.
