# Claude Code and Codex setup

Repository instructions and skills are shared. `AGENTS.md` is canonical;
`CLAUDE.md` imports it. Skills are maintained in `.agents/skills/`, with Claude
compatibility links in `.claude/skills/`.

## Caveman

Claude Code already uses the Caveman plugin. For Codex, add its marketplace and
install the plugin:

```bash
codex plugin marketplace add JuliusBrussee/caveman
codex plugin add caveman@caveman-repo
```

Start a new session and verify `$caveman:caveman full` in Codex and
`/caveman:caveman full` in Claude Code.

## Notion

Codex uses the installed `notion@openai-curated` plugin. Confirm that it is
enabled and authorized in a new session before running
`monthly-investment-review`. Claude Code continues to use its existing Notion
plugin. Test page updates against a disposable or explicitly approved page.

## OpenDART

The `opendart` MCP server is [MarcoYou/open-proxy-mcp](https://github.com/MarcoYou/open-proxy-mcp).
It ran as a hosted endpoint (`https://open-proxy-mcp.fly.dev/mcp`) until that host
started returning `HTTP 503` and then hanging outright (2026-08-20). The server now
runs locally, which removes the dependency on that host.

Keep the rotated OpenDART key out of tracked files; it is passed as a query
parameter on the endpoint URL, exactly as before.

### Local server

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git ~/.local/share/mcp/open-proxy-mcp
cd ~/.local/share/mcp/open-proxy-mcp && uv sync
```

Upstream removed the `stdio` transport, so the server only speaks
`streamable-http` and has to be running before a client can reach it. A user
LaunchAgent at `~/Library/LaunchAgents/kr.opm.open-proxy-mcp.plist` keeps it on
`127.0.0.1:8000` across logins:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/kr.opm.open-proxy-mcp.plist
```

It sets `FASTMCP_HOST=127.0.0.1` (loopback only), `OPM_MASTER_DB_PATH` and
`OPM_DOC_CACHE_DIR` under the clone so caches survive restarts, and
`OPM_CORPCODE_TIMEOUT=900`. Logs go to `~/Library/Logs/open-proxy-mcp.log`.

The timeout matters outside Korea: the server bootstraps from DART's
`corpCode.xml` (3.6 MB zip, 118,719 corps), which transfers at roughly 10 KB/s
from Europe — 361 s measured, against a hardcoded 120 s in
`open_proxy_mcp/dart/client.py`. That file carries a local patch making the
value read `OPM_CORPCODE_TIMEOUT`; re-apply it after pulling upstream. The
result is cached in sqlite with a 7-day TTL, so only the refresh pays the cost.

### Client registration

```bash
export OPENDART_API_KEY="your-key"
claude mcp add --transport http --scope local opendart \
  "http://127.0.0.1:8000/mcp?opendart=${OPENDART_API_KEY}"
codex mcp add opendart --url \
  "http://127.0.0.1:8000/mcp?opendart=${OPENDART_API_KEY}"
```

The Claude command stores local project configuration in `~/.claude.json`; the
Codex command stores it in `~/.codex/config.toml`. Do not add this endpoint to a
tracked `.mcp.json` or `.codex/config.toml`.

Start new sessions, inspect `/mcp`, and verify that `company` and
`financial_metrics` are available. Always call `company` before a
company-specific OpenDART query. If tools disappear, check that the server is
listening (`curl -s -o /dev/null -w '%{http_code}' -X POST
'http://127.0.0.1:8000/mcp?opendart=x' -H 'Content-Type: application/json' -H
'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'`
should print `200`).

## Verification

In both clients, list available skills and confirm these seven project skills:

- `advice`
- `analyze-stock`
- `market-data`
- `monthly-investment-review`
- `opendart`
- `stage-analysis`
- `summarize-telegram`

Run repository checks with:

```bash
uv run pytest tests/test_agent_configuration.py -q
uv run pytest -q
```
