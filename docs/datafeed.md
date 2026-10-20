# Collection Layer (`invagent.datafeed`)

Maintainer notes for `src/invagent/datafeed/`: how each module behaves and why. Which endpoint serves which value, the credentials and the refresh procedure live in the `market-data` skill; this file is for changing the code.

## Modules and judgement bars

`src/invagent/datafeed/` is the only place that talks to a market data source.
`http.get_json` is the single request path and every other module goes through it:
`stockeasy` (the `/stockdata/api/v1/**` stock endpoints and the `.../market/**`
ones, the `STOCKEASY_COOKIE` session, stock resolution, `gap_reason` — the one 401/cookie hint text every caller quotes), `fsc` (the Financial Services
Commission's official KRX daily prices on data.go.kr, keyed by `DATA_GO_KR_SERVICE_KEY`; an ETF, absent
from the stock-price service, is asked of the securities-product service `getETFPriceInfo_V2` instead —
without it ETFs fell through to StockEasy's three years even intraday and a KODEX 200 scan started a
StockEasy cool-down on 2026-10-16), `naver`
(daily OHLCV, plus the USD/KRW daily Hana Bank closing quote that the briefing's `[환율]` line reads —
StockEasy's market API carries no exchange rate), `daily` (the bars every price judgement reads — see below), `cache` (the shared response cache),
`ratelimit` (per-host request spacing, retry and cool-down — see below), `env` (repository root,
`output/`, `.env` values), `series` (`sma`), `tickers` (override-first ticker
resolution — then Naver's `ac.stock.naver.com` search, with StockEasy `stock-search` only when
Naver fails, so StockEasy's request budget goes to values only it serves — plus the
preferred-to-common mapping below), `stockinfo` (the `info-tab` pair — the share's own payload
for the 시세 축 and the 본주's for the 실적 축, each fetched on first read so a common share costs one
request and a fundamentals-only reader of a preferred never fetches the preferred's own; it collects
each failed fetch into `gaps` with the text from `stockeasy.gap_reason`, and owns the
「우선주 실적은 본주 …」 source line, the share's own `name`, and the
confirmed/estimate quarterly and yearly series (`quarterly`/`yearly`) and whole rows (`rows`) on the
primary statement — every script that reads `info-tab` (`stage_scan`, `canslim_scan`, `extract_earnings`,
`fetch_stock_info`, `market-data/fetch.py quote`) reads it through this module, so no script calls the
private `stockeasy._fs_rows` — only `stockinfo` does) and `snapshot` (portfolio
snapshot tables). `daily.fetch_daily_bars` takes the regular-session bars from `fsc` first — KRX's
official OHLCV, three years in one request, 10,000 requests a day — and, because that data is
published after 13:00 on the next business day, fills only the weekdays after its last bar from the
StockEasy `info-tab` `chart` — except today's bar asked for before 15:40 KST
(`cache.REGULAR_SESSION_SETTLED`), which is still an intraday price and comes from Naver, tagged
`naver_intraday` and labelled 「네이버 장중」, so an intraday scan after 13:00 makes no StockEasy call at all
(user decision, 2026-10-08; earlier missing days stay on StockEasy because Naver's past closes carry the
after-hours price) — so StockEasy is called only for a not-yet-published past bar or after 15:40 rather than
for every stock's three-year history (sequential scans were cut off around the twelfth to fourteenth
StockEasy request on 2026-10-05 and 10-06). A gap StockEasy cannot fill is filled from Naver and
named, with its dates, in the fallback note; without the key, or when `fsc` fails, the old order
applies — StockEasy's three years, then Naver. Without the key `daily` never calls `fsc` and says
nothing about it; a real `fsc` failure is not silent — it heads the label as
`공식 일봉 실패: <사유> → …`. The call returns a `Bars` value (`rows`, `error`, `fallback`,
`official_error`, plus `label`, `closes` and `closing_high(window)`), so no caller rebuilds the
source label or the closing high itself; `fallback` stays a separate field because `peak_drawdown`
groups holdings by it. Every script prints that label: Naver's `siseJson`
close carries the after-hours price (대덕전자 from 2026-09-14 on, e.g. 9/14 95,600 vs a
regular close of 97,000원, confirmed against the official bar on 2026-10-07), and rule triggers are
judged on the regular close (user decision, 2026-09-28). KRX's raw prices are not adjusted for corporate actions, so `fsc` back-adjusts every bar before a split, consolidation or rights ex-date by the ratio implied by `vs` (the change against KRX's adjusted base price) and drops halted days with no trade — unadjusted, LS ELECTRIC's 2026-04-13 5:1 split put its 150-day line at 334,474원 and graded a stage-2 stock as stage 4 (2026-10-07). The endpoint is the `_V2` one; the older
`/service/GetStockSecuritiesInfoService` path rejects new keys with `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`,
which reads as a key problem — `market-data/SKILL.md` records the setup. **Add a new source here,
never in a skill script.** A skill script fetches nothing itself; it calls this
package and spends its own lines on judgement and output. Nothing under
`.agents/skills/` may import another skill's scripts — that cross-directory
`sys.path` injection is gone and must not come back.

## Preferred shares

A **preferred share has no earnings of its own** — it is another class of the same
company's stock, so StockEasy `info-tab` called with a preferred ticker returns no
confirmed quarter and the run silently degraded to a price-only verdict (삼성전자우,
2026-09-20). `datafeed.tickers.common_code` maps it to the common share by replacing
the code's **last character with `0`**: KRX assigns that position to the share class —
`0` for common, `5`/`7`/`9` for the old preferreds, `K` onward for the ones issued
since 2013. The 164 common stocks that filed with DART in the week to 2026-09-21 all
ended in `0` without exception, and 삼성물산우B `02826K` → `028260` (삼성물산) was
cross-checked against that same data. The rule is a pure string operation, so detection
costs no request and cannot disagree with itself across skills; it also leaves the
new-format **common** codes (에임드바이오 `0009K0`) alone, since those end in `0`.
Note that `TICKER_RE` is still `^\d{6}$` and so rejects both those and the new
preferreds — widening it is separate work.

## Response cache

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
and every script that touches the network exposes `--no-cache`, `fetch_market_signals.py`
included. `invagent daily-prep` still reaches its steps with the environment variable rather than
the flag, so one switch lands on every step the same way whatever flags each script takes. Change the
constant and the minutes quoted here together; a test compares them. One exception keeps evening
and pre-dawn re-runs off the network: a quote URL (`info-tab`, Naver `siseJson`) stored while the
market is closed — weekdays after the 20:00 NXT close until 09:00 KST, and weekends — stays fresh
until the next weekday open, since nothing it carries can change before then. News, filings and
reports are not extended, because they keep arriving after the close. Entries are kept four days so
a Friday-evening quote survives to Monday. Judgement bars pull that window forward: `daily.fetch_daily_bars`
passes `closed_from=cache.REGULAR_SESSION_SETTLED` (15:40), since regular-session bars stop changing at the
15:30 close, so an afternoon re-run does not fetch `info-tab` again; other callers of the same URL keep the
20:00 rule because they print the after-hours quote. Change the constant and the time quoted here together.

## Rate limit, retry and cool-down

StockEasy cuts connections or refuses requests once too many arrive at once, and parallel
subagents analysing several stocks do not know about each other. `src/invagent/datafeed/ratelimit.py`
therefore sits inside `http.get_json`, per host and only on cache misses: request starts are spaced
at least `MIN_INTERVAL_SECONDS` (0.5) apart and at most `MAX_CONCURRENT_PER_HOST` (2) run at once,
both shared across processes through lock files under the gitignored `output/.cache/ratelimit/`.
`HOST_LIMITS` overrides those per host: StockEasy gets an 8-second interval, one request at a time and
a one-hour cool-down (raised from 600 seconds after a 2026-10-16 block outlasted it: cut off at 15:08 KST,
still cut off at 15:44), because on 2026-10-05 a strictly sequential 21-stock run was cut off at the
fourteenth stock and every retry after the 2-minute cool-down extended the block, and on 2026-10-06 a
sequential scan at the 3-second interval was still cut off around the twelfth request.
Cool-downs are counted separately for login (cookie-carrying) and anonymous requests — on 2026-10-16
StockEasy dropped every login `info-tab` call while still serving the anonymous market endpoints, so the
limit sits on the login session rather than the IP. Login requests to StockEasy also
carry a rolling budget (`login_budget` 70 per `login_window` six hours, retries included; raised from 60 on 2026-10-19 by user decision after a single stock analysis ran into the cap). It was a KST
daily budget for one afternoon — the same day the browser opened again, so the limit does not reset at
midnight; whether it is a timed lock-out or a rolling count is still unknown, and both are set off by a
burst. Once spent, requests fail fast with a 「요청 예산 소진」 reason and take the kept-response fallback.
Every request to a budgeted host, anonymous ones included, is appended to
`output/.cache/ratelimit/<host>.requests.jsonl` (`t`, `authed`, `ok`, `reason`; kept 48 hours,
`ratelimit.request_log`) so the next block can be read against the hours before it and the numbers tuned.
On 2026-10-16 the same `info-tab` URL answered 401 in 0.9 s without a cookie but dropped the connection
with the stored cookie and with a fresh cookie from the same account, while the browser worked; a cookie
from a different account was answered at once. The block sits on the account, not the session or the
client, so the first check after a login-only drop is a cookie-less request sent outside `http.get_json`
(so it cannot start a shared cool-down), and the remedy is to wait or switch `STOCKEASY_COOKIE` to another
account — never to disguise the client. `env.env_value` reads the process environment before `.env`, so a stale
`STOCKEASY_COOKIE` left in a shell or agent `env` setting silently hides a refreshed `.env` value; that
masked the first cookie refresh the same day. `stockinfo`'s
earnings axis accepts an `info-tab` response up to 12 hours old (`FUNDAMENTALS_TTL_SECONDS`) because
quarterly figures do not change within a day; the price axis keeps the 15-minute TTL.
A 429/502/503/504, a dropped connection or a timeout is retried up to `MAX_ATTEMPTS` (3) times with
exponential backoff plus jitter, honouring `Retry-After`; a 401 is never retried. When retries run
out the host enters a `COOLDOWN_SECONDS` (120, or its `HOST_LIMITS` value) cool-down during which every process fails fast
instead of extending the block — daily bars then take the Naver fallback. A name-resolution failure
(`[Errno 8]`) is retried but never starts a cool-down, because the request never reached the server: on
2026-10-14 the "DNS drops" were scripts run inside a network-denied Codex sandbox, and their cool-down files
blocked every other session for ten minutes. The cool-down file records why it started (`HTTP 429`,
`연결 끊김: …` or `시간 초과`, trimmed to `REASON_MAX_CHARS`) and the fail-fast message carries that reason, because on 2026-10-06
three unrelated hosts cooled down within eight seconds — a local network drop that looked like a StockEasy block until the
reasons could be told apart. Tune the spacing only on HTTP refusals; a file written before reasons existed reads as `사유 미기록`. A URL refused that way
falls back to its kept response, with an `[캐시 대체] … N분 전` line on stderr; a 401 never does,
because a stale body would hide an expired cookie. This throttles to the limit rather than evading
it. The real limit is unpublished, so tune the constants from the refusals the next time they
appear. `tests/conftest.py` points the lock files at a temporary directory and disables the waits
for the whole suite, and points `cache.CACHE_ROOT` at a fresh temporary directory for every test, so a faked
response never lands in the real `output/.cache/http` and a real cached response never masks a fake one.
