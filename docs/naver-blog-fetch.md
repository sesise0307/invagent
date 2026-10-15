# Reading a Naver Blog post

For a direct request to read a public Naver Blog post, run the repository's
`LinkExtractor.fetch_content` from the repository root. It already resolves the
post frame to its body document and extracts the article paragraphs. Reuse it
rather than adding a separate network client.

The web tool and local HTTP requests are different access paths. On 2026-10-15,
the web tool returned `is not accessible via this tool` for the example below,
while local HTTP returned 200 and `LinkExtractor` extracted 3,203 characters.
The original URL returned only a 2,834-byte iframe shell. This establishes a
working local route; it does not establish why the web service refused the URL
or which tool Claude Code used.

```bash
.venv/bin/python - 'https://blog.naver.com/gaunyu/224442622904' <<'PY'
import asyncio
import sys
from pathlib import Path

from invagent.telegram.link_extractor import (
    LinkExtractor, LinkFailure, naver_post_id,
)

url = sys.argv[1]
post = naver_post_id(url)
if post is None:
    raise SystemExit("Expected a Naver Blog /blogId/logNo post URL")
body = asyncio.run(LinkExtractor().fetch_content(url))
if not isinstance(body, str) or isinstance(body, LinkFailure) or not body.strip():
    raise SystemExit(f"Post fetch failed: {body}")
path = Path('output/link-fetch') / f'{post[0]}_{post[1]}.md'
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(f'출처: {url}\n\n{body}\n', encoding='utf-8')
print(body)
print(f'\nSaved: {path} ({len(body)} characters)')
PY
```

Use the installed `.venv` directly when available. This avoids `uv` cache
permission errors unrelated to Naver access. If no environment exists, follow
the repository's normal environment setup.

Read the resulting file and summarize the author's claims with attribution.
Check that it contains article paragraphs rather than navigation, a login page,
or just a title. The extractor's `MAX_CONTENT_CHARS` limit still applies; a final
`...` may indicate truncation. Images and attachments are not transcribed by
this text extraction. Report those limits when relevant to the request.

If the local route fails, identify its actual failure (HTTP status, DNS,
timeout, permissions, or missing dependencies). A web-tool failure alone is
insufficient evidence that Codex cannot read Naver Blog. For a textless post,
use an available browser according to its skill instructions to inspect the
rendered article.
