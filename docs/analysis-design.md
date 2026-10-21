# Stock Analysis Design Notes

Design notes behind the `analyze-stock` and `stage-analysis` scripts, thresholds and report layout. The procedures themselves live in each SKILL.md; read this before changing a script, a threshold or the template.

## Report archive and inheritance

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
moves that existing file to today's target path with
`find_prior_report.py "<종목명>" --inherit --expected-sha256 <hash>` — the hash is the
`승계본 sha256` line of the script's plain run, taken when the report was read — and then
edits the moved file in place. `--inherit` writes the target through `atomic_write` and
only then removes the source; it refuses with exit 2 and leaves the source untouched when
the source changed since it was read or a different file already sits at the target, and
it is a no-op when the prior report is already at today's path. Keep one rolling report per stock; do not create or retain a
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
report as it is next analysed rather than in a commit, and the move goes through
`--inherit`, never a hand `mv`, so it resolves the exact source and target and never
overwrites an existing target.

## fetch_stock_info

Quote, multiples, per-broker target-price history, consensus estimates, EPS
consensus revisions, and stock news come from StockEasy through
`.agents/skills/analyze-stock/scripts/fetch_stock_info.py`, which takes the `info-tab` pair from
`invagent.datafeed.stockinfo`, then news, filings and broker reports from `invagent.datafeed.stockeasy`
one after another — StockEasy serves one request at a time, so a thread pool bought nothing — and prints a compact summary — the raw `info-tab`
payload is ~128 KB because it embeds three years of chart data. **All of those
endpoints except `stock-search` require a login** (as of 2026-08 `info-tab` and
`news/by-stock-code`, previously open, return HTTP 401 without a session), so the
cookie is read once from `STOCKEASY_COOKIE` (environment first, then the
repository-root `.env`) and sent verbatim on every call. Without it the script
cannot resolve quote, multiples, consensus, or news at all and exits 1 naming the
cookie as the cause; the report-summary section alone stays non-blocking. Keep the
real value in the gitignored `.env`, never in a tracked file, and never print it;
the refresh procedure lives in `market-data/SKILL.md`. Since the 2026-09-29 page
redesign the script also prints two things the new page exposes: a `[시간외·NXT]` line from
`info-tab`'s `after_hours_quote`/`nxt_quote`, measured against the regular-session price and labelled as
reference only. The regular-session price is the last bar of the `info-tab` chart, not `stock_info.cur_prc`:
after the close `cur_prc` follows after-hours trades (산일전기 2026-10-12: chart close 214,500원, `cur_prc` 213,500원
at 16:43), so `[시세]`, the after-hours gap, the 52-week position, the preferred discount, consensus upside and
`--json`'s `regular_close` all read the chart close, and `[시세]` names a differing `cur_prc` as 시간외 반영.
Inside the session (weekdays 09:00~15:40 KST) the chart is used only once it has today's bar.
The second addition is a `[공시]` list from `stock-info/analysis-tab` (`--disclosures N`, default 10), the company's
latest DART filings by receipt number. That list is a checklist for the OpenDART steps, never a
substitute for reading a filing.

## Stage verdict

The `stage-analysis` skill judges which of the four price-maturity stages a stock
sits in, following DB Securities' 2026-08-25 「Stage Analysis 마스터하기」 integrated
version — Weinstein's price/150-day-moving-average axis plus Minervini's operating
-profit-growth axis. `.agents/skills/stage-analysis/scripts/stage_scan.py` fixes the
stage **deterministically**; the model interprets boundaries and maps the verdict to
`context/my_rules.md`, it does not re-grade the number. Daily OHLCV comes from
`datafeed.daily` (StockEasy regular-session bars, Naver as a labelled fallback), and the quarterly operating-profit
series comes from the same StockEasy `info-tab` payload described above, so a missing
`STOCKEASY_COOKIE` degrades the run to a price-only verdict instead of failing it.
Ticker resolution, cookie loading, and financial-row selection come from
`invagent.datafeed` rather than being reimplemented, and ticker resolution goes
through `datafeed.tickers`, which reads `context/ticker_overrides.md` before the
search API — that override path used to exist in only one of the two skills that
resolve names, so the same name could resolve two ways.

The two axes never mix. `stage_scan` takes the operating-profit axis from the common
share and leaves the price axis (daily bars, the 150-day and 20-week lines, the swings)
on the preferred share's own quote, because the two trade at a discount that moves
independently; the display name stays the preferred one and the parent is named in the
source line. `fetch_stock_info` fetches `info-tab` **twice** for a preferred (both through
`stockinfo`, which makes the preferred split once for the whole script) — the
common share's for financials, consensus, target prices, grades, news and broker
reports, and the preferred's own for quote, 52-week band and flow — prints which axis
came from where plus the discount, and computes consensus upside against the **common
share's** price, since a target price set for the common share measured against the
preferred's would overstate upside by the discount (삼성전자우 2026-09-20: -23.8%).

The judgement
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

## Turn scan and entry paths

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
`turn`; unknown withholds and `falling` avoids. A `basing` stock can still open path C —
early accumulation before the turn, the proviso added to 「매매규칙 2」 on 2026-09-25 — when
turn_scan's `accumulation` price check passes (a base at least 30 sessions old after a 20%
or deeper decline, a box no more than 25% above the low, a close within 10% of it), the
latest confirmed quarter's operating profit beat the same quarter a year earlier
(`stage_scan`'s `growth.latest.increased`, which counts a narrowing loss as an increase),
and the value gate passes; `entry_policy.py` then caps the entry at half the first tranche,
with the stop at the base's closing low minus 3%. A backtest over eight watchlist names
(2015–2026, fundamentals taken point-in-time) set those numbers: base age barely separated
outcomes, while year-on-year operating-profit growth lifted the hit rate from 19% to 47%.
The remaining tranches follow the scan's
watch lines, a third of the target weight each: the second on a held higher low plus a
rising 60-day line, the third on a stage-2 verdict or a rising 20-week line. The
thresholds are this skill's operating choices, live as constants at the top of the
script, and are tabled with their rationale in `stage-analysis/SKILL.md`; a test compares
the two.

The model no longer copies those inputs into `entry_policy.py` by hand — that translation is
what picks path A, B or C, and it was untested. `analyze-stock` step 10 runs
`.agents/skills/analyze-stock/scripts/entry_decision.py <ticker> --valuation <json> --context <json>`,
which runs `stage_scan.py` and `turn_scan.py` **by path as subprocesses** with `--json` (never an
import, so the layout rule above holds; the re-run is absorbed by the response cache) and builds
`stage`, `turn` and `accumulation` from them, with `op_yoy_increased` `null` when earnings are
unknown. A child that exits non-zero or prints no JSON leaves its key `unknown` with the stderr
reason, which withholds. It recomputes `valuation_decision` on the step-9 input for
`valuation_grade` and the value gate (`downside_blocked` is `risk.downside <= 0`, `reward_risk` is
`risk.reward_risk`, the tranche fractions and trigger come from the context's `tranche_plan`),
passes the context's `account_pause`, `event`, `long_bull`, `earnings` and `instrument` through
verbatim — unknown or missing keys exit 2 — and prints `entry_policy.decide`'s result plus the
whole envelope as `inputs` for the report to quote. `entry_policy.py` stays as the module it
calls; its CLI is no longer the documented entry. `tests/test_entry_decision.py` fakes the
children at `run_child` with their real `--json` output.

## Consensus and target-price range

The `[컨센 요약]` line aggregates **the latest report per broker**, not every row
in `target_price_history`. One broker publishing six times a year would otherwise
count six times, and targets cut since publication would drag the average toward
stale highs — the blend in step 9 needs today's consensus. The all-history simple
average is still printed on the following `↳` line, labelled as not being the
blend input. That line is for people; step 9 takes the same aggregate from
`fetch_stock_info --json` instead — `consensus_input` in exactly
`valuation_decision.py`'s `consensus` shape and `consensus_envelopes` (the
`validate_inputs.py` envelopes for consensus low/average/high, with source and
KST fetch time filled in) — and passes both through unedited rather than copying
numbers off the printed line. `eps_revision` is left null with its reason in
`consensus_input_gaps`, because step 9 gives "persistent EPS downgrade" no
mechanical test; a null there is rejected by `valuation_decision.py`, so the model
cannot forget to fill it.

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
and the low point against the -15% final stop of 「매매규칙 6」 (-20% until 2026-10-21), which together set the
effective stop width used for the reward/risk ratio and the 「기본 원칙 4」 2%-rule
position cap. The calculation rules live in `analyze-stock/SKILL.md` step 9; the
output layout lives in `template/stock_analysis.md` §2.

## Reference-only checks: CAN SLIM and overhang

`analyze-stock` step 6 also runs `.agents/skills/analyze-stock/scripts/canslim_scan.py`, which
scores William O'Neil's CAN SLIM deterministically for §5 「📋 CAN SLIM 점검」. Like the overhang
check it is **reference only** (user decision, 2026-10-05): the score changes nothing in step 10, and a
test asserts `entry_policy.py` never reads it. C and A come from `info-tab` quarterly and annual EPS
(from the common share for a preferred), N from the 250-session **closing** high, S from 50-session
up/down volume (both on the split-adjusted judgement bars (`datafeed.daily.fetch_daily_bars`, 400 days, the
preferred's own bars for a preferred), not the raw `info-tab` chart, whose pre-split highs gave false N misses; fewer
than 250 sessions leaves N unknown and the bar label joins the `출처` line), L from `info-tab`'s `rs_data.rs` — StockEasy's own relative-strength score, already in
the payload, so no new endpoint was needed — I from 20-session institutional net buying via
`datafeed.naver.fetch_investor_trend` (Naver `m.stock.naver.com/api/stock/<code>/trend`, since StockEasy
exposes no per-stock institutional flow), and M from the StockEasy big picture of the stock's own market,
where `uptrend_under_pressure` is shown as a warning but not counted. Only ✅ count toward `N/7`; warnings
and gaps are listed separately. The model adds one thing — N needs a §4 catalyst as well as the price
condition. The sector-RS endpoint (`/rs/dashboard-data`) demands an app token and returns 403, so it
is not collected. The thresholds live as constants at the top of the script and in the table in
`analyze-stock/SKILL.md`; a test compares the two.

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
