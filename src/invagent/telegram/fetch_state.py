"""Remember where the last `fetch-messages` run stopped.

Telegram message ids only grow, so the newest id a run saw is an exact boundary
for the next run: a skipped day (a weekend) is fetched in full and nothing is
fetched twice. The state lives beside the raw exports as one small JSON file.
"""

import json
from pathlib import Path
from typing import Optional

FETCH_STATE_FILE = "daily-digest/.last_fetch.json"


def read_fetch_state(path: Path) -> Optional[dict]:
    """Return the saved state, or None when the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resume_after_id(state: Optional[dict], today: str) -> Optional[int]:
    """Pick the id after which today's run starts; None means use the time window."""
    if not state:
        return None
    # A same-day rerun (regenerating today's briefing) must refetch today's window,
    # or it would find nothing new and overwrite today's raw with an empty file.
    if state.get("date") == today:
        return state.get("after_id")
    return state.get("last_id")


def write_fetch_state(
    path: Path, today: str, after_id: Optional[int], messages: list[dict]
) -> None:
    """Record today's starting id and the newest id seen.

    A time-window run (first run, explicit ``--days``) has no starting id, so the
    one just before the oldest message fetched stands in — a same-day rerun then
    gets the same window back.
    """
    ids = [m["id"] for m in messages]
    if after_id is None and ids:
        after_id = min(ids) - 1
    last_id = max(ids) if ids else after_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": today, "after_id": after_id, "last_id": last_id}),
        encoding="utf-8",
    )
