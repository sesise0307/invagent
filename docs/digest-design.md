# Daily Digest Design Notes

Design notes behind the `daily-digest` pipeline: prep, archive layout, triggers, portfolio snapshot and peak drawdown. The procedure lives in `daily-digest/SKILL.md`; read this before changing one of its scripts or the archive layout. Image and link fetching are in `docs/telegram-fetch.md`.

## Prep and briefing archive

`uv run invagent daily-prep` (`src/invagent/daily_prep.py`) is the single entry point for the
briefing's independent prep steps — market signals, the DART filing match and, when today's snapshot
already exists, the peak-drawdown scan. They do not depend on each other, so they run
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
`<yyyy>/<mm>/<yyyy-mm-dd>.md`, the rolling cross-day index to
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

## Holding triggers and breakout watch

Holding-specific buy and sell triggers — add-on tranches, lifted buying freezes, user-confirmed stops,
trend-exit lines, remaining profit-taking, earnings-based invalidation — live in
`output/watchlist/보유_트리거.md`. `analyze-stock` step 11 and user-confirmed briefing decisions register
them, and `daily-digest` step 1-3-2 checks every entry each day against `turn_scan`/`stage_scan` closes
and reports sells before buys in the decision section; holdings without their own triggers are skipped. Non-held names that cleared the value gate but are blocked only on timing sit in
`output/watchlist/돌파선_감시.md`, which step 1-3-3 re-scans each day with `turn_scan` closes; a sector group
defined there (조선·엔진, user decision 2026-10-04) needs its sector confirmation as well as the breakout. The
file is gitignored working data; its price lines come from the report's target range as of a stated
date, so the briefing flags them for recalculation once they age. `context/my_rules.md`
「매매규칙 16」 caps the AI/semiconductor/substrate/power sector at 65% of evaluated weight; step 8
of `analyze-stock` reports the room left under that cap instead of treating sector overlap as a
reason to shrink a position.

## Portfolio snapshot and dated layout

The Telegram daily briefing also reads the user's live holdings from the Google
Sheets file `주식 포트폴리오` (sheet `포트폴리오`) through the connected Google Drive plugin's
formatted range reads (the collection procedure is owned by `market-data`), parses it with
`.agents/skills/daily-digest/scripts/extract_portfolio.py`, and stores the
snapshot in `output/portfolio/<yyyy>/<mm>/<yyyy-mm-dd>.md`. `analyze-stock` and `advice` read
the latest snapshot instead of re-fetching the sheet. `output/` is gitignored, so
portfolio data never enters the repository.

Snapshots and briefings share one dated layout, `<root>/<yyyy>/<mm>/<yyyy-mm-dd>.md`,
because one file a day in a single directory grew past what a listing can be read in.
`src/invagent/core/archive.py` owns it: `dated_path` builds the path, `dated_files`
lists every dated file under a root oldest first (skipping backups, CSV dumps and any
file whose date disagrees with its folders), and `archive_root` climbs from a dated
file back to its root — `peak_drawdown` reads the whole snapshot history and keeps
`.peak_drawdown_state.json` there, not in the month folder, and `find_mentions` walks
the briefings through `dated_files`. Both trees used to be flat — snapshots straight
under `portfolio/`, briefings in one folder per month — until 2026-10-04; since `output/` is gitignored
they were moved on disk and the paths quoted inside reports, briefings, the index and
theme files were rewritten in the same pass. The daily `raw/`, `media/` and `links/`
directories keep their flat names, because step 7 deletes everything but today's.
Weekly and monthly reviews follow the same folders under `output/reviews/<yyyy>/<mm>/`:
`<yyyy-mm-dd>_weekly.md` keyed by the week's Sunday (user decision, 2026-10-04) and `<yyyy-mm>_monthly.md` keyed by the
reviewed month, each with its `_merged.md` Notion body beside it.

## Peak drawdown and account MDD

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
idempotent. Daily bars come from the same `invagent.datafeed.daily.fetch_daily_bars` as
`stage-analysis`, and a holding measured on the Naver fallback is named in the section; ticker resolution
goes through `invagent.datafeed.tickers` — neither is reimplemented, and neither skill reaches
into the other's script directory any more. The peak
window and the band list are this skill's own operating choices and live as
constants at the top of the script; the band-to-rule mapping is documented in
`daily-digest/SKILL.md` step 1-3-1, and the two must change together.
Because the -15% band is measured from the peak while 「매매규칙 6」's -15% full exit is
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

## DART filings beyond Telegram

The user forwards DART filings to Telegram but misses many, so step 1-5 4) — which pulls the body of every
filing that reached the raw through the OpenDART MCP — saw only what was forwarded. Step 1-5 5)
(`daily-digest/scripts/dart_scan.py`, user decision 2026-10-26) lists every filing from the last briefing
day (inclusive, so an evening filing after that briefing is not lost; capped at 14 days) to today through
`invagent.datafeed.opendart`, keeps the companies in the latest snapshot, `context/interested_stocks.md` and the 「감시 대상」 table of
`output/watchlist/돌파선_감시.md` (added 2026-10-26; the sector-group table is not a watch list; preferred
shares matched on the common code), and marks a filing whose receipt number already appears in
today's raw or link files as forwarded. The list comes from one paginated `list.json` sweep instead of a
per-company MCP call because thirty-odd daily MCP lookups were the agent's to skip and could not be tested;
the MCP still serves every body. The notable/routine split mirrors the `quick-brief` notable-filing list.
