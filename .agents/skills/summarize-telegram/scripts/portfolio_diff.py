#!/usr/bin/env python3
"""두 포트폴리오 스냅샷의 실제 보유 수량 차이를 구조화한다.

변화를 매매나 입출금으로 추정하지 않는다. 사용자가 제공한 이벤트만 별도 태그로 연결한다.
네트워크 접근 없음. 의존성: stdlib만 사용.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from invagent.datafeed.snapshot import parse_holdings
from invagent.datafeed.snapshot import to_float as number
from invagent.datafeed.tickers import TICKER_RE

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_TYPES = {"trade", "cash_flow", "corporate_event"}


def parse_snapshot(text: str) -> list[dict]:
    """스냅샷의 `## 보유` 표 → 비교용 보유 항목 리스트.

    표를 읽는 일은 수집 계층이 하고, 여기서는 수량 비교에 필요한 모양으로 옮기기만 한다.
    """
    rows = parse_holdings(text, require_rows=False)
    out: list[dict] = []
    for row in rows:
        name = row.get("종목") or row.get("종목명")
        quantity = number(row.get("보유") or row.get("보유수량") or row.get("수량") or "")
        if quantity is None:
            raise ValueError(f"{name}: 보유 수량을 파싱하지 못했다")
        out.append(
            {
                "name": name,
                "ticker": row.get("티커") or row.get("종목코드") or "",
                "account": row.get("계좌") or row.get("계좌명") or "(미지정)",
                "quantity": quantity,
                "average_cost": number(row.get("평단") or row.get("평균단가") or ""),
                "cost": number(row.get("매수금액") or row.get("매입금액") or ""),
            }
        )
    return out


def identity(row: dict) -> str:
    ticker = row.get("ticker", "").strip()
    return f"ticker:{ticker}" if TICKER_RE.fullmatch(ticker) else f"name:{row['name']}"


def group_positions(rows: list[dict]) -> dict[str, dict]:
    """동일 티커를 합산하되 계좌별 수량도 보존한다."""
    grouped: dict[str, dict] = {}
    for row in rows:
        key = identity(row)
        item = grouped.setdefault(
            key,
            {"key": key, "ticker": row.get("ticker") or None, "names": [], "quantity": 0.0, "accounts": {}},
        )
        if row["name"] not in item["names"]:
            item["names"].append(row["name"])
        item["quantity"] += row["quantity"]
        account = item["accounts"].setdefault(
            row["account"], {"quantity": 0.0, "cost": 0.0, "cost_known": True}
        )
        account["quantity"] += row["quantity"]
        if row["cost"] is None:
            account["cost_known"] = False
        else:
            account["cost"] += row["cost"]
    for item in grouped.values():
        item["names"].sort()
        for account in item["accounts"].values():
            if not account.pop("cost_known"):
                account["cost"] = None
    return grouped


def load_events(path: Path | None) -> list[dict]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("events", []) if isinstance(data, dict) else data
    if not isinstance(events, list):
        raise ValueError("events는 배열이어야 한다")
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
            raise ValueError("이벤트 type은 trade, cash_flow, corporate_event 중 하나여야 한다")
        if not event.get("tag"):
            raise ValueError("모든 이벤트에는 사용자가 정한 tag가 필요하다")
    return events


def matching_events(item: dict, events: list[dict]) -> list[dict]:
    names = set(item["names"])
    return [
        event for event in events
        if (event.get("ticker") and event.get("ticker") == item.get("ticker"))
        or (event.get("name") and event.get("name") in names)
    ]


def diff_portfolios(before_rows: list[dict], after_rows: list[dict], events: list[dict] | None = None) -> dict:
    before = group_positions(before_rows)
    after = group_positions(after_rows)
    explicit = events or []
    changes: list[dict] = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        old_quantity = old["quantity"] if old else 0.0
        new_quantity = new["quantity"] if new else 0.0
        if old_quantity == new_quantity and (old or {}).get("accounts") == (new or {}).get("accounts"):
            continue
        item = new or old
        if old_quantity == 0 and new_quantity != 0:
            kind = "entry"
        elif old_quantity != 0 and new_quantity == 0:
            kind = "exit"
        elif old_quantity != new_quantity:
            kind = "quantity_change"
        else:
            kind = "account_allocation_change"
        tags = matching_events(item, explicit)
        changes.append(
            {
                "kind": kind,
                "key": key,
                "ticker": item.get("ticker"),
                "names": item["names"],
                "before_quantity": old_quantity,
                "after_quantity": new_quantity,
                "quantity_delta": new_quantity - old_quantity,
                "before_accounts": old["accounts"] if old else {},
                "after_accounts": new["accounts"] if new else {},
                "classification": "user_tagged" if tags else "unclassified",
                "events": tags,
            }
        )
    return {"schema_version": 1, "changes": changes, "event_count": len(explicit)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--events", type=Path, help="사용자가 확인한 거래·입출금·기업 이벤트 JSON")
    parser.add_argument("--run-manifest", type=Path, help="수집 실행 manifest JSON")
    parser.add_argument("--json", action="store_true", help="구조화 JSON 출력")
    args = parser.parse_args(argv)
    try:
        result = diff_portfolios(
            parse_snapshot(args.before.read_text(encoding="utf-8")),
            parse_snapshot(args.after.read_text(encoding="utf-8")),
            load_events(args.events),
        )
        result["before"] = str(args.before)
        result["after"] = str(args.after)
        if args.run_manifest:
            manifest = json.loads(args.run_manifest.read_text(encoding="utf-8"))
            result["run"] = {
                key: manifest.get(key) for key in ("run_id", "workflow", "asof", "sources")
                if key in manifest
            }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: 포트폴리오 diff 실패 — {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if not result["changes"]:
            print("변화 없음")
        for change in result["changes"]:
            names = ", ".join(change["names"])
            print(
                f"{change['kind']}: {names} {change['before_quantity']:g} → "
                f"{change['after_quantity']:g} ({change['classification']})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
