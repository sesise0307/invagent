# Repository Guidelines

This repository contains a personal investment-decision support package. Use the
project skills for investment advice, single-stock analysis, price-stage
judgement, the daily briefing, monthly Notion reviews, and OpenDART research
instead of recreating those workflows in a prompt. When the question is where a
piece of external data comes from — an endpoint, a credential, the response cache —
read the `market-data` skill rather than tracing the scripts.

At the start of each session, activate the installed `caveman` skill at `full`
intensity. In Codex use `$caveman:caveman full`; in Claude Code use
`/caveman:caveman full`. Keep code, commit messages, and PR descriptions in normal
professional language.

## Project Structure

Core package code lives under `src/invagent/`. Keep shared configuration and
Telegram client setup in `src/invagent/core/`, Telegram fetch logic in
`src/invagent/telegram/`, stock tracking in `src/invagent/tracking/`, and every
other external-data path in `src/invagent/datafeed/`. The CLI entry point is
`src/invagent/cli.py`. Tests mirror the package in `tests/`. Runtime outputs go to
`output/`, reusable templates to `template/`, reference context to `context/`, and
design notes to `docs/`.

`src/invagent/datafeed/` is the only place that talks to a market data source.
`http.get_json` is the single request path and every other module goes through it:
`stockeasy` (the `/stockdata/api/v1/**` stock endpoints and the `.../market/**`
ones, the `STOCKEASY_COOKIE` session, stock resolution, `fs_rows`), `naver`
(daily OHLCV), `cache` (the shared response cache), `env` (repository root,
`output/`, `.env` values), `series` (`sma`), `tickers` (override-first ticker
resolution plus the preferred-to-common mapping below) and `snapshot` (portfolio
snapshot tables). **Add a new source here,
never in a skill script.** A skill script fetches nothing itself; it calls this
package and spends its own lines on judgement and output. Nothing under
`.agents/skills/` may import another skill's scripts — that cross-directory
`sys.path` injection is gone and must not come back.

Curated reference files in `context/` are checked into the repository:
`context/my_rules.md` holds the user's personal risk-management rules, `context/interested_stocks.md` holds the
tracked holdings list, and `context/ticker_overrides.md` pins stock names to
tickers the StockEasy search cannot resolve. Skills read `context/my_rules.md`
directly, so keep the rule numbering stable.

The `analyze-stock` skill reads the local analyst-report archive at
`~/1_Investment/리포트/<초성>/<종목명>/` (PDF only, outside this repository). Override
the location with `INVAGENT_REPORT_ARCHIVE`; never hardcode an absolute path in
skill files. It also searches the accumulated briefing archive under
`output/daily-digest/` (index, theme files, daily briefings) through
`.agents/skills/analyze-stock/scripts/find_mentions.py`, which emits `path:line`
locators rather than content — the archive's index and theme lines run to
several kilobytes each and must be read from the original file, never truncated.
Reports are filed under `output/reports/` in three branches:
`종목/<초성>/<종목명>_<yyyy-mm-dd>.md` for single-stock analyses (the 초성 folder follows
the same rule as the PDF archive, via `find_reports.chosung_dir()`),
`산업/<yyyy-mm-dd>_<주제>.md` for sector or theme comparisons, and
`기타/<yyyy-mm-dd>_<주제>.md` for everything else. The target path is computed by
`.agents/skills/analyze-stock/scripts/find_prior_report.py`, not by hand, and
the section layout is owned by `template/stock_analysis.md`, which puts the
decision first — 결론, then 밸류에이션 · 목표주가 with the broker consensus folded in
as §2-A, then 포트폴리오 비교, 촉매, 투자 판단 and 리스크 — and the background (사업 구조,
사업보고서 델타, 아카이브, Evidence, 개정 이력) after it. A report still in the older
twelve-section order is re-laid out on its next inheritance run, because `output/`
is gitignored and cannot be migrated in a commit. When a report for
the same stock already exists, the skill inherits it instead of starting over.
It reads the prior report, collects only what is newer than that report's date,
renames that existing file to today's target path, and then edits the renamed
file in place. Keep one rolling report per stock; do not create or retain a
separate dated snapshot. The body of the report holds only the current state —
keeping superseded prose inline made every inheritance run pay to re-read older
versions of facts it was about to overwrite. Change tracking lives in §11 개정 이력
as one line per revision, `- {yyyy-mm-dd}: {정정 | 갱신 | 판단변경} — {요약}`, with the
evidence (rcept_no, report filename, URL plus as-of date) carried inside the line for
corrections and reversed calls. Those lines accumulate and are never rolled off,
because the body no longer holds the past: §11 is the only place the earlier
judgment survives, which is why step 2 requires reading it in full before writing a
new call. A prior report still carrying `### YYYY-MM-DD 업데이트` or
`### {날짜} 기존 분석 (보존)` blocks is migrated on its next inheritance run — each
block folds into its §11 line first, and only then leaves the body, so an interrupted
migration loses nothing. Because `output/` is gitignored, that migration happens per
report as it is next analysed rather than in a commit, and the rename must resolve the
exact source and target and never overwrite an existing target.

Every external data source is documented in one place — the `market-data` skill.
It is the single reference for which endpoint serves which value, how the
`STOCKEASY_COOKIE` session is refreshed, how the response cache behaves, and where
the Telegram, Google Sheets and OpenDART paths run. Other skills do not restate
collection mechanics; they cite it. Judgement stays with the skill that asked.

The collection code itself lives in the `invagent.datafeed` package, not in a
skill: `stockeasy` (both the `/stockdata/api/v1/**` stock endpoints and the
`.../market/**` ones, plus the cookie and stock resolution), `naver` (daily
OHLCV), `http` (the one request-plus-cache path), `cache`, `env`, `series`,
`tickers` and `snapshot`. Skill scripts import it normally, so no script reaches
into another skill's directory through `sys.path` any more.

Quote, multiples, per-broker target-price history, consensus estimates, EPS
consensus revisions, and stock news come from StockEasy through
`.agents/skills/analyze-stock/scripts/fetch_stock_info.py`, which fetches through
`invagent.datafeed.stockeasy` and prints a compact summary — the raw `info-tab`
payload is ~128 KB because it embeds three years of chart data. **All of those
endpoints except `stock-search` require a login** (as of 2026-08 `info-tab` and
`news/by-stock-code`, previously open, return HTTP 401 without a session), so the
cookie is read once from `STOCKEASY_COOKIE` (environment first, then the
repository-root `.env`) and sent verbatim on every call. Without it the script
cannot resolve quote, multiples, consensus, or news at all and exits 1 naming the
cookie as the cause; the report-summary section alone stays non-blocking. Keep the
real value in the gitignored `.env`, never in a tracked file, and never print it;
the refresh procedure lives in `market-data/SKILL.md`.

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
Ticker resolution, cookie loading, and financial-row selection come from
`invagent.datafeed` rather than being reimplemented, and ticker resolution goes
through `datafeed.tickers`, which reads `context/ticker_overrides.md` before the
search API — that override path used to exist in only one of the two skills that
resolve names, so the same name could resolve two ways.

A **preferred share has no earnings of its own** — it is another class of the same
company's stock, so StockEasy `info-tab` called with a preferred ticker returns no
confirmed quarter and the run silently degraded to a price-only verdict (삼성전자우,
2026-09-20). `datafeed.tickers.fundamentals_code` maps it to the common share's
ticker, and `stage_scan` uses that for the operating-profit axis only; the price axis
(daily bars, the 150-day and 20-week lines, the swings) stays on the preferred share's
own quote, because the two trade at a discount that moves independently. The display
name stays the preferred one even though `info-tab` now answers with the parent's.
`fetch_stock_info` applies the same mapping to the news endpoint alone, since articles
are written under the common share's name; its valuation and consensus still come from
the preferred ticker. Detection needs **both** axes to agree: the name ends in a
preferred suffix (`우`, `우B`, `2우B`) **and** the ticker's last digit is not `0`, the
second test being what keeps a common share whose name merely ends in 우 (미래에셋대우
006800) from being remapped. A sheet spelling the suffix differently (`삼성전자(우)`)
is not detected and leaves the verdict price-only, which is the safe failure. The
judgement
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
`analyze-stock` step 6 runs the same script and quotes its verdict verbatim in §5.

The stage verdict is too slow to time a value or neglected stock's bottom — 150-day
confirmation arrives months after the low, or leaves the stock in stage 4 the whole
way — so 「매매규칙 2」's "buy once the base is in and the price starts to lift" is judged
by `.agents/skills/stage-analysis/scripts/turn_scan.py` instead, on 20–60-day daily
windows and on closes only. It returns one of four states: `falling` (a fresh base low
within 15 sessions, a Wyckoff spring of up to 3% excepted — the base is the 120-day closing
low, or the low of the current decline once that decline is 20% or more off the window's
highest close, so a stock that rallied and then crashed is not measured from its
pre-rally low), `basing`,
`turning` (a higher short-term swing low, a close above the rebound high between the low
and that higher low, a rising 20-day line under the close, and a close above the VWAP
anchored at the low) or `extended` (+10% past the breakout line once a higher low has
fixed that line — below the line the stock is basing however far it is off the low — and
+35% off the low only for a V-shaped rebound with no higher low yet; measuring from the
low after the line is set would leave a V-shaped rebound no early window at all, or call
a stock still under its breakout "extended" and then "turning" once it broke out — user
decisions, 2026-09-12).
Breakout volume, up/down volume since the low, RSI divergence and a spring raise
confidence only; they never change the state. `analyze-stock` routes by stage: stage 2
keeps the trend-confirmed path A, while stages 1, 3 and 4 open path B only when the turn
is `turning` and the value gate passes — stage 4 included, since a stock that has turned
up is no longer falling (user decision, 2026-09-12). `entry_policy.py` takes the state as
`turn`; unknown withholds and `falling` avoids. The remaining tranches follow the scan's
watch lines, a third of the target weight each: the second on a held higher low plus a
rising 60-day line, the third on a stage-2 verdict or a rising 20-week line. The
thresholds are this skill's operating choices, live as constants at the top of the
script, and are tabled with their rationale in `stage-analysis/SKILL.md`; a test compares
the two.

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
and the low point against the -20% final stop of 「매매규칙 6」, which together set the
effective stop width used for the reward/risk ratio and the 「기본 원칙 4」 2%-rule
position cap. The calculation rules live in `analyze-stock/SKILL.md` step 9; the
output layout lives in `template/stock_analysis.md` §2.

`analyze-stock` step 5-1 is an overhang and share-supply check whose output is
reference only. It pulls convertibles, rights issues and
their refixing dates from OpenDART `dilutive_issuance`, large-holder and blockdeal
moves from `ownership_structure`, buybacks from `treasury_share`, and lockup
expiries from the StockEasy news pass, converts every block to a percentage of
shares outstanding, and puts the dated ones on the §4 catalyst calendar as well.
A funding method the company has left open ("internal reserves or external
financing") stays 미확정 rather than 없음 — straight debt is neutral, mezzanine
dilutes — and an insider's stock-option exercise is never promoted to the signal
strength of an open-market purchase. The summary line (해소 / 미해소 / 해당 없음)
is quoted once under the §5 verdict and changes nothing in step 10 — not the
buy / watch / avoid call, the confidence grade, the entry path, the value gate,
or the tranche size — because the user treats overhang as context rather than a
deciding factor. `entry_policy.py` therefore has no overhang gate, and a test
asserts that an unresolved or uncollected overhang leaves its output unchanged.
It used to gate entry (미해소 downgraded a buy call to the first tranche); the
check lives in `analyze-stock/SKILL.md` step 5-1; the output layout lives in
`template/stock_analysis.md` §4-A.

`uv run invagent daily-prep` (`src/invagent/daily_prep.py`) is the single entry point for the
briefing's independent prep steps — market signals and, when today's snapshot already exists,
the peak-drawdown scan. They do not depend on each other, so they run
concurrently and print as one block in a fixed order instead of costing a separate command each.
Every step stays non-blocking exactly as its SKILL.md section requires: a failed step leaves its
section with a reason and the command still exits 0. `--snapshot` runs the drawdown step only if
that file is already there, so a first run skips it (the snapshot is created later, in step 1-3)
and a same-day re-run picks it up. The per-step commands stay documented in the skill for
re-running one step alone, and each section remains the authority on how to read its output.

The `daily-digest` skill builds one briefing a day out of every daily input —
saved Telegram messages, market signals, the portfolio snapshot, peak drawdown
and account MDD — which is why it is not named
after Telegram. It owns everything under `output/daily-digest/`, which was
`output/telegram-daily/` until the skill was renamed. `output/` is gitignored, so
that directory moved on disk rather than in a commit: a checkout that predates the
rename still has the old name, and `find_mentions` will report an empty archive
until it is moved. `uv run invagent fetch-messages` writes the raw export to
`raw/<yyyy-mm-dd>_raw.md`; the finished briefing goes to
`<yyyy-mm>/<yyyy-mm-dd>.md`, the rolling cross-day index to
`monthly_context.md`, and the full text of each running theme to
`themes/<slug>.md`, with sub-bullets older than 30 days rolled off to
`themes/archive/`. The index and theme files are the accumulated memory that
`analyze-stock` searches, so they are appended to and rolled off, never rewritten
from scratch. At the end of a run the skill deletes every raw file except today's
and every dated media and link directory except today's (`raw/`, `media/` and `links/`
only, `-maxdepth 1`); nothing outside those three directories is ever deleted. Market
indices come from `.agents/skills/daily-digest/scripts/fetch_market_signals.py`,
which reads the StockEasy market endpoints through `invagent.datafeed.stockeasy`; the module owns
the rule verdicts (leverage rule 3, the drawdown ladder, the margin-call climax), not the fetch.

The same command downloads attached images to `media/<yyyy-mm-dd>/` and leaves
them unread, because reading them is not a fetch-time job. `fetch-messages` only
decides what is an image — photos and `image/*` documents, by whitelist, so a
link preview or a PDF is not mistaken for one — writes the file, and emits an
`이미지:` block into the raw export carrying the path and the literal marker
`[분석 대기]`. Step 1-4 of the skill then reads each image through the agent's
file-read tool, exactly as analyst-report PDFs are read, and replaces the marker
with the transcribed text and, for a chart, its axes, series, and turning points.
There is no local OCR toolchain and no new runtime dependency; a chart is
interpreted rather than transcribed, which a text-only OCR pass cannot do. The
marker string is a contract between `PENDING_IMAGE_MARKER` in
`src/invagent/telegram/fetch.py` and the grep in `daily-digest/SKILL.md`
step 1-4 — change both together, and a test asserts they match, because a
one-sided edit makes the skill find nothing and skip silently. That grep keeps
`-a`: raw exports written before link bodies moved out to `links/` (2026-09-10) contain NUL
bytes from binary link bodies, and plain `grep` treats such a file as binary and prints
nothing. An image that
cannot be downloaded or read never blocks the run — the fetcher records a
bracketed sentinel the way `LinkExtractor` does, and the briefing falls back to
caption and context marked `(이미지 미확인)`. A message carrying only an image
and no caption is now kept; it used to be dropped whole at the fetch loop's
empty-text check, which is why chart captures never reached a briefing.
`MAX_IMAGE_BYTES` and `MAX_IMAGES_PER_RUN` cap what one run can spend on disk and
on reading, and live as constants at the top of the fetch module. Each image download is
bounded by `IMAGE_DOWNLOAD_TIMEOUT_SECONDS` (30s) and tried `IMAGE_DOWNLOAD_ATTEMPTS` (2) times,
because a photo stored on another Telegram data centre makes telethon open a second connection
that can stall forever — on 2026-09-10 one zero-byte file held the whole run for twelve minutes.
An image that times out on every attempt leaves a `[이미지 저장 실패: 시간 초과 …]` sentinel, and
the rest of that run's images are skipped as `[이미지 건너뜀: 앞선 다운로드 시간 초과]` rather than
each waiting out its own timeout on what is most likely the same dead connection. Link bodies are
fetched for up to `MAX_CONCURRENT_LINK_MESSAGES` messages at once rather than one message at a
time: a single message can burn the extractor's `hard_timeout`, so a serial loop multiplies that
by the message count on a catch-up run. Results are written back into each message's own dict, so
completion order cannot reorder a briefing that is meant to read chronologically. Image downloads
stay serial on purpose — they share one MTProto connection, where concurrency buys little, and the
per-run budget is counted in message order, which a race would make non-deterministic.

The Telegram daily briefing also reads the user's live holdings from the Google
Sheets file `주식 포트폴리오` (sheet `포트폴리오`) through the Google Drive MCP
connector, parses it with
`.agents/skills/daily-digest/scripts/extract_portfolio.py`, and stores the
snapshot in `output/portfolio/<yyyy-mm-dd>.md`. `analyze-stock` and `advice` read
the latest snapshot instead of re-fetching the sheet. `output/` is gitignored, so
portfolio data never enters the repository.

`extract_portfolio.py` grades holdings on the **average-cost** axis only, so a
position that ran up and then rolled over stays silent while it is still in
profit. `.agents/skills/daily-digest/scripts/peak_drawdown.py` adds the
**peak** axis: it reads the snapshot that script just wrote, computes each
holding's drawdown from its highest close over the last 250 trading days, and
flags the -10 / -15 / -20 / -30% bands that map to 「매매규칙 3·15」. The same run
derives the account MDD of 「기본 원칙 13」 from the balance line of every past
snapshot in `output/portfolio/`, noting that this peak covers only the snapshot
window and is not adjusted for deposits or withdrawals. That rule's responses —
clearing leverage and holding 30% cash at -10%, a five-session buying freeze and
a rule-violation review at -15% — cannot be prepared after the band is already
hit, so the script prints the balance that triggers each un-hit band and the
distance left to it every day, and raises a `⚠️⚠️ 임박` warning once the account
comes within `ACCOUNT_MDD_WARN_MARGIN_PP` (2.0 percentage points) of the next
band; a warned day promotes 「기본 원칙 13」 to the briefing's rule reminder even
though nothing has triggered yet. The warning's action text lives in
`ACCOUNT_MDD_ACTION`, summarised from `context/my_rules.md`, and a test asserts
both against the rule file so the two cannot drift apart. Per-holding bands warn
the same way on both axes: a holding within `DRAWDOWN_WARN_MARGIN_PP` (2.0
percentage points, tuned separately from the account margin because the band
spacing differs) of its next band is listed under `⚠️ 임박` with the **price**
that trips it, and the table's alert column shows the approaching band only when
no band has actually triggered, so an approach never masks a live one. Both
margins compare on the drawdown rounded to the displayed decimal — -12.969%
prints as -13.0%, which reads as 2.0 points from -15%, and withholding the
warning over the unrounded 2.031 would contradict the table; this is the same
class of mismatch `BAND_EPS` exists to prevent. `--append` writes the
result back into the snapshot, replacing its own section so reruns stay
idempotent. Daily bars come from the same unauthenticated Naver `siseJson`
endpoint as `stage-analysis`, through `invagent.datafeed.naver.fetch_bars`, and ticker resolution
goes through `invagent.datafeed.tickers` — neither is reimplemented, and neither skill reaches
into the other's script directory any more. The peak
window and the band list are this skill's own operating choices and live as
constants at the top of the script; the band-to-rule mapping is documented in
`daily-digest/SKILL.md` step 1-3-1, and the two must change together.
Because the -15% band is measured from the peak while 「매매규칙 6」's -15% is
measured from average cost, the skill is required to keep the axes apart.
Names the StockEasy search cannot resolve (a new listing, or a sheet label that
differs from the official name) are pinned in the tracked
`context/ticker_overrides.md` as `종목명 = <6-digit code>` lines, which the script
consults before the API.

The same script reports a **second peak axis that needs no network**: for each
holding it takes the highest `현재가` ever recorded in a parseable snapshot under
`output/portfolio/` and measures today's drawdown from that, against the same
band list. The 250-day axis answers "how far has this stock fallen in the
market", including a peak formed before the position existed; this one answers
"how much has the position given back since I started recording it", which is
the axis a trailing stop actually sits on — on 2026-09-03 SK하이닉스 was -45.3%
on the market axis but only -8.4% on the record axis. Both axes are labelled
wherever they appear and are never summed under one rule number, because the
sheet's `현재가` is a collection-time quote rather than an official close and the
two therefore do not reconcile arithmetically. The scan reuses `parse_holdings`
per snapshot file and skips any file it cannot parse (an earlier hand-written
snapshot has a different table shape), so one odd file cannot take the axis
down. Its known limits — the peak only covers the snapshot window on disk, and a
position sold and later rebought is not distinguished from one held throughout —
are printed as a footnote in the section itself. Renaming the section title
requires adding the old title to `LEGACY_SECTION_TITLES` so `--append` strips it;
`output/` is gitignored, so already-written snapshots can only be migrated by the
code that rewrites them.

`src/invagent/datafeed/cache.py` is the shared HTTP response cache every network call goes
through —
`datafeed.http.get_json` is the single request path, so `stockeasy`, `naver` and every skill
script above get it without asking. It exists because one `analyze-stock` run pulls the ~128 KB
`info-tab` payload twice (once for the quote, once for the stage verdict) and a re-run of a
briefing repeats every call. **The TTL is short — 15분 by default — rather than
daily, because `info-tab` carries 현재가**: a day-scoped entry would hand a late-afternoon re-run
the morning's price. Entries live under the gitignored `output/.cache/http/`, keyed by URL plus
whether a cookie was sent — never by the cookie's value, which is written nowhere — so an
unauthenticated HTTP 401 body can never be replayed to an authenticated call. Only successful
responses are stored, so a transient 401 or timeout does not stick for the rest of the TTL.
`INVAGENT_HTTP_CACHE=0` disables it, `INVAGENT_HTTP_CACHE_TTL` overrides the window in seconds,
and every script that has an `argparse` exposes `--no-cache` — `fetch_market_signals.py` has
none, which is why `invagent daily-prep` reaches all three steps with the environment variable
rather than a flag. Change the
constant and the minutes quoted here together; a test compares them.

The `weekly-investment-review` skill scores one week of trades and writes the verdict
into the Notion journal's `## {dd}~{dd}(주말)` block. It grades each fill on three axes —
the stock report's execution lines, the same day's briefing stance under
`output/daily-digest/`, and whether last week's improvement items were actually carried
out — and then fills three required cells for the week ahead: new entries, position
sizing under the 2% rule, and the cash target. It reads the briefings section by section
rather than whole, because one day's file runs 20~40 KB and a week of them buries the
context the review is written in. Its Notion write is guarded, because
`notion-update-page`'s `replace_content` overwrites the **entire page body** even when a
selection is supplied: on 2026-09-20 a one-block edit erased that month's journal. The
skill therefore requires backing the body up under `output/notion-backup/`, merging
locally through `.agents/skills/weekly-investment-review/scripts/weekly_upsert.py`, and
passing the complete merged body as `new_str`. That script refuses — exit 2, no output
file — when the weekend heading is missing or when any `## ` heading would be lost, and a
test fixes both refusals.

Canonical project skills live in `.agents/skills/`: `advice`, `analyze-stock`,
`daily-digest`, `market-data`, `monthly-investment-review`, `opendart`,
`stage-analysis`, and `weekly-investment-review`. Each `.claude/skills/<name>` is a symlink to the canonical
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
  skill-script behavior, and the credential/absolute-path guard after editing
  anything under `.agents/skills/` or `template/`.
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
(StockEasy session) from the same file, and it reads any key — not just that one — so a
new credential needs no new parser. `INVAGENT_REPORT_ARCHIVE` (analyst-PDF root),
`INVAGENT_HTTP_CACHE` and `INVAGENT_HTTP_CACHE_TTL` are read there too. OpenDART
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

Naver blog posts need one extra step. A post page (`blog.naver.com/<id>/<logNo>` or the
`m.blog.naver.com` form) is only a frame around a `PostView.naver` document, so fetching it as
given returns the frame's title and nothing else — which is why saved blog links used to reach the
briefing as a bare title. Those two URL shapes are rewritten to
`blog.naver.com/PostView.naver?blogId=…&logNo=…` **before** `assert_public_url` runs, so the
rewritten address goes through the same SSRF checks as any other; any other Naver URL (a blog
home, Naver News) is fetched as given. From the PostView document only the `.se-main-container`
body is kept, because trafilatura otherwise mixes the page's layer notices and embedded JSON into
the text. Every link body, posts included, is kept up to `MAX_CONTENT_CHARS` (10,000)
characters — conclusions tend to come last, and the body lands in a file rather than the raw
export.

Link bodies do not sit in the raw export: step 2 of `daily-digest` reads raw in full, so a day's
worth of 10,000-character bodies would fill the context the briefing is written in. When
`fetch-messages` runs with links, every successfully fetched body is written to
`output/daily-digest/links/<yyyy-mm-dd>/` (`Config.digest_link_dir`) — `<blogId>_<logNo>.md` for a
Naver post, `<host>_<first 10 hex of the URL's SHA-1>.md` for anything else — and the raw link
block keeps only the saved URL, a `파일:` path and the marker `[요약 대기]`; a failed fetch stays
inline as its bracketed sentinel. Step 1-5 of the skill splits the pending files into batches of
eight, one subagent per batch in parallel, and each subagent writes only a sidecar
`<name>.summary.md` of at most 1,000 characters. The subagents leave the raw export alone because
parallel edits to one file lose each other's writes;
`.agents/skills/daily-digest/scripts/apply_link_summaries.py` then swaps every marker for its
sidecar in one pass and prints only counts, so the agent writing the briefing meets the summaries
in raw and never the bodies. The marker is a contract between `PENDING_LINK_SUMMARY_MARKER` in
`src/invagent/telegram/fetch.py` and the grep in step 1-5, asserted by a test exactly as the image
marker is.

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
single seam every request passes through. Skill behavior is covered by two files that load
the skill scripts by path: `tests/test_agent_configuration.py` (skill wiring plus the script
behavior that matters) and `tests/test_stage_analysis.py` (stage grading, against synthetic
price series). Keep their assertions about documented thresholds and output rules in step
with the SKILL.md files they mirror. A test that needs to stop a network call patches
`invagent.datafeed`, never a script's own name for it.

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
