# Telegram Image and Link Fetching

Maintainer notes for `src/invagent/telegram/fetch.py` and `link_extractor.py`. The SSRF rules are summarised in `AGENTS.md`; the per-source rewrites and the marker contracts with `daily-digest/SKILL.md` are here. For reading one Naver post on request, use `docs/naver-blog-fetch.md`.

## Images

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

## Link extractor

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

Several other sources keep their body somewhere other than the link, and each used to reach the
briefing as a bare title or as garbage (2026-10-02: six of twenty links failed to summarise). They
are handled inside the hop loop of `_fetch_sync`, so every rewritten or followed address is
re-validated exactly like a redirect, and the hop limit (`MAX_REDIRECTS`, 5) counts HTTP and script
redirects together — a KIS report link takes five requests. Brokers' StreamDocs viewers (an
Angular shell carrying only a title) are rewritten to the document API that serves the PDF:
`/streamdocs/view/sd;streamdocsId=X` to `/streamdocs/v4/documents/X` (KIS) and the `mail` route
to `…/X/custom` (Samsung, which returns HTTP 500 without it). KIS `download_pdf.jsp` forwards with a
`document.location` script, which is followed only on `SCRIPT_REDIRECT_HOSTS` — following any
page's script would let the collector be steered anywhere. A DART `dsaf001/main.do` address is a
frame; the first `viewDoc(...)` call's arguments give the `report/viewer.do` body, which is MS949,
so HTML is now decoded by the response's declared charset rather than always as UTF-8. Pages that
need a login (`SKIPPED_PAGES`, the awakeplus `/board/` paths) are not requested at all and return
`[건너뜀: …]`. Trailing sentence punctuation is stripped from extracted URLs (`vo.la/X.` was a 404).

A PDF body is never decoded: `fetch_content` returns a `PdfDocument` holding the raw bytes, and
`MessageFetcher` writes it as `<stem>.pdf` next to the text bodies with the same pending marker;
the summarising subagent reads its first pages with the agent's file-read tool, as analyst PDFs
are read, so there is still no local PDF toolchain. Detection is by the `%PDF` magic, since the
viewers serve `application/octet-stream`; PDFs get their own cap (`MAX_PDF_BYTES`, 20 MB) because
a truncated PDF does not open, and an oversized one returns a sentinel instead. `.pdf`, StreamDocs
and DART targets skip trafilatura, which would download them a second time. Per-URL wall time is
`timeout × HARD_TIMEOUT_MULTIPLIER` (9, so 90 s): a stalled connection is still cut by the 10 s
read timeout, so the longer ceiling only lets slow-but-progressing downloads finish — a 12.3 MB
KIS report took 35 s alone and failed at the old 30 s ceiling when fetched in parallel.

## Link bodies outside the raw export

Link bodies do not sit in the raw export: step 2 of `daily-digest` reads raw in full, so a day's
worth of 10,000-character bodies would fill the context the briefing is written in. When
`fetch-messages` runs with links, every successfully fetched body is written to
`output/daily-digest/links/<yyyy-mm-dd>/` (`Config.digest_link_dir`) — `<blogId>_<logNo>.md` for a
Naver post, `<host>_<first 10 hex of the URL's SHA-1>.md` for anything else — and the raw link
block keeps only the saved URL, a `파일:` path and the marker `[요약 대기]`; a failed fetch stays
inline as its bracketed sentinel. Failure is judged by type, not by the bracket: every sentinel the
extractor returns is a `LinkFailure` (a `str` subclass, so it still writes into raw as text),
because real bodies start with a bracket too — an awakeplus filing page begins
`[일진전기] 단일판매…` and used to be mistaken for a failure and left inline. Step 1-5 of the skill splits the pending files into batches of
eight, one subagent per batch in parallel, and each subagent writes only a sidecar
`<name>.summary.md` of at most 1,000 characters. The subagents leave the raw export alone because
parallel edits to one file lose each other's writes;
`.agents/skills/daily-digest/scripts/apply_link_summaries.py` then swaps every marker for its
sidecar in one pass and prints only counts, so the agent writing the briefing meets the summaries
in raw and never the bodies. The marker is a contract between `PENDING_LINK_SUMMARY_MARKER` in
`src/invagent/telegram/fetch.py` and the grep in step 1-5, asserted by a test exactly as the image
marker is.
