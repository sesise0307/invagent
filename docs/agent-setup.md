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

Rotate the OpenDART key that was previously committed. Put the replacement in
your shell environment, then register the credential-bearing remote endpoint in
each client's private configuration:

```bash
export OPENDART_API_KEY="replacement-key"
claude mcp add --transport http --scope local opendart \
  "https://open-proxy-mcp.fly.dev/mcp?opendart=${OPENDART_API_KEY}"
codex mcp add opendart --url \
  "https://open-proxy-mcp.fly.dev/mcp?opendart=${OPENDART_API_KEY}"
```

The Claude command stores local project configuration in `~/.claude.json`; the
Codex command stores it in `~/.codex/config.toml`. Do not add this endpoint to a
tracked `.mcp.json` or `.codex/config.toml`.

Start new sessions, inspect `/mcp`, and verify that `company` and
`financial_metrics` are available. Always call `company` before a
company-specific OpenDART query.

## Verification

In both clients, list available skills and confirm these four project skills:

- `advice`
- `monthly-investment-review`
- `opendart`
- `summarize-telegram`

Run repository checks with:

```bash
uv run pytest tests/test_agent_configuration.py -q
uv run pytest -q
```
